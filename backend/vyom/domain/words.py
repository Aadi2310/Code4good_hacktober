from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "crore": 10_000_000}
_VOCAB = set(_ONES) | set(_TENS) | set(_SCALES) | {"lac", "lakhs", "crores", "paisa", "paise", "and", "rupee", "rupees", "rs", "inr", "only"}


def _match(word: str) -> tuple[str | None, float]:
    if word in {"lac", "lakhs"}: return "lakh", 1.0
    if word == "crores": return "crore", 1.0
    if word in {"paisa", "paise"}: return "paise", 1.0
    if word in {"rs", "inr"}: return "rupees", 1.0
    if word in _VOCAB: return word, 1.0
    scored = sorted(((SequenceMatcher(None, word, candidate).ratio(), candidate) for candidate in _VOCAB), reverse=True)
    if not scored or scored[0][0] < .85 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None, 0.0
    return scored[0][1], scored[0][0]


def _parse_integer_words(words: list[str]) -> int | None:
    total = 0
    group = 0
    current = 0
    for token in words:
        if token in {"and", "rupee", "rupees", "rs", "inr", "only"}:
            continue
        if token in _ONES:
            current += _ONES[token]
        elif token in _TENS:
            current += _TENS[token]
        elif token == "hundred":
            group += max(current, 1) * 100
            current = 0
        elif token in {"thousand", "lakh", "crore"}:
            scale = _SCALES[token]
            total += (group + current) * scale
            group = current = 0
        else:
            return None
    return total + group + current


def parse_amount_words(value: str | None) -> tuple[Decimal | None, float]:
    """Parse Indian rupee words; unknown tokens fail closed."""
    if not value:
        return None, 0.0
    tokens = re.findall(r"[A-Za-z]+", value.casefold().replace("-", " "))
    matched: list[str] = []
    confidences: list[float] = []
    for token in tokens:
        normalized, confidence = _match(token)
        if normalized is None:
            return None, 0.0
        matched.append(normalized); confidences.append(confidence)
    if not matched:
        return None, 0.0
    paise_at = matched.index("paise") if "paise" in matched else len(matched)
    rupee_at = matched.index("rupees") if "rupees" in matched else paise_at
    rupee_words = matched[:rupee_at]
    paise_words = matched[rupee_at + 1:paise_at] if "rupees" in matched else (matched[:paise_at] if paise_at < len(matched) else [])
    rupees = _parse_integer_words(rupee_words) if rupee_words else 0
    paise = _parse_integer_words(paise_words) if paise_words else 0
    if rupees is None or paise is None or paise > 99:
        return None, 0.0
    result = (Decimal(rupees) + Decimal(paise) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result, sum(confidences) / len(confidences)


def to_words(value: Decimal | str | int) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount < 0:
        return "minus " + to_words(-amount)
    whole = int(amount)
    paise = int((amount - whole) * 100)
    words = _integer_to_words(whole) + " rupees"
    if paise:
        words += " and " + _integer_to_words(paise) + " paise"
    return words + " only"


def _integer_to_words(value: int) -> str:
    if value == 0: return "zero"
    parts: list[str] = []
    for scale, label in ((10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand"), (100, "hundred")):
        count, value = divmod(value, scale)
        if count:
            parts.append(_integer_to_words(count) + " " + label)
    if value:
        if value < 20:
            parts.append(next(word for word, number in _ONES.items() if number == value))
        else:
            tens, ones = divmod(value, 10)
            tens_word = next(word for word, number in _TENS.items() if number == tens * 10)
            parts.append(tens_word + (" " + _integer_to_words(ones) if ones else ""))
    return " ".join(parts)
