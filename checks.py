"""
checks.py — Accounting sanity checks on an extracted invoice.

The model can misread a digit. These checks don't fix anything: they flag invoices
where the numbers don't add up, so a human looks at them before they're used.
"""

from schema import Invoice

# Amounts are often rounded to 2 or 3 decimals (TND uses 3); allow a small gap.
TOLERANCE = 0.02


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOLERANCE, abs(b) * 0.001)


def check_invoice(inv: Invoice) -> list[str]:
    warnings: list[str] = []

    if inv.total_amount is None:
        warnings.append("Total amount not found.")

    for i, item in enumerate(inv.line_items, start=1):
        if None not in (item.quantity, item.unit_price, item.total):
            if not _close(item.quantity * item.unit_price, item.total):
                warnings.append(
                    f"Line {i}: {item.quantity} × {item.unit_price} = "
                    f"{item.quantity * item.unit_price:.3f}, but the line total is {item.total}."
                )

    line_totals = [it.total for it in inv.line_items]
    if inv.subtotal is not None and line_totals and None not in line_totals:
        s = sum(line_totals)
        if not _close(s, inv.subtotal):
            warnings.append(f"Line items add up to {s:.3f}, but the subtotal is {inv.subtotal}.")

    if None not in (inv.subtotal, inv.vat_rate, inv.vat_amount):
        expected = inv.subtotal * inv.vat_rate / 100
        if not _close(expected, inv.vat_amount):
            warnings.append(
                f"VAT {inv.vat_rate}% of {inv.subtotal} is {expected:.3f}, but the VAT amount is {inv.vat_amount}."
            )

    if None not in (inv.subtotal, inv.vat_amount, inv.total_amount):
        # Some invoices add a fixed stamp duty (e.g. Tunisian "timbre fiscal", 1 TND) after VAT.
        gap = inv.total_amount - (inv.subtotal + inv.vat_amount)
        if not _close(gap, 0) and not (0 < gap <= 1.0 + TOLERANCE):
            warnings.append(
                f"Subtotal + VAT = {inv.subtotal + inv.vat_amount:.3f}, but the total is {inv.total_amount}."
            )

    return warnings
