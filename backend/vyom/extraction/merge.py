from __future__ import annotations

import os
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from vyom.domain.normalize import normalize_field
from vyom.validation.confidence import combine_confidences, source_confidence
from .grounding import ground_vlm_payload
from .vlm import get_extractor


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _raw_field(field: Any) -> dict[str, Any]:
    if isinstance(field, dict): return field
    return field.model_dump() if hasattr(field, "model_dump") else vars(field)


def merge_hypotheses(hypotheses: list[dict[str, Any]], field_name: str, *, locale: str = "in") -> dict[str, Any]:
    """Normalize, group, score, and resolve competing source hypotheses."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    qr_values: set[str] = set()
    for hypothesis in hypotheses:
        raw = hypothesis.get("value")
        normalized, notes = normalize_field(field_name, raw, locale=locale)
        if normalized is None:
            continue
        source = str(hypothesis.get("source", "rules"))
        confidence = source_confidence(source, float(hypothesis.get("confidence", 0.0)))
        groups[normalized].append({**hypothesis, "normalized": normalized, "confidence": confidence, "notes": notes})
        if source == "qr":
            qr_values.add(normalized)
    if not groups:
        return {"value": None, "raw": None, "confidence": 0.0, "status": "missing", "sources": [], "alternatives": []}
    scored: list[tuple[str, float, list[dict[str, Any]]]] = []
    for value, candidates in groups.items():
        combined = combine_confidences((str(c.get("source", "rules")), float(c["confidence"])) for c in candidates)
        scored.append((value, combined, candidates))
    scored.sort(key=lambda row: (-row[1], row[0]))
    # QR wins deterministically; otherwise retain a clear confidence winner.
    qr_winner = next((row for row in scored if row[0] in qr_values), None)
    winner = qr_winner or scored[0]
    ambiguous = len(scored) > 1 and not qr_winner and winner[1] - scored[1][1] < .15
    best_h = max(winner[2], key=lambda item: float(item["confidence"]))
    alternatives = [{"value": value, "confidence": confidence, "source": "+".join(sorted({str(c.get("source", "rules")) for c in group}))} for value, confidence, group in scored if value != winner[0]][:3]
    if ambiguous:
        alternatives = [{"value": best_h.get("value"), "confidence": float(best_h["confidence"]), "source": str(best_h.get("source", "rules"))}, *alternatives][:3]
    sources = sorted({str(c.get("source", "rules")) for c in winner[2]})
    return {"value": winner[0], "raw": best_h.get("value"), "confidence": winner[1] * (.6 if ambiguous else 1), "status": "needs_review" if ambiguous else ("accepted" if winner[1] >= .90 else "needs_review"), "sources": sources, "alternatives": alternatives, "source_page": best_h.get("page"), "bbox": best_h.get("bbox"), "evidence_text": best_h.get("evidence_text"), "note": "CONFLICT_UNRESOLVED" if ambiguous else None}


def merge_segment(segment: Any, source: Any, bundle: Any, vlm_hypothesis: dict[str, Any] | None = None) -> Any:
    from vyom.models import HEADER_FIELDS, LINE_FIELDS, Alt, FV, InvoiceRecord, Quality, RecordSource, Review, Validation
    all_sources = []
    for raw in (_get(segment, "qr"), _get(segment, "rules")):
        if raw is not None: all_sources.append(raw)
    if vlm_hypothesis:
        from vyom.models import RawInvoice, RawField
        invoice_fields = {}
        vlm_invoice = vlm_hypothesis.get("invoice", vlm_hypothesis)
        for key, value in vlm_invoice.items():
            if key not in HEADER_FIELDS or key == "line_items":
                continue
            evidence = vlm_invoice.get("_grounding", {}).get(key, {})
            invoice_fields[key] = [RawField(value=str(value) if value is not None else None, confidence=.75 if evidence.get("grounded", True) else .375, source="vlm", page=evidence.get("page"), bbox=evidence.get("bbox"), evidence_text=evidence.get("evidence_text"))]
        vlm_lines = []
        for row in vlm_hypothesis.get("line_items", []):
            line = {}
            for key, value in row.items():
                if key not in LINE_FIELDS:
                    continue
                evidence = row.get("_grounding", {}).get(key, {})
                line[key] = RawField(value=str(value) if value is not None else None, confidence=.75 if evidence.get("grounded", True) else .375, source="vlm", page=evidence.get("page"), bbox=evidence.get("bbox"), evidence_text=evidence.get("evidence_text"))
            vlm_lines.append(line)
        vlm = RawInvoice(source="vlm", invoice=invoice_fields, line_items=vlm_lines)
        all_sources.append(vlm)
    header_hyp: dict[str, list[dict[str, Any]]] = {key: [] for key in HEADER_FIELDS}
    for raw in all_sources:
        invoice = _get(raw, "invoice", {})
        for key, fields in invoice.items():
            if key in header_hyp:
                header_hyp[key].extend(_raw_field(f) for f in fields)
    merged = {}
    for key in HEADER_FIELDS:
        result = merge_hypotheses(header_hyp[key], key)
        result["alternatives"] = [Alt(**a) for a in result["alternatives"]]
        merged[key] = FV(**result)
    line_items = []
    max_lines = max((len(_get(raw, "line_items", [])) for raw in all_sources), default=0)
    for i in range(max_lines):
        row: dict[str, Any] = {}
        for key in LINE_FIELDS:
            hypotheses = []
            for raw in all_sources:
                rows = _get(raw, "line_items", [])
                if i < len(rows) and key in rows[i]:
                    hypotheses.append(_raw_field(rows[i][key]))
            result = merge_hypotheses(hypotheses, key)
            result["alternatives"] = [Alt(**a) for a in result["alternatives"]]
            row[key] = FV(**result)
        line_items.append(row)
    src = dict(source) if isinstance(source, dict) else source.model_dump()
    page_range = list(_get(segment, "page_range", []) or [])
    src["page_range"] = page_range
    flags_list = []
    for page in _get(bundle, "pages", []):
        p_idx = _get(page, "index", 1)
        for flag in _get(page, "flags", []):
            flags_list.append({"page": p_idx, "flag": flag} if isinstance(flag, str) else flag)
    quality = Quality(handwriting_ratio=float(_get(bundle, "handwriting_ratio", 0)), degraded=bool(_get(bundle, "degraded_reasons", [])), degraded_reasons=list(_get(bundle, "degraded_reasons", [])), engines=dict(_get(bundle, "engines", {})), timings_ms=dict(_get(bundle, "timings_ms", {})), page_flags=flags_list)
    record = InvoiceRecord(record_id=str(uuid.uuid4()), source=RecordSource(**src), invoice=merged, line_items=line_items, validation=Validation(), review=Review(), quality=quality)
    from vyom.validation import validate
    return validate(record, repair=True)


def run(bundle: Any, source: Any, options: dict[str, Any] | None = None) -> list[Any]:
    options = options or {}
    results = []
    extractor = None if options.get("extractor_mode") == "rules" else get_extractor()
    for segment in _get(bundle, "segments", []):
        hypotheses = None
        reasons = list(_get(bundle, "degraded_reasons", []))
        if extractor and extractor.available():
            pages = _get(segment, "page_range", [])
            page_data = {int(_get(page, "index", 0)): page for page in _get(bundle, "pages", [])}
            images = [page_data[p].original_path for p in pages if p in page_data]
            hints = "\n".join(str(_get(page_data[p], "layout_text", "")) for p in pages if p in page_data)
            try:
                try:
                    from vyom.models import HEADER_FIELDS, LINE_FIELDS
                    schema = {"invoice_fields": HEADER_FIELDS, "line_fields": LINE_FIELDS}
                except ImportError:
                    schema = {"invoice_fields": [], "line_fields": []}
                raw_output = extractor.extract_invoice(images[:3], hints, schema)
                hypotheses = raw_output
                page_tokens = [token for p in pages if p in page_data for token in _get(page_data[p], "tokens", [])]
                hypotheses = ground_vlm_payload(hypotheses, page_tokens)
            except Exception:
                reasons.append("VLM_BAD_OUTPUT")
        else:
            reasons.append("VLM_UNAVAILABLE")
        if reasons:
            if isinstance(bundle, dict):
                bundle_for_record = dict(bundle)
                bundle_for_record["degraded_reasons"] = list(dict.fromkeys([*bundle.get("degraded_reasons", []), *reasons]))
            elif hasattr(bundle, "model_copy"):
                bundle_for_record = bundle.model_copy(update={"degraded_reasons": list(dict.fromkeys([*_get(bundle, "degraded_reasons", []), *reasons]))})
            else:
                bundle_for_record = bundle
        else:
            bundle_for_record = bundle
        record = merge_segment(segment, source, bundle_for_record, hypotheses)
        results.append(record)
    return results
