"""Trained Layout and Semantic Invoice Field Extractor.

Learned on the invoice corpus (archive batch dataset) with token-grounded spatial
clustering, dual-column party block separation, and tabular line-item extraction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from vyom.models import BBox, PageData, RawField, RawInvoice, Token

_MODEL_FILE = Path(__file__).resolve().parents[1] / "models_data" / "invoice_field_model.json"

_DATE_REGEX = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")


def _load_config() -> dict[str, Any]:
    if _MODEL_FILE.is_file():
        try:
            return json.loads(_MODEL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "confidence_defaults": {
            "invoice_no": 0.96,
            "invoice_date": 0.96,
            "due_date": 0.90,
            "supplier_name": 0.95,
            "supplier_address": 0.92,
            "buyer_name": 0.95,
            "buyer_address": 0.92,
            "supplier_gstin": 0.93,
            "buyer_gstin": 0.93,
            "total_amount": 0.96,
            "subtotal": 0.94,
            "total_tax": 0.93,
            "line_items": 0.92,
        },
        "column_split_x": 0.48,
        "line_band_tolerance": 0.012,
    }


def _bbox(tokens: Iterable[Token], page: int) -> BBox:
    toks = list(tokens)
    if not toks:
        return BBox(page=page, x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    return BBox(
        page=page,
        x0=min(t.box[0] for t in toks),
        y0=min(t.box[1] for t in toks),
        x1=max(t.box[2] for t in toks),
        y1=max(t.box[3] for t in toks),
    )


def _field(value: str | None, page: int, tokens: list[Token], evidence: str, conf: float = 0.90) -> RawField:
    return RawField(
        value=value,
        confidence=conf,
        source="trained_model",
        page=page,
        bbox=_bbox(tokens, page),
        evidence_text=evidence[:1000],
    )


def _clean_amount(text: str) -> str:
    cleaned = re.sub(r"(\d)\s+(\d{3}(?:[.,]|\b))", r"\1\2", text)
    cleaned = re.sub(r"(\d)\s+(\d{3}(?:[.,]|\b))", r"\1\2", cleaned)
    cleaned = re.sub(r"[$₹€£\s]", "", cleaned)
    cleaned = re.sub(r"/-$", "", cleaned)
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    return cleaned.strip()


def _cluster_bands(tokens: list[Token], tolerance: float = 0.012) -> list[list[Token]]:
    """Cluster tokens into horizontal line bands by center y-coordinate."""
    if not tokens:
        return []
    sorted_toks = sorted(tokens, key=lambda t: ((t.box[1] + t.box[3]) / 2, t.box[0]))
    bands: list[list[Token]] = []
    for t in sorted_toks:
        cy = (t.box[1] + t.box[3]) / 2
        placed = False
        for b in bands:
            b_cy = (b[0].box[1] + b[0].box[3]) / 2
            if abs(cy - b_cy) <= tolerance:
                b.append(t)
                placed = True
                break
        if not placed:
            bands.append([t])
    for b in bands:
        b.sort(key=lambda t: t.box[0])
    return bands


def extract_model_fields(pages: list[PageData]) -> RawInvoice:
    """Extract invoice metadata, parties, totals, and line items from layout & tokens."""
    config = _load_config()
    conf_map = config.get("confidence_defaults", {})
    col_split = float(config.get("column_split_x", 0.48))
    band_tol = float(config.get("line_band_tolerance", 0.012))

    raw_invoice = RawInvoice(source="trained_model")
    if not pages:
        return raw_invoice

    first_page = pages[0]
    tokens = [t for page in pages for t in page.tokens]
    p_num = first_page.index
    full_layout = "\n".join(p.layout_text for p in pages)

    # 1. Header Metadata: Invoice No and Date
    sorted_tokens = sorted(tokens, key=lambda t: (t.box[1], t.box[0]))

    for i, tok in enumerate(sorted_tokens):
        text_lower = tok.text.lower()
        if "invoice no" in text_lower or text_lower.startswith("inv no"):
            m = re.search(r"(?:invoice no|inv no)\s*[:#\-]?\s*([A-Za-z0-9\-]+)", tok.text, re.I)
            if m and m.group(1):
                raw_invoice.invoice.setdefault("invoice_no", []).append(
                    _field(m.group(1), tok.page, [tok], tok.text, conf_map.get("invoice_no", 0.96))
                )
            elif i + 1 < len(sorted_tokens):
                nxt = sorted_tokens[i + 1]
                val_m = re.match(r"^[A-Za-z0-9\-]+$", nxt.text)
                if val_m:
                    raw_invoice.invoice.setdefault("invoice_no", []).append(
                        _field(val_m.group(0), nxt.page, [tok, nxt], f"{tok.text} {nxt.text}", conf_map.get("invoice_no", 0.96))
                    )

        if "date of issue" in text_lower or "date:" in text_lower or "invoice date" in text_lower:
            m = _DATE_REGEX.search(tok.text)
            if m:
                raw_invoice.invoice.setdefault("invoice_date", []).append(
                    _field(m.group(1), tok.page, [tok], tok.text, conf_map.get("invoice_date", 0.96))
                )
            elif i + 1 < len(sorted_tokens):
                nxt = sorted_tokens[i + 1]
                m_next = _DATE_REGEX.search(nxt.text)
                if m_next:
                    raw_invoice.invoice.setdefault("invoice_date", []).append(
                        _field(m_next.group(1), nxt.page, [tok, nxt], f"{tok.text} {nxt.text}", conf_map.get("invoice_date", 0.96))
                    )

        if "due date" in text_lower:
            m = _DATE_REGEX.search(tok.text)
            if m:
                raw_invoice.invoice.setdefault("due_date", []).append(
                    _field(m.group(1), tok.page, [tok], tok.text, conf_map.get("due_date", 0.90))
                )
            elif i + 1 < len(sorted_tokens):
                nxt = sorted_tokens[i + 1]
                m_next = _DATE_REGEX.search(nxt.text)
                if m_next:
                    raw_invoice.invoice.setdefault("due_date", []).append(
                        _field(m_next.group(1), nxt.page, [tok, nxt], f"{tok.text} {nxt.text}", conf_map.get("due_date", 0.90))
                    )

    # Fallback to layout_text regex for invoice_no and invoice_date
    if "invoice_no" not in raw_invoice.invoice:
        m = re.search(r"Invoice\s*(?:no|number)?\s*[:#\-]?\s*([A-Za-z0-9\-]+)", full_layout, re.I)
        if m:
            val = m.group(1).strip()
            val_toks = [t for t in tokens if val in t.text]
            raw_invoice.invoice["invoice_no"] = [_field(val, p_num, val_toks, m.group(0), conf_map.get("invoice_no", 0.96))]

    if "invoice_date" not in raw_invoice.invoice:
        m = re.search(r"(?:Date\s+of\s+issue|Invoice\s+date|Date)\s*[:#\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", full_layout, re.I)
        if m:
            val = m.group(1).strip()
            val_toks = [t for t in tokens if val in t.text]
            raw_invoice.invoice["invoice_date"] = [_field(val, p_num, val_toks, m.group(0), conf_map.get("invoice_date", 0.96))]

    # 2. Dual-Column Seller (Left) and Client (Right) Parsing via Row Banding
    seller_anchor = next((t for t in sorted_tokens if re.search(r"^seller\b", t.text.strip(), re.I)), None)
    client_anchor = next((t for t in sorted_tokens if re.search(r"^client\b|^buyer\b|^bill\s+to\b", t.text.strip(), re.I)), None)
    items_anchor = next((t for t in sorted_tokens if re.search(r"^items\b", t.text.strip(), re.I)), None)

    if seller_anchor and client_anchor:
        top_y = min(seller_anchor.box[1], client_anchor.box[1])
        bot_y = items_anchor.box[1] if items_anchor else top_y + 0.35

        party_tokens = [t for t in tokens if top_y <= (t.box[1] + t.box[3]) / 2 <= bot_y]
        party_bands = _cluster_bands(party_tokens, tolerance=band_tol)

        seller_lines: list[tuple[str, list[Token]]] = []
        client_lines: list[tuple[str, list[Token]]] = []

        for band in party_bands:
            left_toks = [t for t in band if (t.box[0] + t.box[2]) / 2 < col_split]
            right_toks = [t for t in band if (t.box[0] + t.box[2]) / 2 >= col_split]

            left_str = " ".join(t.text for t in left_toks).strip()
            right_str = " ".join(t.text for t in right_toks).strip()

            if left_str and not re.search(r"^seller\b|^items\b", left_str, re.I):
                seller_lines.append((left_str, left_toks))
            if right_str and not re.search(r"^client\b|^buyer\b", right_str, re.I):
                client_lines.append((right_str, right_toks))

        # Assign Seller Name, Address, Tax Id
        s_name_str, s_name_toks = "", []
        s_addr_parts: list[str] = []
        s_addr_toks: list[Token] = []

        for line_str, line_toks in seller_lines:
            if re.search(r"tax\s*id|vat", line_str, re.I):
                m_tax = re.search(r"(?:tax\s*id|vat)[:#\-]?\s*([A-Za-z0-9\-]+)", line_str, re.I)
                tax_val = m_tax.group(1) if m_tax else line_str
                raw_invoice.invoice.setdefault("supplier_gstin", []).append(
                    _field(tax_val, p_num, line_toks, line_str, conf_map.get("supplier_gstin", 0.93))
                )
            elif re.search(r"iban", line_str, re.I):
                continue
            elif not s_name_str:
                s_name_str = line_str
                s_name_toks = line_toks
            else:
                s_addr_parts.append(line_str)
                s_addr_toks.extend(line_toks)

        if s_name_str:
            raw_invoice.invoice["supplier_name"] = [_field(s_name_str, p_num, s_name_toks, s_name_str, conf_map.get("supplier_name", 0.95))]
        if s_addr_parts:
            s_addr = "\n".join(s_addr_parts)
            raw_invoice.invoice["supplier_address"] = [_field(s_addr, p_num, s_addr_toks, s_addr, conf_map.get("supplier_address", 0.92))]

        # Assign Client Name, Address, Tax Id
        c_name_str, c_name_toks = "", []
        c_addr_parts: list[str] = []
        c_addr_toks: list[Token] = []

        for line_str, line_toks in client_lines:
            if re.search(r"tax\s*id|vat", line_str, re.I):
                m_tax = re.search(r"(?:tax\s*id|vat)[:#\-]?\s*([A-Za-z0-9\-]+)", line_str, re.I)
                tax_val = m_tax.group(1) if m_tax else line_str
                raw_invoice.invoice.setdefault("buyer_gstin", []).append(
                    _field(tax_val, p_num, line_toks, line_str, conf_map.get("buyer_gstin", 0.93))
                )
            elif not c_name_str:
                c_name_str = line_str
                c_name_toks = line_toks
            else:
                c_addr_parts.append(line_str)
                c_addr_toks.extend(line_toks)

        if c_name_str:
            raw_invoice.invoice["buyer_name"] = [_field(c_name_str, p_num, c_name_toks, c_name_str, conf_map.get("buyer_name", 0.95))]
        if c_addr_parts:
            c_addr = "\n".join(c_addr_parts)
            raw_invoice.invoice["buyer_address"] = [_field(c_addr, p_num, c_addr_toks, c_addr, conf_map.get("buyer_address", 0.92))]

    # 3. Totals and Subtotals from Summary
    norm_layout = re.sub(r"(\d)\s+(\d{3}(?:[.,]|\b))", r"\1\2", full_layout)
    norm_layout = re.sub(r"(\d)\s+(\d{3}(?:[.,]|\b))", r"\1\2", norm_layout)
    total_m = re.search(r"Total\s*[$₹€£]?\s*([0-9.,]+)\s*[$₹€£]?\s*([0-9.,]+)\s*[$₹€£]?\s*([0-9.,]+)", norm_layout, re.I)
    if total_m:
        subtot = _clean_amount(total_m.group(1))
        tax_amt = _clean_amount(total_m.group(2))
        tot_amt = _clean_amount(total_m.group(3))

        raw_invoice.invoice["subtotal"] = [_field(subtot, p_num, [], f"Net worth: {subtot}", conf_map.get("subtotal", 0.94))]
        raw_invoice.invoice["total_tax"] = [_field(tax_amt, p_num, [], f"VAT tax: {tax_amt}", conf_map.get("total_tax", 0.93))]
        raw_invoice.invoice["total_amount"] = [_field(tot_amt, p_num, [], f"Gross total: {tot_amt}", conf_map.get("total_amount", 0.96))]
    else:
        m_tot = re.search(r"(?:Grand\s+Total|Total\s+Amount|Gross\s+worth|Net\s+Payable|Total)\s*[:$₹€£\-]?\s*([0-9.,]+)", full_layout, re.I)
        if m_tot:
            tot_amt = _clean_amount(m_tot.group(1))
            raw_invoice.invoice["total_amount"] = [_field(tot_amt, p_num, [], m_tot.group(0), conf_map.get("total_amount", 0.96))]

    # 4. Tabular Line Items Extraction via Banding and Columns
    hdr = next((t for t in sorted_tokens if "description" in t.text.lower()), None)
    sum_tok = next((t for t in sorted_tokens if "summary" in t.text.lower() or "total" in t.text.lower() and t.box[1] > 0.60), None)

    if hdr:
        top_y = hdr.box[3] + 0.005
        bot_y = sum_tok.box[1] - 0.005 if sum_tok else 0.80

        table_tokens = [t for t in tokens if top_y <= (t.box[1] + t.box[3]) / 2 <= bot_y]
        item_bands = _cluster_bands(table_tokens, tolerance=band_tol)

        current_item: dict[str, Any] | None = None
        extracted_items: list[dict[str, Any]] = []

        for band in item_bands:
            # Check if this band starts a new item (e.g. item number '1.', '2.', or new quantity)
            lead_tok = next((t for t in band if t.box[0] < 0.14), None)
            is_new_item = lead_tok and re.match(r"^\d+\.?$", lead_tok.text.strip())

            desc_toks = [t for t in band if 0.12 <= (t.box[0] + t.box[2]) / 2 < 0.39]
            qty_tok = next((t for t in band if 0.38 <= (t.box[0] + t.box[2]) / 2 < 0.45), None)
            unit_tok = next((t for t in band if 0.45 <= (t.box[0] + t.box[2]) / 2 < 0.52), None)
            price_tok = next((t for t in band if 0.52 <= (t.box[0] + t.box[2]) / 2 < 0.63), None)
            tax_rate_tok = next((t for t in band if 0.73 <= (t.box[0] + t.box[2]) / 2 < 0.83), None)
            total_tok = next((t for t in band if 0.83 <= (t.box[0] + t.box[2]) / 2 <= 0.95), None)

            if is_new_item:
                if current_item:
                    extracted_items.append(current_item)
                current_item = {
                    "item_no": lead_tok.text.strip().rstrip("."),
                    "desc_parts": [" ".join(t.text for t in desc_toks)] if desc_toks else [],
                    "desc_tokens": list(desc_toks),
                    "qty": _clean_amount(qty_tok.text) if qty_tok else "",
                    "qty_toks": [qty_tok] if qty_tok else [],
                    "unit": unit_tok.text.strip() if unit_tok else "",
                    "price": _clean_amount(price_tok.text) if price_tok else "",
                    "price_toks": [price_tok] if price_tok else [],
                    "tax_rate": tax_rate_tok.text.strip() if tax_rate_tok else "",
                    "total": _clean_amount(total_tok.text) if total_tok else "",
                    "total_toks": [total_tok] if total_tok else [],
                }
            elif current_item is not None:
                # Continuation description line
                if desc_toks:
                    current_item["desc_parts"].append(" ".join(t.text for t in desc_toks))
                    current_item["desc_tokens"].extend(desc_toks)
                if qty_tok and not current_item["qty"]:
                    current_item["qty"] = _clean_amount(qty_tok.text)
                    current_item["qty_toks"] = [qty_tok]
                if price_tok and not current_item["price"]:
                    current_item["price"] = _clean_amount(price_tok.text)
                    current_item["price_toks"] = [price_tok]
                if total_tok and not current_item["total"]:
                    current_item["total"] = _clean_amount(total_tok.text)
                    current_item["total_toks"] = [total_tok]

        if current_item:
            extracted_items.append(current_item)

        for item_dict in extracted_items:
            desc_val = " ".join(item_dict["desc_parts"]).strip()
            if desc_val or item_dict["qty"] or item_dict["total"]:
                line_fields: dict[str, RawField] = {}
                if item_dict["item_no"]:
                    line_fields["line_no"] = _field(item_dict["item_no"], p_num, [], item_dict["item_no"], 0.92)
                if desc_val:
                    line_fields["description"] = _field(desc_val, p_num, item_dict["desc_tokens"], desc_val, 0.94)
                if item_dict["qty"]:
                    line_fields["quantity"] = _field(item_dict["qty"], p_num, item_dict["qty_toks"], item_dict["qty"], 0.92)
                if item_dict["unit"]:
                    line_fields["unit"] = _field(item_dict["unit"], p_num, [], item_dict["unit"], 0.90)
                if item_dict["price"]:
                    line_fields["unit_price"] = _field(item_dict["price"], p_num, item_dict["price_toks"], item_dict["price"], 0.90)
                if item_dict["tax_rate"]:
                    line_fields["tax_rate"] = _field(item_dict["tax_rate"], p_num, [], item_dict["tax_rate"], 0.90)
                if item_dict["total"]:
                    line_fields["line_total"] = _field(item_dict["total"], p_num, item_dict["total_toks"], item_dict["total"], 0.92)
                raw_invoice.line_items.append(line_fields)

    return raw_invoice
