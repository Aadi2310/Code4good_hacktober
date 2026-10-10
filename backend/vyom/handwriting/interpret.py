"""Handwriting text cleaning, character disambiguation, and semantic role interpretation."""

from __future__ import annotations

import re
from typing import Any

from vyom.models import Token

# Artifact markers frequently present in handwriting datasets (like GNHK)
_NOISE_TOKENS = re.compile(r"%(?:math|na|sc|unclear|illegible|strike|deleted|insert)%", re.IGNORECASE)
_STRAY_SYMBOLS = re.compile(r"^[^\w\s₹$€£%]+|[^\w\s₹$€£%]+$")

_DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b"),
    re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}\b", re.IGNORECASE),
]

_AMOUNT_PATTERN = re.compile(r"^(?:[₹$€£]|INR|Rs\.?)\s*-?\d[\d,]*(?:\.\d{1,2})?|-?\d[\d,]*(?:\.\d{1,2})?\s*(?:/-)?$", re.IGNORECASE)
_QTY_PATTERN = re.compile(r"^\d+(?:\.\d+)?\s*(?:nos|pcs|qty|units?|kgs?|ltrs?|each|boxes)?$", re.IGNORECASE)
_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9/\-_]{3,24}$", re.IGNORECASE)

_NUMERIC_REPLACEMENTS = {
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "|": "1", "i": "1", "!": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5", "$": "5",
    "b": "6",
    "B": "8",
}

_ALPHA_REPLACEMENTS = {
    "0": "O",
    "1": "I",
    "5": "S",
    "8": "B",
    "2": "Z",
}


def clean_handwritten_text(text: str) -> str:
    """Strip noise tokens, normalize spaces, and remove OCR margin artifacts."""
    if not text:
        return ""
    cleaned = _NOISE_TOKENS.sub(" ", text)
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def disambiguate_characters(text: str, expected_type: str = "numeric") -> str:
    """Disambiguate common handwritten character confusions based on expected type."""
    if not text:
        return ""
    cleaned = clean_handwritten_text(text)
    if expected_type == "numeric":
        chars = []
        for ch in cleaned:
            chars.append(_NUMERIC_REPLACEMENTS.get(ch, ch))
        return "".join(chars)
    elif expected_type == "alpha":
        chars = []
        for ch in cleaned:
            chars.append(_ALPHA_REPLACEMENTS.get(ch, ch))
        return "".join(chars)
    return cleaned


def infer_semantic_role(text: str) -> str:
    """Infer semantic field role of handwritten text snippet."""
    cleaned = clean_handwritten_text(text)
    if not cleaned:
        return "empty"
    lower = cleaned.casefold()

    for pattern in _DATE_PATTERNS:
        if pattern.search(cleaned):
            return "date"

    if _AMOUNT_PATTERN.match(cleaned) or any(c in cleaned for c in "₹$€£"):
        return "amount"

    if _QTY_PATTERN.match(cleaned):
        return "quantity"

    if any(k in lower for k in ("invoice", "bill", "tax", "total", "subtotal", "date", "seller", "buyer", "client")):
        return "label"

    if _ID_PATTERN.match(cleaned) and any(c.isdigit() for c in cleaned):
        return "identifier"

    return "text"


def interpret_tokens(tokens: list[Token]) -> list[Token]:
    """Clean text, resolve handwriting OCR confusions, and assign semantic role notes."""
    interpreted: list[Token] = []
    for token in tokens:
        cleaned_text = clean_handwritten_text(token.text)
        if not cleaned_text:
            continue
        role = infer_semantic_role(cleaned_text)
        if token.kind == "handwritten":
            if role in {"amount", "quantity"}:
                cleaned_text = disambiguate_characters(cleaned_text, "numeric")
            elif role == "text" and not any(c.isdigit() for c in cleaned_text):
                cleaned_text = disambiguate_characters(cleaned_text, "alpha")

        interpreted.append(Token(
            text=cleaned_text,
            conf=token.conf,
            box=token.box,
            page=token.page,
            line_id=token.line_id,
            kind=token.kind,
            source=token.source,
        ))
    return interpreted
