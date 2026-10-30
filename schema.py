"""
schema.py — The invoice format the model must return.

The Pydantic models validate what comes back. The same models, turned into a plain
JSON Schema, are sent to Gemini as structured output (Gemini doesn't accept $ref,
so references are inlined).
"""

from typing import Any, Optional
from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    total: Optional[float] = None


class Invoice(BaseModel):
    page: Optional[int] = Field(None, description="Page number where the invoice starts.")
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    due_date: Optional[str] = Field(None, description="ISO date YYYY-MM-DD.")
    supplier_name: Optional[str] = None
    supplier_address: Optional[str] = None
    supplier_tax_id: Optional[str] = None
    client_name: Optional[str] = None
    client_address: Optional[str] = None
    client_tax_id: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = Field(None, description="Total before VAT.")
    vat_rate: Optional[float] = Field(None, description="Percent, e.g. 19 for 19%.")
    vat_amount: Optional[float] = None
    total_amount: Optional[float] = Field(None, description="Amount to pay, including VAT and stamp duty.")
    currency: Optional[str] = Field(None, description="ISO 4217 code, e.g. TND, EUR, USD.")
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class ExtractionResult(BaseModel):
    invoices: list[Invoice] = Field(default_factory=list)


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(defs[node["$ref"].split("/")[-1]], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


_schema = ExtractionResult.model_json_schema()
RESPONSE_SCHEMA = _inline_refs(_schema, _schema.get("$defs", {}))
