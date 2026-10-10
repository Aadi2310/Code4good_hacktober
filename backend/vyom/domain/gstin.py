from __future__ import annotations

import re

from .common import load_reference

ALPHANUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PATTERN = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")


def normalize(s: str | None) -> tuple[str | None, list[str]]:
    if s is None:
        return None, ["GSTIN_MISSING"]
    value = re.sub(r"[\s-]+", "", str(s)).upper()
    return (value, []) if value else (None, ["GSTIN_EMPTY"])


def check_char(first14: str) -> str:
    value = first14.upper()
    if len(value) != 14 or any(c not in ALPHANUM for c in value):
        raise ValueError("GSTIN checksum requires 14 base-36 characters")
    total = 0
    for i, char in enumerate(value):
        product = ALPHANUM.index(char) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return ALPHANUM[(36 - total % 36) % 36]


def parse(s: str | None) -> dict[str, str] | None:
    value, _ = normalize(s)
    if value is None:
        return None
    match = PATTERN.fullmatch(value)
    if not match:
        return None
    return {"state_code": match.group(1), "pan": match.group(2), "entity": match.group(3), "check_char": match.group(4)}


def is_valid(s: str | None) -> bool:
    value, _ = normalize(s)
    if value is None:
        return False
    match = PATTERN.fullmatch(value)
    if not match or match.group(1) not in load_reference("state_codes.json") or match.group(1) == "96":
        return False
    return check_char(value[:14]) == value[-1]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    states = load_reference("state_codes.json")
    state_code = state.zfill(2) if state.isdigit() else next((code for code, row in states.items() if state.casefold() == row["name"].casefold() or state.upper() in row["aliases"]), "")
    pan_value = pan.upper().replace(" ", "")
    if state_code not in states or state_code == "96" or not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", pan_value):
        raise ValueError("invalid GSTIN state or PAN")
    if not re.fullmatch(r"[1-9A-Z]", entity.upper()):
        raise ValueError("entity must be 1-9 or A-Z")
    first14 = f"{state_code}{pan_value}{entity.upper()}Z"
    return first14 + check_char(first14)
