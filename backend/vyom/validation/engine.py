from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from vyom.domain.common import load_reference
from vyom.domain.gstin import check_char, is_valid as gstin_is_valid, parse as parse_gstin
from .confidence import cap_confidence

TOL_ROUNDING = Decimal("0.02")
TOL_SOFT = Decimal("1.00")
CONF_OK = .90
CONF_LOW = .70


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _val(field: Any) -> Any:
    return _get(field, "value")


def _dec(field: Any) -> Decimal | None:
    value = _val(field)
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


def _check(code: str, severity: str, passed: bool, fields: list[str], message: str, **kwargs: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "passed": passed, "field_paths": fields, "message": message, **kwargs}


def _money_check(code: str, a: Decimal | None, b: Decimal | None, paths: list[str]) -> list[dict[str, Any]]:
    if a is None or b is None:
        return []
    diff = abs(a - b)
    if diff <= TOL_ROUNDING:
        return [_check(code, "error", True, paths, "Values reconcile", difference=str(diff))]
    if diff <= TOL_SOFT:
        return [_check(f"{code}_ROUNDING", "warning", True, paths, "Values differ within soft rounding tolerance", difference=str(diff))]
    return [_check(code, "error", False, paths, "Values do not reconcile", expected=str(b), actual=str(a), difference=str(diff))]


