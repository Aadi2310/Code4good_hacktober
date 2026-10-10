"""PDF page classification based on extractable text and image coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Classification:
    kind: str
    text: str
    chars: int
    garbled_ratio: float
    image_coverage: float


_CID = re.compile(r"\(cid:\s*\d+\)", re.IGNORECASE)
_GARBLED = re.compile(r"[\ufffd\ue000-\uf8ff]")


def classify_page(page: Any) -> Classification:
    """Classify one pdfplumber page using the shared contract thresholds."""
    text = page.extract_text() or ""
    visible = [char for char in text if not char.isspace()]
    cid_matches = list(_CID.finditer(text))
    cid_chars = sum(sum(not char.isspace() for char in match.group(0)) for match in cid_matches)
    garbled = sum(bool(_GARBLED.fullmatch(char)) for char in visible) + cid_chars
    chars = len(visible)
    garbled_ratio = min(1.0, garbled / max(chars, 1))

    page_area = max(float(page.width) * float(page.height), 1.0)
    image_area = 0.0
    for image in getattr(page, "images", []) or []:
        try:
            width = max(0.0, min(float(image["x1"]), float(page.width)) - max(float(image["x0"]), 0.0))
            height = max(0.0, min(float(image["bottom"]), float(page.height)) - max(float(image["top"]), 0.0))
            image_area += width * height
        except (KeyError, TypeError, ValueError):
            continue
    image_coverage = min(1.0, image_area / page_area)

    if chars >= 50 and garbled_ratio < 0.10 and image_coverage < 0.85:
        kind = "digital"
    elif chars < 20 or garbled_ratio >= 0.30 or (image_coverage >= 0.85 and chars < 50):
        kind = "scanned"
    else:
        kind = "mixed"
    return Classification(kind, text, chars, garbled_ratio, image_coverage)
