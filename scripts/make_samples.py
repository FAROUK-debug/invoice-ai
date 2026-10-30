"""
make_samples.py — Generates fictional sample invoices and their expected answers.

All companies, people and numbers are invented. Each invoice is described once in
SAMPLES; the same data draws the PDF and writes samples/expected/<name>.json,
so the expected answers always match the documents.

Run: python scripts/make_samples.py
"""

import io
import json
import random
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
EXPECTED = OUT / "expected"

L = {  # labels per language
    "fr": dict(title="FACTURE", no="Facture N°", date="Date", due="Échéance", client="Client",
               mf="MF", desc="Désignation", qty="Qté", pu="P.U. HT", total="Total HT",
               subtotal="Total HT", vat="TVA", stamp="Timbre fiscal", ttc="Net à payer", pay="Mode de paiement"),
    "en": dict(title="INVOICE", no="Invoice No.", date="Date", due="Due date", client="Bill to",
               mf="VAT ID", desc="Description", qty="Qty", pu="Unit price", total="Amount",
               subtotal="Subtotal", vat="VAT", stamp="Stamp duty", ttc="Total due", pay="Payment method"),
}


def invoice(number, date, due, supplier, client, items, vat_rate, currency, lang,
            payment, stamp=0.0, total_override=None, notes=None):
    lines = [{"description": d, "quantity": q, "unit_price": p, "total": round(q * p, 3)} for d, q, p in items]
    subtotal = round(sum(li["total"] for li in lines), 3)
    vat_amount = round(subtotal * vat_rate / 100, 3)
    total = total_override if total_override is not None else round(subtotal + vat_amount + stamp, 3)
    return {
        "invoice_number": number, "invoice_date": date, "due_date": due,
        "supplier_name": supplier[0], "supplier_address": supplier[1], "supplier_tax_id": supplier[2],
        "client_name": client[0], "client_address": client[1], "client_tax_id": client[2],
        "line_items": lines, "subtotal": subtotal, "vat_rate": vat_rate, "vat_amount": vat_amount,
        "total_amount": total, "currency": currency, "payment_method": payment, "notes": notes,
        "_lang": lang, "_stamp": stamp,
    }


SUPPLIER_TN = ("Atlas Informatique SARL", "12 Rue des Oliviers, 1002 Tunis", "1234567/A/M/000")
SUPPLIER_TN2 = ("Medina Fournitures", "45 Avenue de Carthage, 2000 Le Bardo", "7654321/B/P/000")
SUPPLIER_FR = ("Brightline Studio SAS", "8 Rue Imaginaire, 69002 Lyon, France", "FR12345678901")
CLIENT_TN = ("Société Jasmin Services", "3 Rue du Lac Léman, 1053 Les Berges du Lac", "9876543/C/M/000")
CLIENT_EN = ("Northwind Demo Ltd", "77 Example Street, Dublin, Ireland", "IE9999999X")

SAMPLES = {
    # 1. Typical Tunisian invoice: TND with 3 decimals, 19% VAT, 1 TND stamp duty
    "01_tn_facture_simple": [invoice(
        "FA-2026-0142", "2026-09-15", "2026-10-15", SUPPLIER_TN, CLIENT_TN,
        [("Ordinateur portable 15 pouces", 2, 1850.0), ("Souris sans fil", 2, 35.5), ("Installation et configuration", 1, 120.0)],
        19, "TND", "fr", "Virement bancaire", stamp=1.0)],
    # 2. English invoice in euros
    "02_en_invoice_eur": [invoice(
        "INV-3307", "2026-08-02", "2026-09-01", SUPPLIER_FR, CLIENT_EN,
        [("Logo design", 1, 900.0), ("Brand guidelines (PDF)", 1, 450.0), ("Revision hours", 6, 55.0)],
        20, "EUR", "en", "Bank transfer")],
    # 3. Two invoices in one PDF, one per page
    "03_two_invoices": [
        invoice("FA-2026-0201", "2026-10-01", "2026-10-31", SUPPLIER_TN2, CLIENT_TN,
                [("Ramette papier A4", 40, 12.8), ("Stylos bleus (boîte de 50)", 3, 22.0)],
                19, "TND", "fr", "Chèque", stamp=1.0),
        invoice("FA-2026-0202", "2026-10-03", "2026-11-02", SUPPLIER_TN2, CLIENT_TN,
                [("Classeurs à levier", 25, 6.75)],
                19, "TND", "fr", "Espèces", stamp=1.0),
    ],
    # 4. Same kind of invoice, but as a slightly rotated, blurred scan (PNG)
    "04_scanned": [invoice(
        "FA-2026-0177", "2026-07-20", None, SUPPLIER_TN, CLIENT_TN,
        [("Maintenance serveur (mensuel)", 3, 400.0), ("Licence antivirus", 10, 48.0)],
        19, "TND", "fr", "Virement bancaire", stamp=1.0)],
    # 5. An invoice whose printed total is wrong: the app should flag it
    "05_wrong_total": [invoice(
        "INV-3311", "2026-09-28", "2026-10-28", SUPPLIER_FR, CLIENT_EN,
        [("Website maintenance", 1, 300.0), ("Hosting (12 months)", 1, 240.0)],
        20, "EUR", "en", "Card", total_override=658.0)],
}


