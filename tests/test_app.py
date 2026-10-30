"""Tests that run without any API key: the model call is replaced by a fake."""

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
    monkeypatch.setattr(extractor, "provider", lambda: "gemini")
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
    assert body["status"] == "ok" and body["provider"] in ("gemini", "groq")


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


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
