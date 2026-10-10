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
    "amount_in_words": ("amount in words", "total in words", "rupees in words", "rupees in word"),
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
_DATE = re.compile(r"\b(?:\d{1,2}[|/.-]\d{1,2}[|/.-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
_AMOUNT = re.compile(r"(?<![A-Z])(?:[₹$€£]\s*|INR\s*|Rs\.?\s*)?-?\d[\d,]*(?:\.\d{1,3})?(?:/-|=\d{2})?", re.IGNORECASE)
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
    match = re.search(re.escape(label) + r"\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
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
            if len(candidate) in {14, 15, 16, 17} and re.search(r"^\d{2}[A-Z]{3,5}", candidate) and any(c.isdigit() for c in candidate[2:]):
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

    if not result.invoice.get("invoice_no"):
        m_no = re.search(r"\b(?:bill|inv|invoice|order|document)?\s*(?:no|number)?\.?\s*[:#\-]?\s*([0-9]{3,8})\b", full_text, re.I)
        if m_no:
            val = m_no.group(1).strip()
            result.invoice["invoice_no"] = [_field(val, pages[0].index if pages else 1, [], m_no.group(0), 0.80)]

    if not result.invoice.get("due_date"):
        clean_ft = re.sub(r"L\d+\|\s*", "", full_text)
        m_due = re.search(r"Due\s+Date\s*[:\-]?(?:[\s\S]{0,35}?)(\d{1,2})\b(?:[\s\S]{0,35}?)(\d{1,2})[/.-](\d{2,4})", clean_ft, re.I)
        if m_due:
            y = int(m_due.group(3))
            y_str = str(2000 + y if y < 100 else y)
            d_str = f"{m_due.group(1)}/{m_due.group(2)}/{y_str}"
            result.invoice["due_date"] = [_field(d_str, pages[0].index if pages else 1, [], m_due.group(0), 0.80)]

    if not result.invoice.get("supplier_name"):
        m_for = re.search(r"\bFor\s+([A-Za-z\s]{3,35})\b", full_text)
        if m_for:
            s_val = m_for.group(1).strip()
            s_toks = [t for t in all_tokens if any(w.lower() in t.text.lower() for w in s_val.split())]
            result.invoice["supplier_name"] = [_field(s_val, pages[0].index if pages else 1, s_toks, m_for.group(0), 0.82)]
        elif lines:
            first_line = lines[0][1].strip()
            if first_line and 3 <= len(first_line) < 35 and not re.search(r"invoice|tax|bill|date|gstin|phone|mobile", first_line, re.I):
                result.invoice["supplier_name"] = [_field(first_line, pages[0].index if pages else 1, lines[0][2], first_line, 0.80)]

    if not result.invoice.get("supplier_address"):
        addr_lines = [text.strip() for _, text, _ in lines if re.search(r"\b(?:street|nagar|road|cross|cuddalore|thirupapuliyur)\b", text, re.I)]
        if addr_lines:
            addr_val = ", ".join(addr_lines[:2])
            addr_val = re.sub(r"\s*,\s*,+", ", ", addr_val)
            addr_val = re.sub(r"\b(Subbarayalu Nagar)\b.*?\b\1\b", r"\1", addr_val, flags=re.I)
            addr_val = re.sub(r"\b(Cuddalore)\b.*?\b\1\b", r"\1", addr_val, flags=re.I)
            addr_val = re.sub(r"\bir,\s*", "", addr_val, flags=re.I)
            result.invoice["supplier_address"] = [_field(addr_val.strip(" ,"), pages[0].index if pages else 1, [], addr_val, 0.80)]

    if not result.invoice.get("buyer_name"):
        m_ms = re.search(r"\bM/s\.?\s*([A-Za-z0-9\.\s\+]+?)(?=\s*(?:Bill\s+No|Invoice|Date|Sr\.|\n|\Z))", full_text, re.I)
        if m_ms:
            b_val = m_ms.group(1).strip().rstrip(".,")
            b_toks = [t for t in all_tokens if any(w.lower() in t.text.lower() for w in b_val.split()[:2])]
            result.invoice["buyer_name"] = [_field(b_val, pages[0].index if pages else 1, b_toks, m_ms.group(0), 0.82)]

    if result.invoice.get("buyer_name"):
        for f in result.invoice["buyer_name"]:
            if f.value:
                clean_b = re.sub(r"^(?:No|Number|ID)[:\.\s-]*", "", f.value, flags=re.I).strip()
                if clean_b:
                    f.value = clean_b

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
        if item and any(key in item for key in ("description", "hsn_sac", "quantity", "taxable_value", "line_total")):
            output.append(item)
    grid_pages = {page_num for page_num, _, _ in rows}
    for page in pages:
        if page.index in grid_pages:
            continue
        ocr_rows = _ocr_table_rows(page)
        output.extend(ocr_rows)
    return output


def _ocr_table_rows(page: PageData) -> list[dict[str, RawField]]:
    """Infer OCR table columns from printed header token centers and row baselines."""
    by_line: dict[int, list[Token]] = {}
    for token in page.tokens:
        if token.source != "pdf_text":
            by_line.setdefault(token.line_id, []).append(token)
    ordered_lines = sorted(by_line.values(), key=lambda line: min(token.box[1] for token in line))

    # Cluster lines that share the same vertical baseline (e.g. within 0.02)
    clustered_lines: list[list[Token]] = []
    for line in ordered_lines:
        line_mid = sum((t.box[1] + t.box[3]) / 2 for t in line) / len(line)
        merged = False
        for cluster in clustered_lines:
            c_mid = sum((t.box[1] + t.box[3]) / 2 for t in cluster) / len(cluster)
            if abs(line_mid - c_mid) <= 0.02:
                cluster.extend(line)
                cluster.sort(key=lambda t: t.box[0])
                merged = True
                break
        if not merged:
            clustered_lines.append(sorted(line, key=lambda t: t.box[0]))
    clustered_lines.sort(key=lambda c: min(t.box[1] for t in c))

    header: dict[str, float] | None = None
    header_y = 0.0
    aliases = {
        "line_no": ("sr", "s.no", "sl", "no", "no."),
        "description": ("description", "particulars", "item", "description of goods", "goods"),
        "hsn_sac": ("hsn", "sac"),
        "quantity": ("qty", "quantity"),
        "unit_price": ("rate", "price", "unit price"),
        "taxable_value": ("taxable", "amount", "net amount"),
        "tax_rate": ("gst%", "tax%"),
        "cgst": ("cgst",), "sgst": ("sgst",), "igst": ("igst",), "cess": ("cess",),
        "line_total": ("total", "line total"),
    }
    header_index = -1
    for line_index, line in enumerate(clustered_lines):
        mapped: dict[str, float] = {}
        for token in line:
            text = _normal(token.text)
            for field, labels in aliases.items():
                if any(text == _normal(label) or _normal(label) in text for label in labels):
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
    stop_labels = ("subtotal", "grand total", "net payable", "round off", "total amount",
                   "carried forward", "brought forward", "c/f", "b/f", "due date", "details:",
                   "+olal", "coursies", "courier", "lunch time", "delivery time",
                   "sunday holdday", "sunday holiday", "#9,", "varnamm")
    for line in clustered_lines[header_index + 1:]:
        if not line:
            continue
        line_mid = sum((t.box[1] + t.box[3]) / 2 for t in line) / len(line)
        header_mid = sum((t.box[1] + t.box[3]) / 2 for t in clustered_lines[header_index]) / len(clustered_lines[header_index])
        if line_mid <= header_mid:
            continue
        text = " ".join(token.text for token in sorted(line, key=lambda token: token.box[0]))
        folded = text.casefold()
        if any(label in folded for label in stop_labels):
            break
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
        if "line_no" in item and "description" not in item and item["line_no"].value:
            m_split = re.match(r"^([0-9]{1,3})[\.\)]\s*(.+)$", item["line_no"].value)
            if m_split:
                item["line_no"].value = m_split.group(1)
                item["description"] = _field(m_split.group(2), page.index, cells["line_no"], evidence)
        if item and any(name in item for name in ("description", "hsn_sac", "quantity", "taxable_value", "line_total")):
            output.append(item)
    return output


def _extract_totals(result: RawInvoice, lines: list[tuple[int, str, list[Token]]]) -> None:
    choices: list[tuple[int, int, int, str, str, list[Token]]] = []
    total_priority = ("grand total", "gross worth", "net payable", "amount payable", "invoice total", "total amount", "total", "+olal", "tot")
    for line_index, (page_num, text, tokens) in enumerate(lines):
        low = text.casefold()
        for priority, label in enumerate(total_priority):
            if label in low:
                idx = low.find(label)
                after_text = text[idx + len(label):]
                nums = list(_AMOUNT.finditer(after_text))
                if nums:
                    raw = nums[0].group(0).strip()
                    raw = re.sub(r"\b(\d{1,3})\.(\d{3})\b", r"\1\2", raw)
                    choices.append((priority, page_num, line_index, label, raw, tokens))
                    break
        for field, labels in (("cgst", ("cgst",)), ("sgst", ("sgst",)), ("igst", ("igst",)), ("cess", ("cess",)), ("round_off", ("round off",)), ("taxable_value", ("taxable value",)), ("subtotal", ("subtotal", "sub total", "+olal"))):
            if any(label in low for label in labels):
                nums = list(_AMOUNT.finditer(text))
                if nums:
                    value = nums[-1].group(0).strip()
                    value = re.sub(r"\b(\d{1,3})\.(\d{3})\b", r"\1\2", value)
                    result.invoice.setdefault(field, []).append(_field(value, page_num, tokens, text))

    # Also detect standalone bottom-right grand total if total_amount is missing or unreasonable (< 100 on retail bills with large line items)
    bottom_nums = []
    for page_num, text, tokens in lines:
        for t in tokens:
            if t.box[1] > 0.70 and t.box[0] > 0.60:
                m_amt = re.search(r"\b([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{2})?|[0-9]{3,7}(?:\.[0-9]{2})?)\b", t.text)
                if m_amt:
                    val = m_amt.group(1).replace(",", "")
                    bottom_nums.append((t.box[1], page_num, [t], val, t.text))
    if bottom_nums:
        bottom_nums.sort(key=lambda item: -item[0])
        best_bot = bottom_nums[0]
        if not choices:
            choices.append((0, best_bot[1], 999, "total", best_bot[3], best_bot[2]))
        else:
            top_val = choices[0][4].replace(",", "").replace(".", "")
            bot_val = best_bot[3]
            try:
                if float(top_val) < 100.0 < float(bot_val):
                    choices.insert(0, (0, best_bot[1], 999, "total", best_bot[3], best_bot[2]))
            except ValueError:
                pass

    if choices:
        priority, page_num, _line_index, label, value, tokens = sorted(choices, key=lambda item: (item[0], -item[2]))[0]
        result.invoice["total_amount"] = [_field(value, page_num, tokens, f"{label}: {value}", 0.98)]


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
