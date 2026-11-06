"""Tests that run without any API key: the model call is replaced by a fake."""

import csv
import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import extractor  # noqa: E402
import main  # noqa: E402
from checks import check_invoice  # noqa: E402
from schema import Invoice  # noqa: E402

SAMPLES = ROOT / "samples"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "RESULTS_DIR", tmp_path)
    return TestClient(main.app)


@pytest.fixture
def fake_model(monkeypatch):
    """Makes the Gemini call return the expected answer of sample 01."""
    answer = (SAMPLES / "expected" / "01_tn_facture_simple.json").read_text(encoding="utf-8")
    monkeypatch.setattr(extractor, "_call_gemini", lambda data, mime: answer)


# ── checks.py ────────────────────────────────────────────────────────

def expected_invoices(name):
    data = json.loads((SAMPLES / "expected" / f"{name}.json").read_text(encoding="utf-8"))
    return [Invoice.model_validate(inv) for inv in data["invoices"]]


@pytest.mark.parametrize("name", ["01_tn_facture_simple", "02_en_invoice_eur", "03_two_invoices", "04_scanned"])
def test_consistent_samples_have_no_warnings(name):
    for inv in expected_invoices(name):
        assert check_invoice(inv) == []


def test_wrong_total_is_flagged():
    [inv] = expected_invoices("05_wrong_total")
    warnings = check_invoice(inv)
    assert len(warnings) == 1 and "total is 658" in warnings[0]


def test_wrong_line_total_is_flagged():
    inv = Invoice(line_items=[{"description": "x", "quantity": 2, "unit_price": 10, "total": 25}],
                  subtotal=25, total_amount=25)
    assert any("Line 1" in w for w in check_invoice(inv))


def test_gap_of_one_is_stamp_duty_only_in_tnd():
    assert check_invoice(Invoice(subtotal=100, vat_amount=20, total_amount=121, currency="TND")) == []
    assert check_invoice(Invoice(subtotal=100, vat_amount=20, total_amount=121, currency="EUR"))


def test_small_error_on_a_large_invoice_is_flagged():
    inv = Invoice(line_items=[{"quantity": 1, "unit_price": 50000, "total": 50040}], total_amount=50040)
    assert any("Line 1" in w for w in check_invoice(inv))


def test_rounding_is_not_flagged():
    inv = Invoice(line_items=[{"quantity": 3, "unit_price": 1.333, "total": 4}], subtotal=4, total_amount=4)
    assert check_invoice(inv) == []


# ── parsing the model's answer ───────────────────────────────────────

def test_parse_accepts_code_fence_and_bare_list():
    assert len(extractor.parse_result('```json\n{"invoices": [{"invoice_number": "A1"}]}\n```').invoices) == 1
    assert extractor.parse_result('[{"invoice_number": "A1"}]').invoices[0].invoice_number == "A1"


def test_invalid_output_twice_raises():
    calls = []
    with pytest.raises(extractor.ExtractionError):
        extractor._with_retry(lambda: calls.append(1) or "not json")
    assert len(calls) == 2


# ── API ──────────────────────────────────────────────────────────────

def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["model"]


def test_process_and_download(client, fake_model):
    pdf = (SAMPLES / "01_tn_facture_simple.pdf").read_bytes()
    r = client.post("/api/process", files={"file": ("../../evil<script>.pdf", pdf, "application/pdf")})
    assert r.status_code == 200
    body = r.json()
    assert body["invoice_count"] == 1
    assert body["invoices"][0]["total_amount"] == 4631.29
    assert body["invoices"][0]["warnings"] == []
    assert "/" not in body["filename"] and "<" not in body["filename"]

    rid = body["result_id"]
    assert client.get(f"/api/download/{rid}").json()["invoice_count"] == 1
    csv_text = client.get(f"/api/download/{rid}?format=csv").text
    assert "FA-2026-0142" in csv_text


@pytest.mark.parametrize("bad_id", ["..%5C.env", "..%2F.env", "abc", "0" * 31 + "g", "%2e%2e%5cmain.py"])
def test_download_rejects_anything_but_an_id(client, bad_id):
    assert client.get(f"/api/download/{bad_id}").status_code == 404


def test_rejects_files_that_are_not_pdf_or_image(client):
    r = client.post("/api/process", files={"file": ("invoice.pdf", b"MZ this is an exe", "application/pdf")})
    assert r.status_code == 422


def test_rejects_too_large_files(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    r = client.post("/api/process", files={"file": ("big.pdf", b"%PDF-" + b"0" * (1024 * 1024), "application/pdf")})
    assert r.status_code == 413


def test_oversized_upload_is_refused_before_it_is_read(client, monkeypatch):
    async def must_not_run(file):
        raise AssertionError("the body was read")
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    monkeypatch.setattr(main, "read_limited", must_not_run)
    r = client.post("/api/process", files={"file": ("big.pdf", b"%PDF-" + b"0" * (2 * 1024 * 1024), "application/pdf")})
    assert r.status_code == 413


def test_upload_without_a_length_is_refused(client):
    assert client.post("/api/process", content=iter([b"data"])).status_code == 411


def test_batch_may_be_larger_than_one_file(client, fake_model, monkeypatch):
    monkeypatch.setattr(main, "MAX_FILE_SIZE_MB", 1)
    pdf = (SAMPLES / "01_tn_facture_simple.pdf").read_bytes() + b" " * (700 * 1024)
    files = [("files", (f"{i}.pdf", pdf, "application/pdf")) for i in range(2)]
    assert client.post("/api/batch", files=files).status_code == 200


def test_damaged_pdf_is_a_clean_error(client):
    r = client.post("/api/process", files={"file": ("a.pdf", b"%PDF-1.7 broken", "application/pdf")})
    assert r.status_code == 422 and "damaged" in r.json()["detail"]


@pytest.mark.parametrize("text", ['=HYPERLINK("http://x","y")', "+1", "-1", "@SUM(A1)"])
def test_csv_cells_cannot_become_formulas(text):
    text_csv = main.to_csv({"invoices": [{"supplier_name": text, "warnings": []}]})
    assert next(csv.DictReader(io.StringIO(text_csv)))["supplier_name"] == "'" + text


def test_csv_keeps_numbers_and_normal_text():
    row = main.to_csv({"invoices": [{"supplier_name": "ACME", "total_amount": -5.0, "warnings": []}]}).splitlines()[1]
    assert "ACME" in row and "-5.0" in row and "'" not in row


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
