"""Rules-based invoice hypothesis extraction with token-grounded evidence."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from vyom.models import BBox, PageData, RawField, RawInvoice, Token

try:
    from rapidfuzz.fuzz import ratio as _fuzzy_ratio  # type: ignore[import-not-found]
except ImportError:  # small dependency-free fallback
    def _fuzzy_ratio(left: str, right: str) -> float:
        return 100 * SequenceMatcher(None, left, right).ratio()


_LABELS: dict[str, tuple[str, ...]] = {
    "invoice_no": ("invoice no", "invoice number", "inv no", "bill no", "document no"),
    "invoice_date": ("invoice date", "date of issue", "date", "dated", "bill date"),
    "due_date": ("due date", "payment due"),
    "purchase_order_no": ("purchase order no", "po no", "order no"),
    "place_of_supply": ("place of supply", "state of supply"),
    "supplier_name": ("supplier", "seller", "from", "m s", "vendor"),
    "supplier_address": ("supplier address", "seller address", "address"),
    "buyer_name": ("buyer", "client", "bill to", "billed to", "customer", "consignee", "ship to"),
    "buyer_address": ("buyer address", "billing address", "ship to address"),
    "supplier_gstin": ("supplier gstin", "seller gstin", "gstin", "gst no", "gst registration no", "tax id"),
    "buyer_gstin": ("buyer gstin", "bill to gstin", "customer gstin", "consignee gstin"),
    "reverse_charge": ("reverse charge", "reverse charge applicable"),
    "subtotal": ("subtotal", "sub total", "taxable value", "assessable value", "net worth"),
    "discount": ("discount", "less discount"),
    "taxable_value": ("taxable value", "total taxable value", "assessable value", "net worth"),
    "cgst": ("cgst", "central gst"), "sgst": ("sgst", "state gst"), "igst": ("igst", "integrated gst"),
    "cess": ("cess",), "total_tax": ("total tax", "gst total", "tax amount", "vat"),
    "round_off": ("round off", "rounding"),
    "total_amount": ("grand total", "net payable", "amount payable", "invoice total", "total amount", "gross worth", "total"),
    "amount_in_words": ("amount in words", "total in words", "rupees in words"),
}


def _load_label_synonyms() -> dict[str, tuple[str, ...]]:
    """Read P1's optional label map and retain defaults for absent fields."""
    path = Path(__file__).resolve().parents[2] / "reference" / "label_synonyms.yaml"
    if not path.is_file():
        return _LABELS
    try:
        import yaml  # type: ignore[import-not-found]
        supplied = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return _LABELS
    if not isinstance(supplied, dict):
        return _LABELS
    result = dict(_LABELS)
    for field, aliases in supplied.items():
        if field in result:
            values = [aliases] if isinstance(aliases, str) else aliases
            if isinstance(values, (list, tuple)):
                result[field] = tuple(dict.fromkeys((*result[field], *(str(value) for value in values if value))))
    return result


_LABELS = _load_label_synonyms()
_LINE_LABELS: dict[str, tuple[str, ...]] = {
    "line_no": ("sr no", "s no", "sl no", "line no", "item no", "no."),
    "description": ("description", "particulars", "item description", "product"),
    "hsn_sac": ("hsn", "hsn sac", "sac", "hsn code"),
    "quantity": ("qty", "quantity"), "unit": ("unit", "uqc", "um"),
    "unit_price": ("rate", "unit price", "price", "net price"), "discount": ("discount",),
    "taxable_value": ("taxable value", "taxable amount", "amount", "net worth"), "tax_rate": ("gst rate", "tax rate", "rate %", "vat [%]", "vat"),
    "cgst": ("cgst",), "sgst": ("sgst",), "igst": ("igst",), "cess": ("cess",),
    "line_total": ("total", "line total", "net amount", "amount", "gross worth"),
}
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
_CANDIDATE = re.compile(r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9 -]{12,17}[A-Z0-9](?![A-Z0-9])", re.IGNORECASE)
_DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_AMOUNT = re.compile(r"(?<![A-Z])(?:[₹$€£]\s*|INR\s*|Rs\.?\s*)?-?\d[\d,]*(?:\.\d{1,2})?(?:/-|=\d{2})?", re.IGNORECASE)
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PIN = re.compile(r"\b[1-9]\d{5}\b")
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", re.IGNORECASE)
_IRN = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
_TOTAL_PRIORITY = ("grand total", "gross worth", "net payable", "amount payable", "invoice total", "total amount", "total")


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _bbox(tokens: Iterable[Token], page: int) -> BBox:
    values = list(tokens)
    if not values:
        return BBox(page=page, x0=0, y0=0, x1=1, y1=1)
    return BBox(page=page, x0=min(t.box[0] for t in values), y0=min(t.box[1] for t in values),
                x1=max(t.box[2] for t in values), y1=max(t.box[3] for t in values))


