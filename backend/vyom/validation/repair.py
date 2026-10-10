from __future__ import annotations

import itertools
import json
from pathlib import Path
from decimal import Decimal
from typing import Any

from vyom.domain.common import REFERENCE_DIR
from vyom.domain.gstin import is_valid
from vyom.domain.words import parse_amount_words


def gstin_candidates(value: str) -> list[tuple[float, str]]:
    try:
        confusion = json.loads((REFERENCE_DIR / "ocr_confusions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    maps: dict[str, set[str]] = {}
    for source, targets in confusion["digit_like"].items():
        for target in targets:
            maps.setdefault(target.upper(), set()).add(source)
    for source, targets in confusion["letter_like"].items():
        for target in targets:
            maps.setdefault(target.upper(), set()).add(source)
    allowed = [set("0123456789"), set("0123456789"), *([set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")] * 5), *([set("0123456789")] * 4), set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), set("123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"), {"Z"}, set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
    positions = [i for i, ch in enumerate(value) if i < len(allowed) and any(x in allowed[i] for x in maps.get(ch.upper(), set()))]
    if len(value) != 15:
        positions.extend(i for i in range(min(len(value), 15), 15))
    positions = positions[:6]
    candidates: dict[str, float] = {}
    if len(value) == 15:
        for count in (1, 2):
            for indexes in itertools.combinations(positions, count):
                choices = []
                for i in indexes:
                    ch = value[i].upper()
                    opts = {x for x in maps.get(ch, set()) if x in allowed[i]}
                    if i == 13 and ch in {"2", "7", "Z"}:
                        opts.add("Z")
                    choices.append(sorted(opts))
                if any(not options for options in choices):
                    continue
                for replacements in itertools.product(*choices):
                    chars = list(value.upper())
                    cost = 0.0
                    for i, replacement in zip(indexes, replacements):
                        chars[i] = replacement
                        cost += 1.3 if i != 13 else .3
                    candidate = "".join(chars)
                    if is_valid(candidate):
                        candidates[candidate] = min(candidates.get(candidate, 99), cost)
    return sorted(((cost, candidate) for candidate, cost in candidates.items()), key=lambda item: (item[0], item[1]))


def repair_gstin(rec: Any, independent_values: set[str] | None = None) -> list[dict[str, Any]]:
    invoice = rec.get("invoice") if isinstance(rec, dict) else getattr(rec, "invoice", {})
    corrections: list[dict[str, Any]] = []
    for key in ("supplier_gstin", "buyer_gstin"):
        field = invoice.get(key) if isinstance(invoice, dict) else getattr(invoice, key, None)
        value = field.get("value") if isinstance(field, dict) else getattr(field, "value", None)
        if not value or is_valid(str(value)):
            continue
        candidates = gstin_candidates(str(value))
        alternatives = field.get("alternatives", []) if isinstance(field, dict) else getattr(field, "alternatives", [])
        independent = set(independent_values or set())
        for alt in alternatives:
            alt_source = alt.get("source", "") if isinstance(alt, dict) else getattr(alt, "source", "")
            alt_value = alt.get("value") if isinstance(alt, dict) else getattr(alt, "value", None)
            if alt_value and alt_source not in {"original", "derived"}:
                independent.add(str(alt_value).replace(" ", "").upper())
        matches = [candidate for _, candidate in candidates if candidate in independent]
        unique = bool(candidates) and (len(candidates) == 1 or (len(candidates) > 1 and candidates[1][0] - candidates[0][0] >= .5))
        chosen = matches[0] if matches else candidates[0][1] if unique else None
        if not chosen:
            _add_candidates(rec, [{"field_path": f"invoice.{key}", "candidates": [v for _, v in candidates[:3]], "reason": "GSTIN_REPAIR_AMBIGUOUS"}])
            continue
        before_conf = float(field.get("confidence", 0) if isinstance(field, dict) else getattr(field, "confidence", 0))
        alt = {"value": str(value), "confidence": before_conf, "source": "original"}
        if isinstance(field, dict):
            field.setdefault("alternatives", []).insert(0, alt)
            field["alternatives"] = field["alternatives"][:3]
            field["value"] = chosen; field["status"] = "corrected"; field["confidence"] = min(.85, max(before_conf, .60))
        else:
            field.alternatives.insert(0, alt); field.alternatives = field.alternatives[:3]
            field.value = chosen; field.status = "corrected"; field.confidence = min(.85, max(before_conf, .60))
        correction = {"field_path": f"invoice.{key}", "original": str(value), "corrected": chosen, "method": "gstin_checksum", "reason": "Unique OCR-confusion candidate passes GSTIN checksum", "confidence_before": before_conf, "confidence_after": min(.85, max(before_conf, .60))}
        corrections.append(correction)
    if corrections:
        _add_corrections(rec, corrections)
    return corrections


def repair_amount_words(rec: Any) -> list[dict[str, Any]]:
    invoice = rec.get("invoice") if isinstance(rec, dict) else getattr(rec, "invoice", {})
    get = invoice.get if isinstance(invoice, dict) else lambda key, default=None: getattr(invoice, key, default)
    words_field = get("amount_in_words")
    total_field = get("total_amount")
    words = words_field.get("value") if isinstance(words_field, dict) else getattr(words_field, "value", None)
    total = total_field.get("value") if isinstance(total_field, dict) else getattr(total_field, "value", None)
    sources = (total_field.get("sources", []) if isinstance(total_field, dict) else getattr(total_field, "sources", [])) if total_field is not None else []
    if not words or not total or any(source in {"excel", "csv"} for source in sources):
        return []
    value, confidence = parse_amount_words(str(words))
    if value is None or confidence < .85:
        return []
    try:
        current = Decimal(str(total))
    except Exception:
        return []
    if value == current:
        return []
    taxable = get("taxable_value"); discount = get("discount"); roundoff = get("round_off")
    def dec(field: Any) -> Decimal:
        raw = field.get("value") if isinstance(field, dict) else getattr(field, "value", None)
        return Decimal(str(raw)) if raw is not None else Decimal(0)
    components = sum((dec(get(key)) for key in ("cgst", "sgst", "igst", "cess")), Decimal(0))
    expected = dec(taxable) - dec(discount) + components + dec(roundoff)
    if value != expected:
        return []
    before_conf = float(total_field.get("confidence", 0) if isinstance(total_field, dict) else getattr(total_field, "confidence", 0))
    after_conf = min(.85, max(before_conf, .60))
    if isinstance(total_field, dict):
        total_field.setdefault("alternatives", []).insert(0, {"value": str(total), "confidence": before_conf, "source": "original"})
        total_field["alternatives"] = total_field["alternatives"][:3]
        total_field["value"] = f"{value:.2f}"; total_field["status"] = "corrected"; total_field["confidence"] = after_conf
    else:
        from vyom.models import Alt
        total_field.alternatives.insert(0, Alt(value=str(total), confidence=before_conf, source="original"))
        total_field.alternatives = total_field.alternatives[:3]
        total_field.value = f"{value:.2f}"; total_field.status = "corrected"; total_field.confidence = after_conf
    correction = {"field_path": "invoice.total_amount", "original": str(total), "corrected": f"{value:.2f}", "method": "amount_in_words", "reason": "Words parse with high confidence and match the invoice arithmetic", "confidence_before": before_conf, "confidence_after": after_conf}
    _add_corrections(rec, [correction])
    return [correction]


def _add_corrections(rec: Any, corrections: list[dict[str, Any]]) -> None:
    validation = rec.get("validation") if isinstance(rec, dict) else getattr(rec, "validation", None)
    if validation is None:
        return
    if isinstance(validation, dict):
        validation.setdefault("corrections", []).extend(corrections)
    else:
        from vyom.models import Correction
        validation.corrections.extend(Correction(**item) for item in corrections)


def _add_candidates(rec: Any, candidates: list[dict[str, Any]]) -> None:
    validation = rec.get("validation") if isinstance(rec, dict) else getattr(rec, "validation", None)
    if validation is None:
        return
    if isinstance(validation, dict):
        validation.setdefault("repair_candidates", []).extend(candidates)
    else:
        validation.repair_candidates.extend(candidates)
