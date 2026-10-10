from __future__ import annotations

import re

from .common import ascii_digits, clean_text, load_reference, numeric_lookalikes
from .gstin import normalize as normalize_gstin


def normalize_pan(value: str | None) -> tuple[str | None, list[str]]:
    text = re.sub(r"\s+", "", clean_text(value or "")).upper()
    if not text:
        return None, ["PAN_MISSING"]
    if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", text):
        return text, ["PAN_FORMAT"]
    if text[3] not in "PCHFATBLJG":
        return text, ["PAN_ENTITY_TYPE"]
    return text, []


def normalize_invoice_no(value: str | None) -> tuple[str | None, list[str]]:
    text = clean_text(value or "")
    if not text:
        return None, ["INVOICE_NUMBER_MISSING"]
    notes = []
    if len(text) > 16 or not re.fullmatch(r"[A-Za-z0-9/\\.\- ]+", text):
        notes.append("INVOICE_NUMBER_RULE46")
    return text, notes


def normalize_hsn(value: str | None) -> tuple[str | None, list[str]]:
    text = ascii_digits(clean_text(value or ""))
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None, ["HSN_MISSING"]
    valid = (len(digits) in (4, 6, 8) and digits[:2] in load_reference("hsn_chapters.json")["valid"])
    if len(digits) == 6 and digits.startswith("99"):
        valid = True
    return digits, [] if valid else ["HSN_FORMAT"]


def normalize_pincode(value: str | None) -> tuple[str | None, list[str]]:
    forced, _ = numeric_lookalikes(ascii_digits(clean_text(value or "")))
    digits = re.sub(r"\D", "", forced)
    return digits, [] if re.fullmatch(r"[1-9]\d{5}", digits) else ["PINCODE_FORMAT"]


def normalize_phone(value: str | None) -> tuple[str | None, list[str]]:
    digits = re.sub(r"\D", "", ascii_digits(clean_text(value or "")))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if re.fullmatch(r"[6-9]\d{9}", digits):
        return digits, []
    return clean_text(value or "") or None, ["PHONE_FORMAT"]


def normalize_ifsc(value: str | None) -> tuple[str | None, list[str]]:
    text = re.sub(r"\s+", "", clean_text(value or "")).upper()
    return text or None, [] if re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", text) else ["IFSC_FORMAT"]


def normalize_state(value: str | None) -> tuple[str | None, list[str]]:
    text = clean_text(value or "")
    if not text:
        return None, ["POS_MISSING"]
    states = load_reference("state_codes.json")
    key = re.sub(r"[^A-Z0-9]", "", text.upper())
    for code, row in states.items():
        aliases = [code, row["name"], *row.get("aliases", [])]
        if key in {re.sub(r"[^A-Z0-9]", "", a.upper()) for a in aliases}:
            return f"{code}-{row['name']}", []
    return text, ["STATE_UNKNOWN"]


def normalize_unit(value: str | None) -> tuple[str | None, list[str]]:
    text = clean_text(value or "").lower().replace(".", "")
    data = load_reference("uqc.json")
    mapped = data["synonyms"].get(text, text.upper())
    return (mapped, []) if mapped in data["codes"] else (mapped or None, ["UOM_UNKNOWN"])
