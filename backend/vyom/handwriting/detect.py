"""Line-level handwriting likelihood from confidence, glyph geometry, and ink."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from vyom.models import Token
from .interpret import interpret_tokens

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    cv2 = None

_MODEL_PATH = Path(__file__).resolve().parent / "models" / "handwriting_model.json"
_MODEL_CACHE: dict[str, Any] | None = None


def _load_model() -> dict[str, Any] | None:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if _MODEL_PATH.is_file():
        try:
            _MODEL_CACHE = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
            return _MODEL_CACHE
        except Exception:
            return None
    return None


def score_line(
    mean_confidence: float,
    component_heights: list[float] | None = None,
    component_bottoms: list[float] | None = None,
    median_line_height: float = 1.0,
    blue_ink_share: float | None = None,
    noise_tokens: float = 0.0,
) -> float:
    """Compute handwriting score in [0, 1] using trained model or calibrated features."""
    confidence_feature = min(1.0, max(0.0, (0.90 - mean_confidence) / 0.40))
    heights = np.asarray(component_heights or [], dtype=float)
    if len(heights) > 1 and float(np.mean(heights)) > 0:
        height_cv = float(np.std(heights) / np.mean(heights))
    else:
        height_cv = 0.0
    height_feature = min(0.5, height_cv) / 0.5
    bottoms = np.asarray(component_bottoms or [], dtype=float)
    baseline_feature = min(0.4, float(np.std(bottoms)) / max(median_line_height, 1e-6)) / 0.4 if len(bottoms) > 1 else 0.0

    model = _load_model()
    if model and "weights" in model:
        try:
            weights = np.asarray(model["weights"], dtype=float)
            bias = float(model.get("bias", 0.0))
            mean = np.asarray(model.get("mean", [0.0] * len(weights)), dtype=float)
            std = np.asarray(model.get("std", [1.0] * len(weights)), dtype=float)

            if len(weights) == 4:
                raw_feats = np.array([confidence_feature, height_feature, baseline_feature, noise_tokens], dtype=float)
            else:
                raw_feats = np.array([confidence_feature, height_feature, baseline_feature, noise_tokens, 0.2, 0.4][:len(weights)], dtype=float)

            norm_feats = (raw_feats - mean) / std
            linear = float(np.dot(norm_feats, weights) + bias)
            prob = 1.0 / (1.0 + np.exp(-np.clip(linear, -25, 25)))
            if blue_ink_share is not None and blue_ink_share > 0.5:
                prob = min(1.0, prob + 0.15)
            # High-confidence printed text guardrail
            if mean_confidence >= 0.94 and (blue_ink_share is None or blue_ink_share < 0.2) and height_feature < 0.40 and baseline_feature < 0.25:
                prob = min(prob, 0.25)
            return float(np.clip(prob, 0.0, 1.0))
        except Exception:
            pass

    features = [confidence_feature, height_feature, baseline_feature]
    if blue_ink_share is not None:
        features.append(1.0 if blue_ink_share > 0.5 else 0.0)
    return min(1.0, max(0.0, float(np.mean(features))))


def classify_handwriting(tokens: list[Token], image: np.ndarray) -> tuple[list[Token], float, dict[int, float]]:
    """Assign printed/mixed/handwritten token kinds and cap uncertain handwriting."""
    by_line: dict[tuple[int, int], list[Token]] = defaultdict(list)
    for token in tokens:
        by_line[(token.page, token.line_id)].append(token)
    height, width = image.shape[:2]
    grayscale = max(float(np.mean(np.abs(image[:, :, 0].astype(np.int16) - image[:, :, 1].astype(np.int16)))),
                    float(np.mean(np.abs(image[:, :, 1].astype(np.int16) - image[:, :, 2].astype(np.int16))))) < 1.0
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV) if cv2 is not None and not grayscale else None
    binary = None
    if cv2 is not None:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    scores: dict[int, float] = {}
    weighted = total = 0
    for (_, line_id), line in by_line.items():
        line_box = [min(t.box[0] for t in line), min(t.box[1] for t in line), max(t.box[2] for t in line), max(t.box[3] for t in line)]
        x0, y0, x1, y1 = (max(0, int(line_box[0] * width)), max(0, int(line_box[1] * height)),
                           min(width, int(line_box[2] * width) + 1), min(height, int(line_box[3] * height) + 1))
        component_heights: list[float] = []
        component_bottoms: list[float] = []
        line_height = max(1.0, y1 - y0)
        if binary is not None and x1 > x0 and y1 > y0:
            count, _, stats, _ = cv2.connectedComponentsWithStats(binary[y0:y1, x0:x1], 8)
            raw_comps = []
            for stat in stats[1:count]:
                bx, by, bw, bh, area = [int(v) for v in stat]
                if area >= 2 and bh >= 2 and bw <= max(1, x1 - x0):
                    raw_comps.append((by, bh))
            if raw_comps:
                max_bh = max(bh for _, bh in raw_comps)
                filtered_comps = [(by, bh) for by, bh in raw_comps if bh >= 0.30 * max_bh]
                if not filtered_comps:
                    filtered_comps = raw_comps
                for by, bh in filtered_comps:
                    component_heights.append(float(bh))
                    component_bottoms.append(float(by + bh))
        color_share = None
        if hsv is not None and x1 > x0 and y1 > y0:
            crop = hsv[y0:y1, x0:x1]
            ink = binary[y0:y1, x0:x1] > 0 if binary is not None else np.ones(crop.shape[:2], bool)
            colored = (crop[:, :, 0] >= 95) & (crop[:, :, 0] <= 135) & (crop[:, :, 1] > 60) & ink
            color_share = float(colored.sum() / max(1, ink.sum()))
        mean_conf = float(np.mean([token.conf for token in line]))
        line_text = " ".join(t.text for t in line)
        noise_feature = 1.0 if any(m in line_text for m in ["%math%", "%NA%", "%SC%", "%unclear%"]) else 0.0
        score = score_line(mean_conf, component_heights, component_bottoms, line_height, color_share, noise_feature)
        scores[line_id] = score
        kind = "handwritten" if score >= 0.55 else "mixed" if score >= 0.40 else "printed"
        char_count = sum(len(token.text) for token in line)
        total += char_count
        if kind != "printed":
            weighted += char_count
        for token in line:
            token.kind = kind
            if kind == "handwritten":
                token.conf = min(token.conf, 0.80)

    # Clean and interpret tokens for semantic meaning
    processed_tokens = interpret_tokens(tokens)
    return processed_tokens, weighted / max(total, 1), scores


def apply_occlusion_confidence(tokens: list[Token], boxes: list[list[float]]) -> None:
    """Lower confidence for OCR words whose centers fall under detected blue stamps."""
    for token in tokens:
        center_x = (token.box[0] + token.box[2]) / 2
        center_y = (token.box[1] + token.box[3]) / 2
        if any(x0 <= center_x <= x1 and y0 <= center_y <= y1 for x0, y0, x1, y1 in boxes):
            token.conf *= 0.8
