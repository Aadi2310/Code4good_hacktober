"""Handwriting detection, interpretation, and routing owned by P2."""

from __future__ import annotations

from pathlib import Path
from .detect import apply_occlusion_confidence, classify_handwriting, score_line
from .interpret import clean_handwritten_text, disambiguate_characters, infer_semantic_role, interpret_tokens


def available() -> bool:
    """Report availability of handwriting detection and interpretation pipeline."""
    model_path = Path(__file__).resolve().parent / "models" / "handwriting_model.json"
    return model_path.is_file()


__all__ = [
    "available",
    "apply_occlusion_confidence",
    "classify_handwriting",
    "score_line",
    "clean_handwritten_text",
    "disambiguate_characters",
    "infer_semantic_role",
    "interpret_tokens",
]
