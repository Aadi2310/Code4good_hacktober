from __future__ import annotations

from collections.abc import Iterable

BASE_CONFIDENCE = {"qr": .99, "excel": 1.0, "csv": 1.0, "pdf_text": .98, "ocr": .97, "trocr": .85, "vlm": .75, "vlm_crop": .80, "llm_text": .70, "rules": .80}
FAMILIES = {"qr": "qr", "excel": "tabular", "csv": "tabular", "pdf_text": "pdf_text", "ocr": "ocr_rules", "rules": "ocr_rules", "trocr": "trocr", "vlm": "vlm", "vlm_crop": "vlm"}


def source_confidence(source: str, reported: float | None = None, *, handwritten: bool = False) -> float:
    base = BASE_CONFIDENCE.get(source, .65)
    if reported is not None:
        base = min(base, max(0.0, reported))
    if source == "ocr" and handwritten:
        base = min(base, .80)
    return base


def combine_confidences(values: Iterable[tuple[str, float]]) -> float:
    family_best: dict[str, float] = {}
    for source, confidence in values:
        family = FAMILIES.get(source, source)
        family_best[family] = max(family_best.get(family, 0.0), max(0.0, min(1.0, confidence)))
    combined = 1.0
    for confidence in family_best.values():
        combined *= 1.0 - confidence
    return min(.99, 1.0 - combined)


def cap_confidence(confidence: float, *, invalid_format: bool = False, gstin_valid: bool | None = None, ambiguous_date: bool = False, ungrounded: bool = False) -> float:
    value = confidence
    if invalid_format:
        value *= .5
    if gstin_valid is True:
        value = max(value, .95)
    elif gstin_valid is False:
        value = min(value, .60)
    if ambiguous_date:
        value = min(value, .92)
    if ungrounded:
        value *= .5
    return min(1.0, max(0.0, value))