def _field(value: str | None, page: int, tokens: list[Token], evidence: str, confidence: float = 0.80) -> RawField:
    value_tokens = _tokens_for_value(value, tokens)
    return RawField(value=value, confidence=confidence, source="rules", page=page,
                    bbox=_bbox(value_tokens or tokens, page), evidence_text=evidence[:1200])


def _tokens_for_value(value: str | None, tokens: list[Token]) -> list[Token]:
    """Narrow a line's evidence box to tokens that actually spell the hypothesis."""
    if not value or not tokens:
        return []
    wanted = _normal(value)
    matches = [token for token in tokens if _normal(token.text) and (_normal(token.text) in wanted or wanted in _normal(token.text))]
    if matches:
        return matches
    wanted_parts = set(wanted.split())
    return [token for token in tokens if wanted_parts.intersection(_normal(token.text).split())]


def _lines(pages: list[PageData]) -> list[tuple[int, str, list[Token]]]:
    result: list[tuple[int, str, list[Token]]] = []
    for page in pages:
        by_id: dict[int, list[Token]] = {}
        for token in page.tokens:
            by_id.setdefault(token.line_id, []).append(token)
        for line_id, tokens in sorted(by_id.items()):
            tokens.sort(key=lambda token: token.box[0])
            result.append((page.index, " ".join(token.text for token in tokens), tokens))
        # Digital layout lines may contain extracted table text not covered by tokens.
        if not page.tokens and page.layout_text:
            for line in page.layout_text.splitlines():
                if not line.startswith("| "):
                    left, separator, right = line.partition("|")
                    result.append((page.index, (right if separator else left).strip(), []))
    return result


