"""Optional CPU-first RapidOCR adapter and engine protocol."""

from __future__ import annotations

from typing import Protocol, Any

import numpy as np

from vyom.models import Token


class OcrEngine(Protocol):
    name: str

    def detect(self, img: np.ndarray) -> list[Any]: ...
    def recognize(self, img: np.ndarray, boxes: list[Any], page: int = 1) -> list[Token]: ...
    def read(self, img: np.ndarray, page: int = 1) -> list[Token]: ...
    def available(self) -> bool: ...


class RapidOcrEngine:
    """RapidOCR ONNX runtime wrapper. Models load lazily and use CPU by default."""

    name = "rapidocr"

    def __init__(self) -> None:
        self._runtime: Any = None
        self._last: list[Any] = []
        self._import_error: str | None = None

    def _load(self) -> Any:
        if self._runtime is not None:
            return self._runtime
        try:
            try:
                from rapidocr import RapidOCR  # type: ignore[import-not-found]
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
            self._runtime = RapidOCR()
        except Exception as exc:  # optional engine failures are reported as degraded
            self._import_error = type(exc).__name__
            raise RuntimeError("RapidOCR is not available") from exc
        return self._runtime

    def detect(self, img: np.ndarray) -> list[Any]:
        self._last = self._run(img)
        return [entry[0] for entry in self._last]

    def recognize(self, img: np.ndarray, boxes: list[Any], page: int = 1) -> list[Token]:
        if not self._last:
            self._last = self._run(img)
        return self._tokens(self._last, img.shape[1], img.shape[0], page)

    def read(self, img: np.ndarray, page: int = 1) -> list[Token]:
        return self._tokens(self._run(img), img.shape[1], img.shape[0], page)

    def available(self) -> bool:
        try:
            self._load()
            return True
        except RuntimeError:
            return False

    def _run(self, img: np.ndarray) -> list[Any]:
        result = self._load()(img)
        if isinstance(result, tuple):
            result = result[0]
        if all(hasattr(result, name) for name in ("boxes", "txts", "scores")):
            boxes, texts, scores = result.boxes, result.txts, result.scores
            if boxes is None or texts is None or scores is None:
                return []
            return [[box, text, score] for box, text, score in zip(boxes, texts, scores)]
        return list(result or [])

    @staticmethod
    def _tokens(results: list[Any], width: int, height: int, page: int) -> list[Token]:
        tokens: list[Token] = []
        for entry in results:
            if not entry or len(entry) < 3:
                continue
            polygon, text, confidence = entry[0], str(entry[1]), float(entry[2])
            points = np.asarray(polygon, dtype=float).reshape(-1, 2)
            if not text.strip() or not len(points):
                continue
            x0, y0 = points.min(axis=0); x1, y1 = points.max(axis=0)
            tokens.append(Token(
                text=text, conf=min(0.97, max(0.0, confidence)),
                box=[max(0, x0 / width), max(0, y0 / height), min(1, x1 / width), min(1, y1 / height)],
                page=page, line_id=0, kind="printed", source="ocr",
            ))
        return merge_lines(tokens)


def read_with_orientation(engine: OcrEngine, image: np.ndarray, page: int = 1) -> tuple[list[Token], int]:
    """Choose a right-angle orientation on a thumbnail, then OCR the full page."""
    from PIL import Image

    height, width = image.shape[:2]
    scale = min(1.0, 1000 / max(height, width))
    thumbnail = np.asarray(Image.fromarray(image).resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)) if scale < 1 else image
    best_angle, best_score = 0, float("-inf")
    for angle in (0, 90, 180, 270):
        sample = np.rot90(thumbnail, k={0: 0, 90: 3, 180: 2, 270: 1}[angle]).copy()
        tokens = engine.read(sample, page=page)
        score = sum(token.conf * max(0.0, token.box[2] - token.box[0]) for token in tokens if (token.box[2] - token.box[0]) >= (token.box[3] - token.box[1]))
        if score > best_score:  # stable iteration means ties prefer 0 degrees
            best_angle, best_score = angle, score
    oriented = np.rot90(image, k={0: 0, 90: 3, 180: 2, 270: 1}[best_angle]).copy()
    return read_with_tiles(engine, oriented, page=page), best_angle


def read_with_tiles(engine: OcrEngine, image: np.ndarray, page: int = 1) -> list[Token]:
    """Read the full page and overlapping 2x2 tiles for large scans, then NMS."""
    height, width = image.shape[:2]
    full = engine.read(image, page=page)
    if max(height, width) <= 2500:
        return merge_lines(full)
    tile_width, tile_height = min(width, round(width * 0.55)), min(height, round(height * 0.55))
    xs = sorted(set((0, max(0, width - tile_width))))
    ys = sorted(set((0, max(0, height - tile_height))))
    candidates = list(full)
    for top in ys:
        for left in xs:
            crop = image[top:top + tile_height, left:left + tile_width]
            for token in engine.read(crop, page=page):
                x0, y0, x1, y1 = token.box
                token.box = [
                    min(1.0, max(0.0, (left + x0 * tile_width) / width)),
                    min(1.0, max(0.0, (top + y0 * tile_height) / height)),
                    min(1.0, max(0.0, (left + x1 * tile_width) / width)),
                    min(1.0, max(0.0, (top + y1 * tile_height) / height)),
                ]
                candidates.append(token)
    retained: list[Token] = []
    for token in sorted(candidates, key=lambda item: item.conf, reverse=True):
        if not any(_iou(token.box, kept.box) > 0.5 for kept in retained):
            retained.append(token)
    return merge_lines(retained)


def _iou(left: list[float], right: list[float]) -> float:
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1, y1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_left = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    area_right = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / max(area_left + area_right - intersection, 1e-12)


def merge_lines(tokens: list[Token]) -> list[Token]:
    """Assign stable line ids by baseline proximity and horizontal gap."""
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda t: ((t.box[1] + t.box[3]) / 2, t.box[0]))
    heights = [max(0.001, t.box[3] - t.box[1]) for t in ordered]
    h = float(np.median(heights))
    lines: list[list[Token]] = []
    for token in ordered:
        center = (token.box[1] + token.box[3]) / 2
        line = next((candidate for candidate in reversed(lines)
                     if abs((candidate[0].box[1] + candidate[0].box[3]) / 2 - center) <= 0.5 * h
                     and token.box[0] - max(t.box[2] for t in candidate) < 3 * h), None)
        if line is None:
            lines.append([token])
        else:
            line.append(token)
    flattened: list[Token] = []
    for line_id, line in enumerate(lines, 1):
        line.sort(key=lambda t: t.box[0])
        for token in line:
            token.line_id = line_id
            flattened.append(token)
    return flattened
