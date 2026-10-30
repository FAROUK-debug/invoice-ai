"""
evaluate.py — Measures extraction accuracy on the fictional samples.

For every file in samples/, runs the real pipeline and compares each field with
samples/expected/<name>.json. Text is compared case- and space-insensitively,
numbers within 0.01, dates exactly. Writes eval/report.md and prints a summary.

Run: python scripts/evaluate.py          (needs a key in .env; each file = 1 API call)
"""

import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import extractor  # noqa: E402

SAMPLES = ROOT / "samples"
REPORT = ROOT / "eval" / "report.md"

TEXT_FIELDS = ["invoice_number", "supplier_name", "supplier_address", "supplier_tax_id",
               "client_name", "client_address", "client_tax_id", "currency", "payment_method"]
DATE_FIELDS = ["invoice_date", "due_date"]
NUMBER_FIELDS = ["subtotal", "vat_rate", "vat_amount", "total_amount"]
ITEM_FIELDS = ["description", "quantity", "unit_price", "total"]


def norm_text(v) -> str | None:
    if v is None:
        return None
    v = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return re.sub(r"[\s,.]+", " ", v).strip().casefold()


def same(field: str, got, want) -> bool:
    if want is None or got is None:
        return got is None and want is None
    if field in NUMBER_FIELDS or field in ("quantity", "unit_price", "total"):
        try:
            return abs(float(got) - float(want)) <= 0.01
        except (TypeError, ValueError):
            return False
    if field in DATE_FIELDS:
        return str(got) == str(want)
    return norm_text(got) == norm_text(want)


def score_file(got: list[dict], want: list[dict], tally: dict, misses: list):
    tally["invoice_count"].append(len(got) == len(want))
    by_number = {norm_text(g.get("invoice_number")): g for g in got}
    for w in want:
        g = by_number.get(norm_text(w["invoice_number"]), {})
        for f in TEXT_FIELDS + DATE_FIELDS + NUMBER_FIELDS:
            ok = same(f, g.get(f), w.get(f))
            tally[f].append(ok)
            if not ok:
                misses.append((w["invoice_number"], f, g.get(f), w.get(f)))
        g_items = g.get("line_items") or []
        tally["line_item_count"].append(len(g_items) == len(w["line_items"]))
        for i, wi in enumerate(w["line_items"]):
            gi = g_items[i] if i < len(g_items) else {}
            for f in ITEM_FIELDS:
                ok = same(f, gi.get(f), wi.get(f))
                tally[f"line_items.{f}"].append(ok)
                if not ok:
                    misses.append((w["invoice_number"], f"line {i + 1} {f}", gi.get(f), wi.get(f)))


def main():
    files = sorted(p for p in SAMPLES.iterdir() if p.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"))
    tally: dict[str, list[bool]] = defaultdict(list)
    misses: list[tuple] = []
    flagged: dict[str, bool] = {}
    durations = []

    for path in files:
        want = json.loads((SAMPLES / "expected" / f"{path.stem}.json").read_text(encoding="utf-8"))["invoices"]
        started = time.perf_counter()
        result = extractor.process_invoice(path.read_bytes())
        durations.append(time.perf_counter() - started)
        got = result["invoices"]
        score_file(got, want, tally, misses)
        flagged[path.stem] = any(inv["warnings"] for inv in got)
        print(f"{path.name}: {len(got)} invoice(s), {durations[-1]:.1f}s")
        time.sleep(4)  # stay under the free-tier requests-per-minute limit

    all_checks = [ok for oks in tally.values() for ok in oks]
    overall = sum(all_checks) / len(all_checks)

    lines = [
        "# Evaluation report",
        "",
        f"Provider: **{extractor.provider()}**, model: **{extractor.model_name()}**  ",
        f"Files: {len(files)}, field checks: {len(all_checks)}, average time per file: "
        f"{sum(durations) / len(durations):.1f}s",
        "",
        f"**Overall field accuracy: {overall:.1%}**",
        "",
        "| Field | Correct | Accuracy |",
        "|---|---|---|",
    ]
    for field, oks in tally.items():
        lines.append(f"| {field} | {sum(oks)}/{len(oks)} | {sum(oks) / len(oks):.0%} |")

    lines += ["", "## Totals check", "",
              "`05_wrong_total` has a printed total that doesn't add up; it should be the only file flagged.", "",
              "| File | Flagged | Expected |", "|---|---|---|"]
    for name, was_flagged in flagged.items():
        expected = name == "05_wrong_total"
        mark = "✓" if was_flagged == expected else "✗"
        lines.append(f"| {name} | {'yes' if was_flagged else 'no'} | {'yes' if expected else 'no'} {mark} |")

    lines += ["", "## Mistakes", ""]
    if misses:
        lines += ["| Invoice | Field | Got | Expected |", "|---|---|---|---|"]
        lines += [f"| {inv} | {f} | {g} | {w} |" for inv, f, g, w in misses]
    else:
        lines.append("None.")

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nOverall field accuracy: {overall:.1%}  ({len(misses)} mistakes)  → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
