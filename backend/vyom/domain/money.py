from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .common import ascii_digits, clean_text, numeric_lookalikes

CENT = Decimal("0.01")


def parse_money(s: str | None, *, locale_hint: str = "in") -> tuple[str | None, list[str]]:
    if s is None:
        return None, ["MONEY_MISSING"]
    text = ascii_digits(clean_text(str(s)))
    if not text:
        return None, ["MONEY_EMPTY"]
    notes: list[str] = []
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()")
    if text.startswith("-"):
        text = text[1:].strip()
    text = re.sub(r"(?i)^(?:₹|rs\.?|inr|rupees?)\s*", "", text)
    text = re.sub(r"(?i)\s*(?:₹|rs\.?|inr|rupees?)\s*$", "", text)
    text, used_confusion = numeric_lookalikes(text)
    if used_confusion:
        notes.append("OCR_CONFUSION_USED")
    if re.search(r"(?i)\s*(?:cr|dr)\s*$", text):
        notes.append("MAGNITUDE_SUFFIX_IGNORED")
        text = re.sub(r"(?i)\s*(?:cr|dr)\s*$", "", text)
    text = re.sub(r"(?i)\s*/[-=]\s*$", "", text)
    if re.fullmatch(r"\d+[-=]\d{2}", text):
        text = re.sub(r"[-=]", ".", text)
        notes.append("DASH_DECIMAL")
    elif re.search(r"[-=]\s*$", text):
        text = re.sub(r"[-=]\s*$", "", text)
    text = text.strip().replace(" ", "")
    if locale_hint == "eu" and "." in text and "," in text:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})*,\d+", text):
            return None, ["MONEY_INVALID_GROUPING"]
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        if "." not in text and re.fullmatch(r"\d+,\d{2}", text):
            text = text.replace(",", ".")
            notes.append("COMMA_DECIMAL")
        else:
            integer, separator, fraction = text.partition(".")
            valid = (bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+", integer)) or
                     bool(re.fullmatch(r"\d{1,2}(?:,\d{2})+,\d{3}", integer)))
            valid = valid and (not separator or bool(re.fullmatch(r"\d+", fraction)))
            if not valid:
                return None, ["MONEY_INVALID_GROUPING"]
            text = integer.replace(",", "") + ("." + fraction if separator else "")
    if text.count(".") > 1 or not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None, ["MONEY_INVALID"]
    whole = text.split(".", 1)[0]
    if len(whole) > 15:
        return None, ["AMOUNT_TOO_LARGE"]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, ["MONEY_INVALID"]
    if amount > Decimal("1000000000000"):
        return None, ["AMOUNT_IMPLAUSIBLE"]
    if negative:
        amount = -amount
    return str(amount.quantize(CENT, rounding=ROUND_HALF_UP)), notes
