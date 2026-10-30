"""
extractor.py — Turns an invoice file (PDF or image) into validated invoice data.

Two providers, picked with LLM_PROVIDER in .env:
  - gemini (default, free tier): the whole PDF is sent in one call, with the invoice
    schema as structured output.
  - groq: each PDF page is rendered to a JPEG and sent to Llama 4 Scout vision.

Whatever the provider, the answer is validated against schema.ExtractionResult,
and every invoice goes through the totals checks in checks.py.
Never log the file or the model's answer: invoices contain business data.
"""

import base64
import io
import json
import logging
import os
import re
import time

import pypdfium2 as pdfium
import requests
from PIL import Image
from pydantic import ValidationError

from checks import check_invoice
from schema import RESPONSE_SCHEMA, ExtractionResult

log = logging.getLogger("invoice_ai")

PDF_MIME = "application/pdf"
IMAGE_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "TIFF": "image/tiff", "BMP": "image/bmp"}
MAX_PAGES = 20

PROMPT = (
    "This document contains one or more invoices (French or English, often Tunisian). "
    "Extract every invoice. Rules: "
    "copy values exactly as printed; use null when a value is not on the document, never guess; "
    "numbers as plain numbers without currency or thousands separators (\"1 190,000\" → 1190.0); "
    "dates as YYYY-MM-DD; vat_rate in percent; currency as an ISO code (DT/TND → TND, € → EUR). "
    "If there is no invoice, return an empty list."
)


class ExtractionError(Exception):
    """Raised when the document can't be read or the model doesn't give valid data."""


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "gemini").lower()


def model_name() -> str:
    if provider() == "groq":
        return os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


# ─────────────────────────────────────────────────────────────────────
# 1. File type checks (by content, not by file name)
# ─────────────────────────────────────────────────────────────────────

def detect_mime(file_bytes: bytes) -> str:
    if file_bytes.startswith(b"%PDF-"):
        return PDF_MIME
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
    except Exception:
        raise ExtractionError("The file is not a readable PDF or image.") from None
    mime = IMAGE_MIMES.get(img.format or "")
    if not mime:
        raise ExtractionError(f"Unsupported image format: {img.format}.")
    return mime


def _to_jpeg_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def pdf_pages_b64(file_bytes: bytes) -> list[str]:
    doc = pdfium.PdfDocument(file_bytes)
    if len(doc) > MAX_PAGES:
        raise ExtractionError(f"PDF has {len(doc)} pages; the limit is {MAX_PAGES}.")
    return [_to_jpeg_b64(doc[i].render(scale=2).to_pil()) for i in range(len(doc))]


# ─────────────────────────────────────────────────────────────────────
# 2. Providers — each returns the raw JSON text from the model
# ─────────────────────────────────────────────────────────────────────

def _call_gemini(file_bytes: bytes, mime: str) -> str:
    from google import genai
    from google.genai import errors, types

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ExtractionError("GEMINI_API_KEY is not set in .env.")
    if mime in ("image/tiff", "image/bmp"):  # not accepted by Gemini: convert first
        file_bytes, mime = base64.b64decode(_to_jpeg_b64(Image.open(io.BytesIO(file_bytes)))), "image/jpeg"

    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(
            model=model_name(),
            contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime), PROMPT],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA,
                http_options=types.HttpOptions(timeout=120_000),
            ),
        )
    except errors.APIError as e:
        if e.code == 429:
            raise ExtractionError("Gemini rate limit reached. Wait a minute and try again.") from None
        raise ExtractionError(f"Gemini failed (HTTP {e.code}).") from None
    return response.text or ""


def _call_groq_page(b64_jpeg: str) -> str:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise ExtractionError("GROQ_API_KEY is not set in .env.")
    payload = {
        "model": model_name(),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_jpeg}"}},
                {"type": "text", "text": PROMPT + " Answer with JSON only, in this shape: "
                                                  + json.dumps(RESPONSE_SCHEMA)},
            ],
        }],
        "temperature": 0,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(3):
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=120,
        )
        if resp.status_code == 429 and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise ExtractionError(f"Groq failed (HTTP {resp.status_code}).")
        return resp.json()["choices"][0]["message"]["content"]
    raise ExtractionError("Groq rate limit reached. Wait a minute and try again.")


# ─────────────────────────────────────────────────────────────────────
# 3. Parse + validate (one retry when the model returns broken JSON)
# ─────────────────────────────────────────────────────────────────────

def parse_result(raw: str) -> ExtractionResult:
    """Parses the model's text into an ExtractionResult. Raises ValueError if it isn't valid."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    data = json.loads(raw)
    if isinstance(data, list):  # some models drop the wrapper object
        data = {"invoices": data}
    return ExtractionResult.model_validate(data)


def _with_retry(call) -> ExtractionResult:
    for attempt in range(2):
        raw = call()
        try:
            return parse_result(raw)
        except (json.JSONDecodeError, ValidationError, ValueError):
            log.warning("invalid model output (attempt %d)", attempt + 1)
    raise ExtractionError("The model returned invalid data twice.")


def process_invoice(file_bytes: bytes) -> dict:
    mime = detect_mime(file_bytes)
    started = time.perf_counter()

    if provider() == "groq":
        pages = pdf_pages_b64(file_bytes) if mime == PDF_MIME else [_to_jpeg_b64(Image.open(io.BytesIO(file_bytes)))]
        invoices = []
        for page_num, b64 in enumerate(pages, start=1):
            for inv in _with_retry(lambda: _call_groq_page(b64)).invoices:
                inv.page = page_num
                invoices.append(inv)
        page_count = len(pages)
    else:
        page_count = len(pdfium.PdfDocument(file_bytes)) if mime == PDF_MIME else 1
        if page_count > MAX_PAGES:
            raise ExtractionError(f"PDF has {page_count} pages; the limit is {MAX_PAGES}.")
        invoices = _with_retry(lambda: _call_gemini(file_bytes, mime)).invoices

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info("extracted %d invoice(s) from %d page(s) with %s in %d ms",
             len(invoices), page_count, model_name(), duration_ms)

    return {
        "invoices": [{**inv.model_dump(), "warnings": check_invoice(inv)} for inv in invoices],
        "page_count": page_count,
        "invoice_count": len(invoices),
        "provider": provider(),
        "model": model_name(),
        "duration_ms": duration_ms,
    }
