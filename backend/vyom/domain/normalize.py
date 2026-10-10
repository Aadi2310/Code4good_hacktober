from __future__ import annotations

import re
from decimal import Decimal

from .common import ascii_digits, clean_text, numeric_lookalikes
from .dates import parse_date
from .gstin import normalize as normalize_gstin
from .identifiers import normalize_hsn, normalize_invoice_no, normalize_state, normalize_unit
from .money import parse_money
from .rates import parse_rate

MONEY_FIELDS = {"subtotal", "discount", "taxable_value", "cgst", "sgst", "igst", "cess", "total_tax", "round_off", "total_amount", "unit_price", "line_total"}
DATE_FIELDS = {"invoice_date", "due_date"}
GSTIN_FIELDS = {"supplier_gstin", "buyer_gstin"}
TEXT_FIELDS = {"document_type", "invoice_no", "purchase_order_no", "supplier_name", "supplier_address", "buyer_name", "buyer_address", "amount_in_words", "description", "line_no"}


def _bool(raw: str | None) -> tuple[str | None, list[str]]:
    value = clean_text(raw or "").casefold()
    if value in {"yes", "y", "true", "1", "是", "included", "applicable"}:
        return "true", []
    if value in {"no", "n", "false", "0", "not applicable", "not applicable."}:
        return "false", []
    return None, ["BOOLEAN_INVALID"]


def normalize_field(name: str, raw: str | None, *, locale: str = "in") -> tuple[str | None, list[str]]:
    """Normalize a contract field. Errors are returned as notes, never raised."""
    try:
        key = name.rsplit(".", 1)[-1].lower()
        if raw is None:
            return None, ["VALUE_MISSING"]
        if key in MONEY_FIELDS:
            return parse_money(raw, locale_hint=locale)
        if key in DATE_FIELDS:
            return parse_date(raw)
        if key in GSTIN_FIELDS:
            return normalize_gstin(raw)
        if key == "tax_rate":
            return parse_rate(raw)
        if key == "quantity":
            text = ascii_digits(clean_text(raw)).replace(",", "")
            text, _ = numeric_lookalikes(text)
            try:
                qty = Decimal(text)
            except Exception:
                return None, ["QUANTITY_INVALID"]
            return format(qty.normalize(), "f"), []
        if key == "hsn_sac":
            return normalize_hsn(raw)
        if key == "place_of_supply":
            return normalize_state(raw)
        if key == "unit":
            return normalize_unit(raw)
        if key in {"reverse_charge", "price_includes_tax"}:
            return _bool(raw)
        if key == "invoice_no":
            return normalize_invoice_no(raw)
        if key == "document_type":
            value = clean_text(raw).casefold().replace(" ", "_")
            aliases = {"invoice":"tax_invoice", "tax_invoice":"tax_invoice", "bill_of_supply":"bill_of_supply", "credit_note":"credit_note", "debit_note":"debit_note", "composition":"bill_of_supply"}
            return aliases.get(value, value), []
        if key in TEXT_FIELDS:
            return clean_text(raw) or None, []
        return clean_text(raw) or None, ["FIELD_UNRECOGNIZED"]
    except Exception as exc:
        return None, [f"NORMALIZATION_FAILED:{type(exc).__name__}"]
