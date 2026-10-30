# Evaluation report

Provider: **gemini**, model: **gemini-3.5-flash-lite**  
Files: 5, field checks: 153, average time per file: 3.2s

**Overall field accuracy: 100.0%**

| Field | Correct | Accuracy |
|---|---|---|
| invoice_count | 5/5 | 100% |
| invoice_number | 6/6 | 100% |
| supplier_name | 6/6 | 100% |
| supplier_address | 6/6 | 100% |
| supplier_tax_id | 6/6 | 100% |
| client_name | 6/6 | 100% |
| client_address | 6/6 | 100% |
| client_tax_id | 6/6 | 100% |
| currency | 6/6 | 100% |
| payment_method | 6/6 | 100% |
| invoice_date | 6/6 | 100% |
| due_date | 6/6 | 100% |
| subtotal | 6/6 | 100% |
| vat_rate | 6/6 | 100% |
| vat_amount | 6/6 | 100% |
| total_amount | 6/6 | 100% |
| line_item_count | 6/6 | 100% |
| line_items.description | 13/13 | 100% |
| line_items.quantity | 13/13 | 100% |
| line_items.unit_price | 13/13 | 100% |
| line_items.total | 13/13 | 100% |

## Totals check

`05_wrong_total` has a printed total that doesn't add up; it should be the only file flagged.

| File | Flagged | Expected |
|---|---|---|
| 01_tn_facture_simple | no | no ✓ |
| 02_en_invoice_eur | no | no ✓ |
| 03_two_invoices | no | no ✓ |
| 04_scanned | no | no ✓ |
| 05_wrong_total | yes | yes ✓ |

## Mistakes

None.