def _plain(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return vars(obj)


def _new_check(data: dict[str, Any]) -> Any:
    try:
        from vyom.models import Check
        return Check(**data)
    except ImportError:
        return data


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def validate(rec: Any, *, repair: bool = True) -> Any:
    """Validate an InvoiceRecord without mutation when repair=False.

    The shared Pydantic contract is supplied by P1. This function deliberately
    operates on that interface by duck typing, allowing isolated P3 fixtures.
    """
    original = rec
    if hasattr(rec, "model_copy"):
        rec = rec.model_copy(deep=True)
    elif isinstance(rec, dict):
        import copy
        rec = copy.deepcopy(rec)
    repair_corrections: list[dict[str, Any]] = []
    repair_candidates: list[dict[str, Any]] = []
    if repair:
        try:
            from .repair import repair_amount_words, repair_gstin
            repair_corrections = repair_gstin(rec)
            repair_corrections += repair_amount_words(rec)
            repair_candidates = list(_get(_get(rec, "validation", {}), "repair_candidates", []) or [])
        except (ImportError, AttributeError, TypeError):
            repair_corrections = []
    invoice = _get(rec, "invoice", {})
    lines = _get(rec, "line_items", [])
    checks: list[dict[str, Any]] = []
    inv = {key: invoice.get(key) if isinstance(invoice, dict) else _get(invoice, key) for key in ("invoice_no", "invoice_date", "due_date", "document_type", "place_of_supply", "supplier_gstin", "buyer_gstin", "supplier_name", "buyer_name", "taxable_value", "discount", "cgst", "sgst", "igst", "cess", "total_tax", "round_off", "total_amount", "amount_in_words", "reverse_charge")}
    doc_type = str(_val(inv["document_type"]) or "tax_invoice")
    supplier = _val(inv["supplier_gstin"])
    buyer = _val(inv["buyer_gstin"])
    for party, value in (("supplier", supplier), ("buyer", buyer)):
        path = f"invoice.{party}_gstin"
        if value:
            if parse_gstin(str(value)) is None:
                checks.append(_check("GSTIN_FORMAT", "error", False, [path], f"{party.title()} GSTIN structure is invalid"))
                checks.append(_check("GSTIN_CHECKSUM", "error", False, [path], f"{party.title()} GSTIN checksum is invalid"))
            else:
                checks.append(_check("GSTIN_FORMAT", "error", True, [path], f"{party.title()} GSTIN structure is valid"))
                known_state = parse_gstin(str(value))["state_code"] in load_reference("state_codes.json")
                checks.append(_check("GSTIN_STATE_CODE", "error", known_state, [path], f"{party.title()} GSTIN state code"))
                checksum_ok = check_char(str(value)[:14]) == str(value)[-1]
                checks.append(_check("GSTIN_CHECKSUM", "error", checksum_ok, [path], f"{party.title()} GSTIN checksum"))
        elif party == "supplier" and doc_type not in {"bill_of_supply", "composition"}:
            checks.append(_check("SUPPLIER_GSTIN_MISSING", "error", False, [path], "Supplier GSTIN is required for tax invoices"))
        elif party == "buyer":
            checks.append(_check("BUYER_GSTIN_MISSING", "info", True, [path], "Buyer GSTIN is absent"))
    for field, code in (("invoice_no", "INVOICE_NUMBER_MISSING"), ("invoice_date", "DATE_MISSING")):
        if not _val(inv[field]):
            checks.append(_check(code, "error", False, [f"invoice.{field}"], f"{field} is required"))
    for field_name, field in inv.items():
        sources = _get(field, "sources", []) or []
        alternatives = _get(field, "alternatives", []) or []
        if "qr" in sources and alternatives and _val(field) is not None:
            checks.append(_check("QR_MISMATCH", "error", False, [f"invoice.{field_name}"], "QR value conflicts with another source"))
    if inv["invoice_no"] and len(str(_val(inv["invoice_no"]))) > 16:
        checks.append(_check("INVOICE_NUMBER_RULE46", "warning", True, ["invoice.invoice_no"], "Invoice number exceeds 16 characters"))
    if inv["due_date"] and inv["invoice_date"] and str(_val(inv["due_date"])) < str(_val(inv["invoice_date"])):
        checks.append(_check("DUE_BEFORE_INVOICE", "warning", True, ["invoice.due_date", "invoice.invoice_date"], "Due date precedes invoice date"))
    date_value = _val(inv["invoice_date"])
    if date_value:
        try:
            from datetime import date, timedelta
            parsed_date = date.fromisoformat(str(date_value))
            today = date.today()
            if parsed_date < date(2017, 7, 1):
                checks.append(_check("DATE_BEFORE_GST", "warning", True, ["invoice.invoice_date"], "Invoice date predates GST commencement"))
            if parsed_date > today + timedelta(days=1):
                checks.append(_check("DATE_IN_FUTURE", "warning", True, ["invoice.invoice_date"], "Invoice date is in the future"))
            if parsed_date < today.replace(year=today.year - 8):
                checks.append(_check("DATE_VERY_OLD", "warning", True, ["invoice.invoice_date"], "Invoice date is more than eight years old"))
        except ValueError:
            checks.append(_check("DATE_MISSING", "error", False, ["invoice.invoice_date"], "Invoice date is invalid"))
    if not _val(inv["place_of_supply"]):
        checks.append(_check("POS_MISSING", "info", True, ["invoice.place_of_supply"], "Place of supply is absent"))
    subtotal = _dec(inv["taxable_value"])
    amount = _dec(inv["total_amount"])
    if subtotal is not None and amount is not None:
        tax_total = sum((_dec(inv[k]) or Decimal(0) for k in ("cgst", "sgst", "igst", "cess")), Decimal(0))
        expected = subtotal - (_dec(inv["discount"]) or Decimal(0)) + tax_total + (_dec(inv["round_off"]) or Decimal(0))
        checks += _money_check("GRAND_TOTAL", amount, expected, ["invoice.total_amount", "invoice.taxable_value"])
        declared_tax = _dec(inv["total_tax"])
        if declared_tax is not None:
            checks += _money_check("TOTAL_TAX", declared_tax, tax_total, ["invoice.total_tax"])
    words_text = _val(inv["amount_in_words"])
    if words_text and amount is not None:
        from vyom.domain.words import parse_amount_words
        words_value, _ = parse_amount_words(str(words_text))
        if words_value is not None and words_value != amount:
            grand_passes = subtotal is not None and abs(amount - (subtotal - (_dec(inv["discount"]) or Decimal(0)) + sum((_dec(inv[k]) or Decimal(0) for k in ("cgst", "sgst", "igst", "cess")), Decimal(0)) + (_dec(inv["round_off"]) or Decimal(0)))) <= TOL_SOFT
            code = "WORDS_MISMATCH" if grand_passes else "WORDS_TOTAL"
            checks.append(_check(code, "warning" if grand_passes else "error", grand_passes, ["invoice.amount_in_words", "invoice.total_amount"], "Amount in words does not match the numeric total", expected=str(amount), actual=str(words_value)))
        elif words_value is None:
            checks.append(_check("WORDS_UNPARSEABLE", "warning", True, ["invoice.amount_in_words"], "Amount in words could not be parsed"))
    supplier_state = str(supplier or "")[:2]
    pos = str(_val(inv["place_of_supply"]) or "")[:2]
    c, s, i = (_dec(inv[k]) for k in ("cgst", "sgst", "igst"))
    if pos and supplier_state and any(v is not None and v > 0 for v in (c,s,i)):
        intra = pos == supplier_state
        mismatch = (intra and i is not None and i > TOL_ROUNDING) or (not intra and ((c is not None and c > TOL_ROUNDING) or (s is not None and s > TOL_ROUNDING)))
        checks.append(_check("TAX_TYPE_MISMATCH", "error", not mismatch, ["invoice.place_of_supply", "invoice.cgst", "invoice.sgst", "invoice.igst"], "Tax components do not match place of supply"))
    if doc_type in {"bill_of_supply", "composition"}:
        tax_present = any((_dec(inv[k]) or Decimal(0)) > TOL_ROUNDING for k in ("cgst", "sgst", "igst", "cess", "total_tax"))
        checks.append(_check("TAX_ON_BILL_OF_SUPPLY", "error", not tax_present, ["invoice.cgst", "invoice.sgst", "invoice.igst", "invoice.cess"], "Bill of supply should not carry GST"))
    if str(_val(inv["reverse_charge"])) == "true":
        checks.append(_check("REVERSE_CHARGE", "info", True, ["invoice.reverse_charge"], "Reverse charge is declared"))
    total_taxable_line = Decimal(0)
    have_lines = False
    for index, line in enumerate(lines):
        line = line if isinstance(line, dict) else _plain(line)
        path = f"line_items[{index}]"
        qty = _dec(line.get("quantity")); price = _dec(line.get("unit_price")); discount = _dec(line.get("discount")) or Decimal(0); taxable = _dec(line.get("taxable_value"))
        rate = _dec(line.get("tax_rate"))
        if rate is not None:
            known_rates = {Decimal(str(item)) for item in load_reference("gst_rates.yaml")["all_known"]}
            if rate not in known_rates:
                checks.append(_check("RATE_UNKNOWN", "error", False, [f"{path}.tax_rate"], "Rate is not in the configured GST rates"))
            else:
                regime = load_reference("gst_rates.yaml")["regime_change"]
                retired = {Decimal(str(item)) for item in regime["retired_rates"]}
                introduced = {Decimal(str(item)) for item in regime["introduced_rates"]}
                if date_value and ((rate in retired and str(date_value) >= regime["effective_date"]) or (rate in introduced and str(date_value) < regime["effective_date"])):
                    checks.append(_check("RATE_NOT_EFFECTIVE", "warning", True, [f"{path}.tax_rate"], "Rate falls outside the configured regime period"))
        if taxable is not None:
            total_taxable_line += taxable; have_lines = True
        if qty is not None and price is not None and taxable is not None:
            expected_line = qty * price - discount
            includes_tax = str(_val(invoice.get("price_includes_tax") if isinstance(invoice, dict) else _get(invoice, "price_includes_tax"))) == "true"
            if includes_tax and rate is not None:
                expected_line = expected_line * 100 / (100 + rate)
            checks += _money_check("LINE_TAXABLE", taxable, expected_line, [f"{path}.taxable_value"])
            for component in ("cgst", "sgst", "igst", "cess"):
                val = _dec(line.get(component))
                if val is not None and val < 0:
                    checks.append(_check("NEGATIVE_AMOUNT", "warning", True, [f"{path}.{component}"], "Negative tax amount"))
            if rate is not None:
                pos_code = str(_val(inv["place_of_supply"]) or "")[:2]
                intra = pos_code == str(supplier or "")[:2] and pos_code != "96"
                expected_component = (taxable * rate / Decimal(200)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if intra else (taxable * rate / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                expected_fields = ("cgst", "sgst") if intra else ("igst",)
                for component in ("cgst", "sgst", "igst"):
                    actual_component = _dec(line.get(component))
                    expected = expected_component if component in expected_fields else Decimal(0)
                    if actual_component is not None or expected > TOL_ROUNDING:
                        checks += _money_check(f"LINE_TAX_{component.upper()}", actual_component or Decimal(0), expected, [f"{path}.{component}"])
            parts = sum((_dec(line.get(k)) or Decimal(0) for k in ("cgst", "sgst", "igst", "cess")), Decimal(0))
            line_total = _dec(line.get("line_total"))
            if line_total is not None:
                checks += _money_check("LINE_TOTAL", line_total, taxable + parts, [f"{path}.line_total"])
    if have_lines and subtotal is not None:
        checks += _money_check("SUM_TAXABLE", total_taxable_line, subtotal, ["invoice.taxable_value"])
    if not lines and doc_type not in {"gstr_layout_c"}:
        checks.append(_check("LINE_ITEMS_MISSING", "warning", True, ["line_items"], "No line items were extracted"))

    critical: list[tuple[str, Any, float]] = [("invoice.invoice_no", inv["invoice_no"], 2), ("invoice.invoice_date", inv["invoice_date"], 2), ("invoice.taxable_value", inv["taxable_value"], 2), ("invoice.total_amount", inv["total_amount"], 3)]
    if doc_type not in {"bill_of_supply", "composition"}:
        critical.append(("invoice.supplier_gstin", inv["supplier_gstin"], 2))
    if buyer:
        critical.append(("invoice.buyer_gstin", inv["buyer_gstin"], 2))
    for j, line in enumerate(lines):
        critical.append((f"line_items[{j}].taxable_value", line.get("taxable_value") if isinstance(line, dict) else _get(line, "taxable_value"), 1))
    if not lines and doc_type != "gstr_layout_c":
        critical.append(("line_items[0].taxable_value", None, 1))
    confidence_sum = 0.0; weight_sum = 0.0; critical_missing = False
    for path, field, weight in critical:
        value = _val(field)
        conf = float(_get(field, "confidence", 0.0) or 0.0)
        if value is None:
            critical_missing = True
        confidence_sum += conf * weight; weight_sum += weight
    score_weights = {"error": 3, "warning": 1, "info": 0}
    denominator = sum(score_weights[c["severity"]] for c in checks)
    score = max(0.0, min(1.0, 1.0 - sum(score_weights[c["severity"]] for c in checks if not c["passed"]) / denominator)) if denominator else 1.0
    overall = (confidence_sum / weight_sum if weight_sum else 0.0) * score
    unresolved_error = any(c["severity"] == "error" and not c["passed"] for c in checks)
    warnings = any(c["severity"] == "warning" for c in checks)
    too_low = any(_val(field) is None or float(_get(field, "confidence", 0.0) or 0.0) < CONF_OK for _, field, _ in critical)
    status = "failed" if not any(_val(field) is not None for _, field, _ in critical) else "needs_review" if (critical_missing or too_low or unresolved_error or overall < CONF_LOW) else "valid_with_warnings" if warnings else "valid"
    if hasattr(rec, "model_copy"):
        from vyom.models import Check, Validation
        from vyom.models import Correction
        val_obj = Validation(status=status, score=score, checks=[Check(**c) for c in checks], corrections=[Correction(**c) for c in repair_corrections], repair_candidates=repair_candidates)
    else:
        val_obj = {"status": status, "score": score, "checks": checks, "corrections": repair_corrections, "repair_candidates": repair_candidates}
    _set(rec, "validation", val_obj)
    quality = _get(rec, "quality")
    if quality is not None:
        if isinstance(quality, dict): quality["overall_confidence"] = overall
        else: setattr(quality, "overall_confidence", overall)
    review = _get(rec, "review")
    if review is not None:
        if isinstance(review, dict): review["required"] = status != "valid"
        else: setattr(review, "required", status != "valid")
    for fields in (invoice,):
        pairs = fields.items() if isinstance(fields, dict) else ((name, _get(fields, name)) for name in vars(fields))
        for field_name, field in pairs:
            if field is None:
                continue
            value = _val(field)
            old = _get(field, "status", "missing")
            confidence = float(_get(field, "confidence", 0.0) or 0.0)
            note = str(_get(field, "note", "") or "")
            if field_name in {"supplier_gstin", "buyer_gstin"} and value:
                confidence = cap_confidence(confidence, gstin_valid=gstin_is_valid(str(value)))
            if field_name in {"invoice_date", "due_date"}:
                confidence = cap_confidence(confidence, ambiguous_date="DATE_AMBIGUOUS_DAY_FIRST" in note)
            new = old if old in {"corrected", "derived", "human_verified"} else ("missing" if value is None else "accepted" if confidence >= CONF_OK else "needs_review")
            if isinstance(field, dict): field["confidence"] = confidence; field["status"] = new
            else: field.confidence = confidence; field.status = new
    for line in lines:
        row = line if isinstance(line, dict) else _plain(line)
        for field in row.values():
            value = _val(field); old = _get(field, "status", "missing")
            confidence = float(_get(field, "confidence", 0.0) or 0.0)
            new = old if old in {"corrected", "derived", "human_verified"} else ("missing" if value is None else "accepted" if confidence >= CONF_OK else "needs_review")
            if isinstance(field, dict): field["status"] = new
            else: field.status = new
    return rec
