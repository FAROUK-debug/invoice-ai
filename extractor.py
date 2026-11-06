"""
extractor.py — Turns an invoice file (PDF or image) into validated invoice data.

The whole file goes to Gemini in one call, with the invoice schema as structured output.
The answer is validated against schema.ExtractionResult, and every invoice goes through
the totals checks in checks.py.
Never log the file or the model's answer: invoices contain business data.
"""

import io
import json
import logging
import os
import re
import time

import pypdfium2 as pdfium
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


def model_name() -> str:
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


def pdf_page_count(file_bytes: bytes) -> int:
    try:
        return len(pdfium.PdfDocument(file_bytes))
    except pdfium.PdfiumError:
        raise ExtractionError("The PDF is damaged or password-protected.") from None


# ─────────────────────────────────────────────────────────────────────
# 2. The model call: returns the raw JSON text
# ─────────────────────────────────────────────────────────────────────

def _call_gemini(file_bytes: bytes, mime: str) -> str:
    from google import genai
    from google.genai import errors, types

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ExtractionError("GEMINI_API_KEY is not set in .env.")
    if mime in ("image/tiff", "image/bmp"):  # not accepted by Gemini: convert first
        buf = io.BytesIO()
        Image.open(io.BytesIO(file_bytes)).convert("RGB").save(buf, format="JPEG", quality=85)
        file_bytes, mime = buf.getvalue(), "image/jpeg"

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

    page_count = pdf_page_count(file_bytes) if mime == PDF_MIME else 1
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
        "model": model_name(),
        "duration_ms": duration_ms,
    }