def draw_invoice(c: canvas.Canvas, inv: dict):
    t = L[inv["_lang"]]
    money = (lambda v: f"{v:,.3f}".replace(",", " ")) if inv["currency"] == "TND" else (lambda v: f"{v:,.2f}")
    w, h = A4
    y = h - 25 * mm

    c.setFont("Helvetica-Bold", 15); c.drawString(20 * mm, y, inv["supplier_name"])
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y - 6 * mm, inv["supplier_address"])
    c.drawString(20 * mm, y - 11 * mm, f'{t["mf"]}: {inv["supplier_tax_id"]}')
    c.setFont("Helvetica-Bold", 20); c.drawRightString(w - 20 * mm, y, t["title"])
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 20 * mm, y - 8 * mm, f'{t["no"]} {inv["invoice_number"]}')
    c.drawRightString(w - 20 * mm, y - 13 * mm, f'{t["date"]}: {inv["invoice_date"]}')
    if inv["due_date"]:
        c.drawRightString(w - 20 * mm, y - 18 * mm, f'{t["due"]}: {inv["due_date"]}')

    y -= 35 * mm
    c.setFont("Helvetica-Bold", 10); c.drawString(20 * mm, y, t["client"])
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y - 5 * mm, inv["client_name"])
    c.drawString(20 * mm, y - 10 * mm, inv["client_address"])
    c.drawString(20 * mm, y - 15 * mm, f'{t["mf"]}: {inv["client_tax_id"]}')

    y -= 30 * mm
    cols = [20 * mm, 120 * mm, 140 * mm, 170 * mm]
    c.setFillGray(0.92); c.rect(18 * mm, y - 2 * mm, w - 36 * mm, 8 * mm, fill=1, stroke=0); c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(cols[0], y, t["desc"]); c.drawRightString(cols[1] + 10 * mm, y, t["qty"])
    c.drawRightString(cols[2] + 20 * mm, y, t["pu"]); c.drawRightString(w - 20 * mm, y, t["total"])
    c.setFont("Helvetica", 9)
    for li in inv["line_items"]:
        y -= 7 * mm
        c.drawString(cols[0], y, li["description"])
        c.drawRightString(cols[1] + 10 * mm, y, f'{li["quantity"]:g}')
        c.drawRightString(cols[2] + 20 * mm, y, money(li["unit_price"]))
        c.drawRightString(w - 20 * mm, y, money(li["total"]))
    c.line(18 * mm, y - 4 * mm, w - 18 * mm, y - 4 * mm)

    y -= 12 * mm
    rows = [(t["subtotal"], inv["subtotal"]), (f'{t["vat"]} {inv["vat_rate"]:g}%', inv["vat_amount"])]
    if inv["_stamp"]:
        rows.append((t["stamp"], inv["_stamp"]))
    rows.append((t["ttc"], inv["total_amount"]))
    for i, (label, val) in enumerate(rows):
        bold = i == len(rows) - 1
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        c.drawString(120 * mm, y, label)
        c.drawRightString(w - 20 * mm, y, f'{money(val)} {inv["currency"]}')
        y -= 7 * mm

    y -= 8 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, f'{t["pay"]}: {inv["payment_method"]}')
    c.setFont("Helvetica-Oblique", 7); c.setFillGray(0.5)
    c.drawString(20 * mm, 15 * mm, "Fictional document generated for testing InvoiceAI. Not a real invoice.")
    c.setFillGray(0)


def build_pdf(invoices: list[dict]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for inv in invoices:
        draw_invoice(c, inv)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_scan(pdf_bytes: bytes) -> bytes:
    """Renders page 1 and makes it look scanned: grey, slight rotation, blur and noise."""
    random.seed(7)
    img = pdfium.PdfDocument(pdf_bytes)[0].render(scale=1.6).to_pil().convert("L")
    img = img.rotate(1.2, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.7))
    px = img.load()
    for _ in range(img.width * img.height // 60):
        x, y = random.randrange(img.width), random.randrange(img.height)
        px[x, y] = random.choice((90, 140, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main():
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for name, invoices in SAMPLES.items():
        pdf = build_pdf(invoices)
        if name == "04_scanned":
            (OUT / f"{name}.png").write_bytes(make_scan(pdf))
        else:
            (OUT / f"{name}.pdf").write_bytes(pdf)
        expected = [{k: v for k, v in inv.items() if not k.startswith("_")} for inv in invoices]
        (EXPECTED / f"{name}.json").write_text(json.dumps({"invoices": expected}, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
