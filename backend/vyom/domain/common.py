from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "reference"


@lru_cache(maxsize=None)
def load_reference(name: str) -> Any:
    path = REFERENCE_DIR / name
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if name == "state_codes.json":
        codes = set(data)
        if not {"25", "96", "97", "99"}.issubset(codes):
            raise ValueError("state reference is missing required special codes")
        if any(len(code) != 2 or "name" not in row or "utgst" not in row for code, row in data.items()):
            raise ValueError("state reference contains a malformed entry")
    if name == "uqc.json" and len(data.get("codes", [])) != len(set(data["codes"])):
        raise ValueError("UQC codes must be unique")
    if name == "gst_rates.yaml":
        from decimal import Decimal
        rates = [Decimal(str(rate)) for rate in data.get("all_known", [])]
        if not rates or any(rate < 0 or rate > 100 for rate in rates):
            raise ValueError("GST rate reference contains invalid rates")
        if not {Decimal("0"), Decimal("5"), Decimal("18")}.issubset(set(rates)):
            raise ValueError("GST rate reference is missing core rates")
    if name == "hsn_chapters.json" and any(chapter not in data.get("valid", []) for chapter in ("01", "76", "78", "97", "99")):
        raise ValueError("HSN reference is missing required chapters")
    return data


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = "".join(c for c in value if c not in "\u200b\u200c\u200d\ufeff")
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def ascii_digits(value: str) -> str:
    out: list[str] = []
    for char in value:
        try:
            out.append(str(unicodedata.digit(char))) if char.isdecimal() else out.append(char)
        except (TypeError, ValueError):
            out.append(char)
    return "".join(out)


def numeric_lookalikes(value: str) -> tuple[str, bool]:
    swaps = {"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "s": "5", "B": "8", "b": "8"}
    result = "".join(swaps.get(char, char) for char in value)
    return result, result != value