def _label_value(line: str, label: str) -> str | None:
    label_pattern = r"m\s*/?\s*s" if _normal(label) == "m s" else re.escape(label)
    match = re.search(label_pattern + r"\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
    if not match:
        return None
    val = match.group(1).strip(" :-|\t")
    # Truncate before any subsequent field label on the same line
    for other_labels in _LABELS.values():
        for other in other_labels:
            if other.lower() != label.lower() and len(other) >= 3:
                idx = val.lower().find(other.lower())
                if idx > 0 and (val[idx - 1].isspace() or val[idx - 1] in ":-|"):
                    val = val[:idx].strip(" :-|\t")
    return val if val else None


def _find_anchored(text: str, field: str) -> tuple[str, str] | None:
    normalized = _normal(text)
    best: tuple[float, str, str] | None = None
    for label in _LABELS[field]:
        label_norm = _normal(label)
        if label_norm in normalized:
            # Brand headers often say "Supplier Services"; only treat Supplier/Seller/Vendor
            # as a label when it starts the line. This prevents the brand from becoming a name.
            if field == "supplier_name" and label_norm in {"supplier", "seller", "vendor"}:
                match = re.search(re.escape(label), text, re.IGNORECASE)
                if match and text[:match.start()].strip(" :-|\t"):
                    continue
            value = _label_value(text, label)
            if value:
                return value, label
        # Try matching short windows to support OCR label errors while guarding against broad labels.
        words = text.split()
        width = max(1, len(label_norm.split()))
        for start in range(max(1, len(words) - width + 1)):
            window = " ".join(words[start:start + width])
            score = _fuzzy_ratio(_normal(window), label_norm)
            if score >= 90 and (best is None or score > best[0]):
                remainder = " ".join(words[start + width:]).strip(" :-|\t")
                if remainder:
                    best = (score, remainder, label)
    return (best[1], best[2]) if best else None


def _table_rows(pages: list[PageData]) -> list[tuple[int, list[str], list[Token]]]:
    rows: list[tuple[int, list[str], list[Token]]] = []
    for page in pages:
        for text in page.layout_text.splitlines():
            if not (text.startswith("| ") and text.endswith(" |")):
                continue
            cells = [cell.strip() for cell in text.strip("| ").split("|")]
            if len(cells) >= 3:
                rows.append((page.index, cells, page.tokens))
    return rows


def extract_rules(pages: list[PageData]) -> RawInvoice:
    """Extract anchored headers, pattern candidates, total block, and basic rows."""
    result = RawInvoice(source="rules")
    lines = _lines(pages)
    for field, labels in _LABELS.items():
        matches = []
        for page_num, text, tokens in lines:
            found = _find_anchored(text, field)
            if found:
                value, label = found
                matches.append((page_num, value, tokens, text, label))
        if matches:
            page_num, value, tokens, text, label = matches[-1] if field in {"total_amount", "total_tax", "taxable_value", "amount_in_words"} else matches[0]
            result.invoice.setdefault(field, []).append(_field(value, page_num, tokens, text))

    full_text = "\n".join(text for _, text, _ in lines)
    all_tokens = [token for page in pages for token in page.tokens]
    # Pattern-only identity candidates retain the precise token evidence where available.
    compact = re.sub(r"(?<=[A-Z0-9])[\s-]+(?=[A-Z0-9])", "", full_text.upper())
    gstins = list(_GSTIN.finditer(compact))
    if gstins:
        occurrences: list[tuple[str, int]] = []
        for match in gstins:
            candidate = match.group(0)
            if candidate not in [item[0] for item in occurrences]:
                occurrences.append((candidate, match.start()))
        supplier = next((item for item in occurrences if re.search(r"supplier|seller|from|vendor", full_text[:max(1, item[1])], re.I)), occurrences[0])
        buyer = next((item for item in occurrences if re.search(r"buyer|bill to|customer|consignee|ship to", full_text[max(0, item[1]-120):item[1]+30], re.I)), None)
        if buyer is None and len(occurrences) > 1:
            buyer = occurrences[1]
        gst_token_groups = [t for t in all_tokens if _GSTIN.search(t.text.replace(" ", "").replace("-", ""))]
        if not result.invoice.get("supplier_gstin"):
            page_num = gst_token_groups[0].page if gst_token_groups else (pages[0].index if pages else 1)
            result.invoice["supplier_gstin"] = [_field(supplier[0], page_num, gst_token_groups[:1], supplier[0], 0.65)]
        if buyer and not result.invoice.get("buyer_gstin"):
            page_num = gst_token_groups[1].page if len(gst_token_groups) > 1 else (pages[0].index if pages else 1)
            result.invoice["buyer_gstin"] = [_field(buyer[0], page_num, gst_token_groups[1:2], buyer[0], 0.65)]
    else:
        for match in _CANDIDATE.finditer(full_text.upper()):
            candidate = re.sub(r"[\s-]", "", match.group(0))
            if len(candidate) in {14, 15, 16, 17}:
                field = "supplier_gstin" if "supplier_gstin" not in result.invoice else "buyer_gstin"
                page_num = pages[0].index if pages else 1
                result.invoice.setdefault(field, []).append(_field(candidate, page_num, [], match.group(0), 0.30))

    # Collect helpful raw pattern hypotheses only where an anchored field is absent.
    for field, pattern in (("invoice_date", _DATE),):
        if field not in result.invoice:
            for page_num, text, tokens in lines:
                match = pattern.search(text)
                if match:
                    result.invoice.setdefault(field, []).append(_field(match.group(0), page_num, tokens, text, 0.65)); break

    result.line_items = _extract_line_items(pages)
    # Metadata not part of the frozen contract stays attached as a note to related hypotheses.
    for field, regex in (("pan", _PAN), ("phone", _PHONE), ("email", _EMAIL), ("pincode", _PIN), ("ifsc", _IFSC), ("irn", _IRN)):
        matches = list(regex.finditer(full_text))
        if matches and field in {"irn"}:
            result.invoice.setdefault("invoice_no", []).append(_field(matches[0].group(0), pages[0].index if pages else 1, [], "IRN " + matches[0].group(0), 0.65))
    _extract_document_type(result, full_text, pages, all_tokens)
    _extract_flags(result, full_text, pages, all_tokens)
    _extract_totals(result, lines)

    # Merge hypotheses from trained layout & semantic invoice model
    try:
        from .model_extractor import extract_model_fields
        model_inv = extract_model_fields(pages)
        for field, field_hypotheses in model_inv.invoice.items():
            if field not in result.invoice or not result.invoice[field]:
                result.invoice[field] = field_hypotheses
            else:
                result.invoice[field].extend(field_hypotheses)
        if not result.line_items and model_inv.line_items:
            result.line_items = model_inv.line_items
    except Exception:
        pass

    return result


def _extract_line_items(pages: list[PageData]) -> list[dict[str, RawField]]:
    output: list[dict[str, RawField]] = []
    rows = _table_rows(pages)
    header_indices: dict[str, int] | None = None
    page_num = pages[0].index if pages else 1
    for current_page, cells, tokens in rows:
        mapped: dict[str, int] = {}
        for index, cell in enumerate(cells):
            ncell = _normal(cell)
            for field, labels in _LINE_LABELS.items():
                if any(ncell == _normal(label) or _normal(label) in ncell for label in labels):
                    mapped.setdefault(field, index)
        if len(mapped) >= 3:
            header_indices, page_num = mapped, current_page
            continue
        if not header_indices:
            continue
        joined = " ".join(cells).casefold()
        if any(marker in joined for marker in ("carried forward", "c/f", "b/f", "brought forward")):
            page_object = next((page for page in pages if page.index == current_page), None)
            if page_object is not None and "CARRY_FORWARD_ROW_IGNORED" not in page_object.flags:
                page_object.flags.append("CARRY_FORWARD_ROW_IGNORED")
            continue
        if any(label in joined for label in ("subtotal", "grand total", "net payable", "round off", "total amount")):
            continue
        if not any(re.search(r"\d", cell) for cell in cells) and output:
            continuation = next((cell for cell in cells if cell.strip()), "")
            if continuation and "description" in output[-1]:
                output[-1]["description"].value = (output[-1]["description"].value or "") + " " + continuation
                output[-1]["description"].evidence_text = (output[-1]["description"].evidence_text or "") + " " + continuation
            continue
        item: dict[str, RawField] = {}
        evidence = " | ".join(cells)
        for field, index in header_indices.items():
            if index < len(cells) and cells[index]:
                item[field] = _field(cells[index], current_page, tokens, evidence)
        if _is_useful_line_item(item):
            output.append(item)
    grid_pages = {page_num for page_num, _, _ in rows}
    for page in pages:
        if page.index in grid_pages:
            continue
        ocr_rows = _ocr_table_rows(page)
        if not any((row.get("description") and row["description"].value) for row in ocr_rows):
            ocr_rows = _layout_text_table_rows(page, _header_tax_rate(pages))
        output.extend(ocr_rows)
    return output


def _is_useful_line_item(item: dict[str, RawField]) -> bool:
    """Discard OCR fragments that contain numbers but no identifiable item row."""
    def has_value(key: str) -> bool:
        field = item.get(key)
        return bool(field and (field.value or "").strip())

    return (has_value("description") and any(has_value(key) for key in ("hsn_sac", "quantity", "taxable_value", "line_total"))) or (
        has_value("hsn_sac") and any(has_value(key) for key in ("quantity", "taxable_value", "line_total"))
    )


def _header_tax_rate(pages: list[PageData]) -> float:
    totals: dict[str, float] = {}
    for page in pages:
        for line in page.layout_text.splitlines():
            folded = line.casefold()
            for field, label in (("taxable", "taxable value"), ("cgst", "cgst"), ("sgst", "sgst"), ("igst", "igst"), ("cess", "cess")):
                if label in folded:
                    numbers = re.findall(r"\d[\d,]*(?:\.\d{1,2})?", line)
                    if numbers:
                        totals[field] = float(numbers[-1].replace(",", ""))
    taxable = totals.get("taxable", 0)
    tax = sum(totals.get(key, 0) for key in ("cgst", "sgst", "igst", "cess"))
    return round(tax * 100 / taxable, 2) if taxable else 0.0


def _layout_text_table_rows(page: PageData, tax_rate: float) -> list[dict[str, RawField]]:
    """Recover item rows from OCR's ordered layout lines when cell grouping fails."""
    output: list[dict[str, RawField]] = []
    row_pattern = re.compile(r"^\s*(\d{1,3})\s+(.+?)\s+(\d{4,8})\s+((?:\d[\d,]*(?:\.\d{1,2})?\s*){2,4})$")
    number_pattern = re.compile(r"\d[\d,]*(?:\.\d{1,2})?")
    for raw_line in page.layout_text.splitlines():
        _prefix, separator, text = raw_line.partition("|")
        if not separator:
            continue
        text = text.strip()
        match = row_pattern.match(text)
        if not match:
            continue
        line_no, description, hsn, raw_values = match.groups()
        values = [float(value.replace(",", "")) for value in number_pattern.findall(raw_values)]
        if len(values) < 2 or not description.strip():
            continue
        if len(values) >= 3:
            quantity, unit_price, taxable = values[-3:]
        else:
            quantity, unit_price, taxable = 1.0, values[-2], values[-1]
        if quantity <= 0 or taxable <= 0:
            continue
        line_tokens = [token for token in page.tokens if hsn in token.text or description.split()[0].casefold() in token.text.casefold()]
        evidence = text
        item = {
            "line_no": _field(line_no, page.index, line_tokens, evidence),
            "description": _field(description.strip(), page.index, line_tokens, evidence),
            "hsn_sac": _field(hsn, page.index, line_tokens, evidence),
            "quantity": _field(str(quantity).rstrip("0").rstrip(".") if quantity % 1 else str(int(quantity)), page.index, line_tokens, evidence),
            "unit_price": _field(f"{unit_price:.2f}", page.index, line_tokens, evidence),
            "taxable_value": _field(f"{taxable:.2f}", page.index, line_tokens, evidence),
        }
        if tax_rate > 0:
            item["tax_rate"] = _field(f"{tax_rate:g}", page.index, line_tokens, evidence, 0.60)
            item["line_total"] = _field(f"{taxable * (1 + tax_rate / 100):.2f}", page.index, line_tokens, evidence, 0.60)
        output.append(item)
    return output


def _ocr_table_rows(page: PageData) -> list[dict[str, RawField]]:
    """Infer OCR table columns from printed header token centers and row baselines."""
    by_line: dict[int, list[Token]] = {}
    for token in page.tokens:
        if token.source != "pdf_text":
            by_line.setdefault(token.line_id, []).append(token)
    ordered_lines = sorted(by_line.values(), key=lambda line: min(token.box[1] for token in line))
    header: dict[str, float] | None = None
    header_y = 0.0
    aliases = {
        "line_no": ("sr", "s.no", "sl", "no"), "description": ("description", "particulars", "item"),
        "hsn_sac": ("hsn", "sac"), "quantity": ("qty", "quantity"), "unit_price": ("rate", "price"),
        "taxable_value": ("taxable", "amount"), "tax_rate": ("gst%", "tax%"),
        "cgst": ("cgst",), "sgst": ("sgst",), "igst": ("igst",), "cess": ("cess",), "line_total": ("total",),
    }
    header_index = -1
    for line_index, line in enumerate(ordered_lines):
        mapped: dict[str, float] = {}
        for token in line:
            text = _normal(token.text)
            for field, labels in aliases.items():
                if text in {_normal(label) for label in labels}:
                    mapped.setdefault(field, (token.box[0] + token.box[2]) / 2)
        if len(mapped) >= 3:
            header, header_index = mapped, line_index
            header_y = max(token.box[3] for token in line)
            break
    if not header:
        return []
    centers = sorted(set(header.values()))
    bounds = [-1.0] + [(left + right) / 2 for left, right in zip(centers, centers[1:])] + [2.0]
    field_bounds: dict[str, tuple[float, float]] = {}
    for field, center in header.items():
        position = centers.index(center)
        field_bounds[field] = (bounds[position], bounds[position + 1])

    output: list[dict[str, RawField]] = []
    for line in ordered_lines[header_index + 1:]:
        if not line or min(token.box[1] for token in line) <= header_y:
            continue
        text = " ".join(token.text for token in sorted(line, key=lambda token: token.box[0]))
        folded = text.casefold()
        if any(label in folded for label in ("subtotal", "grand total", "net payable", "round off", "total amount", "carried forward", "brought forward", "c/f", "b/f")):
            continue
        cells: dict[str, list[Token]] = {field: [] for field in field_bounds}
        for token in line:
            center = (token.box[0] + token.box[2]) / 2
            field = next((name for name, (left, right) in field_bounds.items() if left <= center < right), None)
            if field:
                cells[field].append(token)
        if not any(re.search(r"\d", token.text) for token in line) and output:
            continuation = " ".join(token.text for token in sorted(line, key=lambda token: token.box[0]))
            if continuation and "description" in output[-1]:
                output[-1]["description"].value = (output[-1]["description"].value or "") + " " + continuation
                output[-1]["description"].evidence_text = (output[-1]["description"].evidence_text or "") + " " + continuation
            continue
        item: dict[str, RawField] = {}
        evidence = text
        for field, field_tokens in cells.items():
            if field_tokens:
                item[field] = _field(" ".join(token.text for token in sorted(field_tokens, key=lambda token: token.box[0])), page.index, field_tokens, evidence)
        if _is_useful_line_item(item):
            output.append(item)
    return output


def _extract_totals(result: RawInvoice, lines: list[tuple[int, str, list[Token]]]) -> None:
    choices: list[tuple[int, int, int, str, str, list[Token]]] = []
    for line_index, (page_num, text, tokens) in enumerate(lines):
        low = text.casefold()
        for priority, label in enumerate(_TOTAL_PRIORITY):
            if label in low:
                nums = list(_AMOUNT.finditer(text))
                if nums:
                    raw = nums[-1].group(0).strip()
                    choices.append((priority, page_num, line_index, label, raw, tokens))
                break
        for field, labels in (("cgst", ("cgst",)), ("sgst", ("sgst",)), ("igst", ("igst",)), ("cess", ("cess",)), ("round_off", ("round off",)), ("taxable_value", ("taxable value",))):
            if any(label in low for label in labels):
                nums = list(_AMOUNT.finditer(text))
                if nums:
                    value = nums[-1].group(0).strip()
                    result.invoice.setdefault(field, []).append(_field(value, page_num, tokens, text))
    if choices:
        priority, page_num, _line_index, label, value, tokens = sorted(choices, key=lambda item: (item[0], -item[2]))[0]
        result.invoice["total_amount"] = [_field(value, page_num, tokens, f"{label}: {value}")]


def _extract_document_type(result: RawInvoice, text: str, pages: list[PageData], tokens: list[Token]) -> None:
    folded = text.casefold()
    value = None
    if "composition taxable person" in folded:
        value = "bill_of_supply"
    else:
        if "invoice cum bill of supply" in folded and "tax" not in folded:
            value = "bill_of_supply"
        for phrase, kind in (("credit note", "credit_note"), ("debit note", "debit_note"), ("proforma invoice", "proforma_invoice"), ("bill of supply", "bill_of_supply"), ("invoice cum bill of supply", "bill_of_supply"), ("export invoice", "export_invoice"), ("cash memo", "cash_memo"), ("retail invoice", "retail_invoice"), ("tax invoice", "tax_invoice")):
            if value:
                break
            if phrase in folded:
                value = kind; break
    if value:
        page_num = pages[0].index if pages else 1
        result.invoice.setdefault("document_type", []).append(_field(value, page_num, tokens, value, 0.65))


def _extract_flags(result: RawInvoice, text: str, pages: list[PageData], tokens: list[Token]) -> None:
    folded = text.casefold()
    page_num = pages[0].index if pages else 1
    if "reverse charge" in folded:
        value = "no" if re.search(r"reverse\s+charge\s*[:\-]?\s*(?:no|n|not\s+applicable|\[\s*\])", folded) else "yes"
        result.invoice.setdefault("reverse_charge", []).append(_field(value, page_num, tokens, "reverse charge", 0.65))
    if any(phrase in folded for phrase in ("inclusive of gst", "incl. of all taxes", "mrp inclusive")):
        result.invoice.setdefault("price_includes_tax", []).append(_field("true", page_num, tokens, "tax-inclusive phrase", 0.65))
    original_ref = re.search(r"original\s+invoice(?:\s+(?:ref|no|number))?\s*[:#\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,})", text, re.IGNORECASE)
    if original_ref:
        result.invoice.setdefault("purchase_order_no", []).append(_field(original_ref.group(1), page_num, tokens, f"Original invoice reference: {original_ref.group(1)}", 0.65))
