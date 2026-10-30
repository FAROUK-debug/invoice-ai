"""
main.py — FastAPI backend for InvoiceAI.
Run: uvicorn main:app --reload        (listens on 127.0.0.1:8000 only)

Security choices:
  - uploaded files are processed in memory and never written to disk;
  - results are saved under a random id and can only be downloaded by that id;
  - file type is checked from the content, size is capped while reading;
  - error messages sent to the browser never include internal details.
"""

import csv
import io
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response

load_dotenv()

import extractor  # noqa: E402  (needs .env loaded first)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("invoice_ai")

BASE_DIR = Path(__file__).parent
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", BASE_DIR / "results"))
RESULTS_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_BATCH_FILES = 10
RESULT_ID = re.compile(r"^[a-f0-9]{32}$")

app = FastAPI(title="InvoiceAI", description="Invoice data extraction with a vision LLM", version="2.0.0")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse((BASE_DIR / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": extractor.provider(),
        "model": extractor.model_name(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def safe_filename(name: str | None) -> str:
    """Keeps only the base name and safe characters, for display and the saved result."""
    base = Path((name or "file").replace("\\", "/")).name
    return re.sub(r"[^\w.\- ]", "_", base)[:100] or "file"


async def read_limited(file: UploadFile) -> bytes:
    limit = MAX_FILE_SIZE_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File too large. Max {MAX_FILE_SIZE_MB} MB.")
    if not data:
        raise HTTPException(400, "Empty file.")
    return data


async def run_extraction(data: bytes, filename: str) -> dict:
    try:
        result = await run_in_threadpool(extractor.process_invoice, data)
    except extractor.ExtractionError as e:
        raise HTTPException(422, str(e)) from None
    except Exception:
        log.exception("unexpected extraction error")
        raise HTTPException(500, "Extraction failed. Check the server logs.") from None

    result_id = uuid.uuid4().hex
    result["source_file"] = filename
    (RESULTS_DIR / f"{result_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "filename": filename, "result_id": result_id, **result}


@app.post("/api/process")
async def process_one(file: UploadFile = File(...)):
    data = await read_limited(file)
    return await run_extraction(data, safe_filename(file.filename))


@app.post("/api/batch")
async def process_batch(files: list[UploadFile] = File(...)):
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(400, f"Max {MAX_BATCH_FILES} files per batch.")
    results = []
    for file in files:
        name = safe_filename(file.filename)
        try:
            results.append(await run_extraction(await read_limited(file), name))
        except HTTPException as e:
            results.append({"success": False, "filename": name, "error": e.detail})
    return {"batch_results": results}


CSV_FIELDS = ["invoice_number", "invoice_date", "due_date", "supplier_name", "supplier_tax_id",
              "client_name", "client_tax_id", "subtotal", "vat_rate", "vat_amount", "total_amount",
              "currency", "payment_method", "page", "warnings"]


def to_csv(result: dict) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for inv in result.get("invoices", []):
        writer.writerow({**inv, "warnings": " | ".join(inv.get("warnings", []))})
    return buf.getvalue()


@app.get("/api/download/{result_id}")
async def download(result_id: str, format: str = Query("json", pattern="^(json|csv)$")):
    # Only a 32-hex id is accepted, so the path can never leave RESULTS_DIR.
    if not RESULT_ID.match(result_id):
        raise HTTPException(404, "Result not found.")
    path = RESULTS_DIR / f"{result_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Result not found.")
    if format == "csv":
        csv_text = to_csv(json.loads(path.read_text(encoding="utf-8")))
        return Response(
            "﻿" + csv_text,  # BOM so Excel opens accents correctly
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="invoices_{result_id[:8]}.csv"'},
        )
    return FileResponse(path, media_type="application/json", filename=f"invoices_{result_id[:8]}.json")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
