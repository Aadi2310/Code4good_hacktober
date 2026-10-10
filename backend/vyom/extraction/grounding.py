from __future__ import annotations

import re
from typing import Any


def ground_value(value: str, tokens: list[Any], *, handwritten: bool = False) -> dict[str, Any]:
    """Find exact or near-exact OCR evidence for a candidate value."""
    target = re.sub(r"\s+", "", value).casefold()
    best: tuple[int, Any] | None = None
    limit = 2 if handwritten else 1
    for token in tokens:
        text = token.get("text", "") if isinstance(token, dict) else getattr(token, "text", "")
        candidate = re.sub(r"\s+", "", str(text)).casefold()
        if not candidate:
            continue
        distance = _edit_distance(target, candidate)
        if distance <= limit and (best is None or distance < best[0]):
            best = (distance, token)
    if best is None:
        return {"grounded": False, "bbox": None, "page": None, "evidence_text": None}
    token = best[1]
    get = token.get if isinstance(token, dict) else lambda key, default=None: getattr(token, key, default)
    box = get("box")
    if box and len(box) == 4:
        bbox = {"page": get("page", 1), "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]}
    else:
        bbox = get("bbox")
    return {"grounded": True, "bbox": bbox, "page": get("page"), "evidence_text": get("text"), "distance": best[0]}


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 3
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def ground_raw_invoice(raw: Any, tokens: list[Any]) -> Any:
    """Annotate critical RawFields in-place with OCR evidence and grounding note."""
    critical = {"invoice_no", "invoice_date", "supplier_gstin", "buyer_gstin", "total_amount", "taxable_value", "line_total", "unit_price"}
    hypotheses = raw.invoice if hasattr(raw, "invoice") else raw.get("invoice", {})
    for key, fields in hypotheses.items():
        if key not in critical:
            continue
        for field in fields:
            value = field.value if hasattr(field, "value") else field.get("value")
            if value is None:
                continue
            result = ground_value(str(value), tokens)
            if result["grounded"]:
                for name in ("page", "bbox", "evidence_text"):
                    if result.get(name) is not None:
                        setattr(field, name, result[name]) if hasattr(field, name) else field.__setitem__(name, result[name])
            else:
                if hasattr(field, "confidence"):
                    field.confidence *= .5
                else:
                    field["confidence"] = float(field.get("confidence", 0)) * .5
                if hasattr(field, "evidence_text"):
                    field.evidence_text = "ungrounded"
                else:
                    field["evidence_text"] = "ungrounded"
    return raw


def ground_vlm_payload(payload: dict[str, Any], tokens: list[Any]) -> dict[str, Any]:
    critical = {"invoice_no", "invoice_date", "supplier_gstin", "buyer_gstin", "total_amount", "taxable_value", "line_total", "unit_price"}
    invoice = payload.get("invoice")
    if isinstance(invoice, dict):
        _ground_mapping(invoice, critical, tokens)
    for key, value in list(payload.items()):
        if key == "line_items" and isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    _ground_mapping(row, critical, tokens)
        elif key in critical and value is not None:
            result = ground_value(str(value), tokens)
            payload.setdefault("_grounding", {})[key] = result
    return payload


def _ground_mapping(mapping: dict[str, Any], critical: set[str], tokens: list[Any]) -> None:
    evidence: dict[str, Any] = {}
    for key, value in list(mapping.items()):
        if key in critical and value is not None:
            evidence[key] = ground_value(str(value), tokens)
    if evidence:
        mapping["_grounding"] = evidence
