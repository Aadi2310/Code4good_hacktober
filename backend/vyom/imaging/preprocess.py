"""Deterministic image loading and enhancement for OCR and evidence display."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageOps, ImageFilter

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised on minimal installations
    cv2 = None


@dataclass
class PreparedImage:
    original: np.ndarray
    enhanced: np.ndarray
    flags: list[str]
    quality: dict[str, Any]


def load_rgb(path: str | Path, max_edge: int = 3500) -> tuple[np.ndarray, list[str]]:
    """Load, EXIF-correct, flatten and bound an image as 8-bit RGB."""
    flags: list[str] = []
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        if image.mode in {"P", "LA", "RGBA"}:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba).convert("RGB")
        elif image.mode == "CMYK":
            image = image.convert("RGB")
        elif image.mode == "I;16":
            image = image.point(lambda value: value / 257).convert("L").convert("RGB")
        else:
            image = image.convert("RGB")
        width, height = image.size
        short, long = min(width, height), max(width, height)
        if short < 1000:
            image = image.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
            flags.append("LOW_RESOLUTION")
            short *= 2
            long *= 2
        if short < 600:
            flags.append("VERY_LOW_RESOLUTION")
        if long > max_edge:
            scale = max_edge / long
            image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.uint8), flags


def correct_perspective(image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Correct a clear four-corner page contour; leave uncertain frames untouched."""
    if cv2 is None:
        return image, {"perspective_corrected": False}
    height, width = image.shape[:2]
    scale = min(1.0, 1000 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median_value = float(np.median(blurred))
    edges = cv2.Canny(blurred, max(0, int(0.66 * median_value)), min(255, int(1.33 * median_value)))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidate = None
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        perimeter = cv2.arcLength(contour, True)
        quad = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(quad) == 4 and cv2.contourArea(quad) >= small.shape[0] * small.shape[1] * 0.25 and cv2.isContourConvex(quad):
            candidate = quad.reshape(4, 2).astype(np.float32) / scale
            break
    if candidate is None:
        return image, {"perspective_corrected": False}

    # Order corners as top-left, top-right, bottom-right, bottom-left.
    sums, diffs = candidate.sum(axis=1), np.diff(candidate, axis=1).reshape(-1)
    ordered = np.array([candidate[np.argmin(sums)], candidate[np.argmin(diffs)], candidate[np.argmax(sums)], candidate[np.argmax(diffs)]], dtype=np.float32)
    tl, tr, br, bl = ordered
    top = np.linalg.norm(tr - tl); bottom = np.linalg.norm(br - bl)
    left = np.linalg.norm(bl - tl); right = np.linalg.norm(br - tr)
    out_w, out_h = max(1, round(max(top, bottom))), max(1, round(max(left, right)))
    target = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(ordered, target)
    corrected = cv2.warpPerspective(image, transform, (out_w, out_h), borderMode=cv2.BORDER_REPLICATE)
    return corrected, {"perspective_corrected": True}


def rotate(image: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate clockwise by a right-angle orientation."""
    degrees %= 360
    if degrees == 90:
        return np.rot90(image, k=3).copy()
    if degrees == 180:
        return np.rot90(image, k=2).copy()
    if degrees == 270:
        return np.rot90(image, k=1).copy()
    return image


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate a modest text-line skew using Hough segments."""
    if cv2 is None:
        return image, 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    lines = cv2.HoughLinesP(binary, 1, np.pi / 1800, threshold=60, minLineLength=max(30, int(image.shape[1] * 0.15)), maxLineGap=20)
    angles: list[float] = []
    for segment in lines if lines is not None else []:
        x1, y1, x2, y2 = np.asarray(segment).reshape(-1)[:4]
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if -15 <= angle <= 15:
            angles.append(angle)
    angle = float(np.median(angles)) if angles else 0.0
    if 0.3 <= abs(angle) <= 15:
        matrix = cv2.getRotationMatrix2D((image.shape[1] / 2, image.shape[0] / 2), angle, 1.0)
        image = cv2.warpAffine(image, matrix, (image.shape[1], image.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        return image, angle
    return image, angle


def enhance(image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Improve local contrast and gently sharpen blurred pages."""
    if cv2 is None:
        pil = Image.fromarray(image)
        return np.asarray(ImageEnhance.Contrast(pil).enhance(1.15).filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=3))), {"blur_score": None, "contrast": None, "occlusion_ratio": 0.0, "occlusion_boxes": []}
    hsv_source = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    carbon_paper = float(np.mean(hsv_source[:, :, 1])) / 255 > 0.25
    if carbon_paper:
        scores = [_otsu_variance(image[:, :, channel]) for channel in range(3)]
        chosen = image[:, :, int(np.argmax(scores))]
        image = cv2.cvtColor(chosen, cv2.COLOR_GRAY2RGB)
    # Divide out slowly changing page illumination before local contrast equalization.
    short_edge = min(image.shape[:2])
    kernel_size = max(15, min(151, short_edge // 30))
    if kernel_size % 2 == 0:
        kernel_size += 1
    background = cv2.morphologyEx(image, cv2.MORPH_CLOSE, np.ones((kernel_size, kernel_size), dtype=np.uint8))
    balanced = cv2.divide(image, np.maximum(background, 1), scale=255)
    lab = cv2.cvtColor(balanced, cv2.COLOR_RGB2LAB)
    lightness, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lightness = clahe.apply(lightness)
    result = cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2RGB)
    gray = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if blur_score < 80:
        result = cv2.addWeighted(result, 1.6, cv2.GaussianBlur(result, (0, 0), 1.0), -0.6, 0)
    boxes, ratio = find_blue_occlusions(image)
    return result, {"blur_score": blur_score, "contrast": float(np.std(gray)), "occlusion_ratio": ratio, "occlusion_boxes": boxes, "carbon_paper": carbon_paper}


def _otsu_variance(channel: np.ndarray) -> float:
    """Between-class variance at Otsu's threshold for one color channel."""
    threshold, _ = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    histogram = cv2.calcHist([channel], [0], None, [256], [0, 256]).reshape(-1)
    total = float(histogram.sum())
    if total == 0:
        return 0.0
    values = np.arange(256, dtype=float)
    weight_background = float(histogram[:int(threshold) + 1].sum()) / total
    weight_foreground = 1.0 - weight_background
    if weight_background <= 0 or weight_foreground <= 0:
        return 0.0
    mean_background = float(np.dot(values[:int(threshold) + 1], histogram[:int(threshold) + 1]) / histogram[:int(threshold) + 1].sum())
    mean_foreground = float(np.dot(values[int(threshold) + 1:], histogram[int(threshold) + 1:]) / histogram[int(threshold) + 1:].sum())
    return weight_background * weight_foreground * (mean_background - mean_foreground) ** 2


def find_blue_occlusions(image: np.ndarray) -> tuple[list[list[float]], float]:
    """Find blue stamp-like regions without removing them from the evidence image."""
    if cv2 is None:
        return [], 0.0
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    saturated_share = float(np.mean(hsv[:, :, 1] > 80))
    if saturated_share > 0.5:
        return [], 0.0  # uniform colored carbon stock is not a stamp mask
    mask = cv2.inRange(hsv, np.array([100, 81, 0], dtype=np.uint8), np.array([140, 255, 255], dtype=np.uint8))
    count = int(np.count_nonzero(mask))
    ratio = count / max(1, mask.shape[0] * mask.shape[1])
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = mask.shape
    boxes: list[list[float]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h >= width * height * 0.005:
            boxes.append([x / width, y / height, (x + w) / width, (y + h) / height])
    return boxes, ratio


def enhance_handwriting(image: np.ndarray) -> np.ndarray:
    """Enhance faint handwriting strokes, reduce paper texture, and boost ink contrast."""
    if cv2 is None:
        pil = Image.fromarray(image)
        return np.asarray(ImageEnhance.Contrast(pil).enhance(1.25).filter(ImageFilter.SHARPEN))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    bg = cv2.morphologyEx(gray, cv2.MORPH_DILATE, np.ones((5, 5), np.uint8))
    diff = cv2.absdiff(gray, bg)
    norm = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    inverted = cv2.cvtColor(255 - norm, cv2.COLOR_GRAY2RGB)
    return cv2.addWeighted(image, 0.65, inverted, 0.35, 0)


def prepare_image(path: str | Path, orientation: int = 0, expect_handwritten: bool = False) -> PreparedImage:
    original, flags = load_rgb(path)
    image, perspective = correct_perspective(original)
    image = rotate(image, orientation)
    if orientation:
        flags.append(f"ROTATED_{orientation % 360}")
    image, skew = deskew(image)
    enhanced, quality = enhance(image)
    if expect_handwritten:
        enhanced = enhance_handwriting(enhanced)
        flags.append("HANDWRITING_ENHANCED")
    return PreparedImage(
        original=original,
        enhanced=enhanced,
        flags=flags + (["PERSPECTIVE_CORRECTED"] if perspective["perspective_corrected"] else []),
        quality={**perspective, **quality, "skew_deg": skew, "orientation": orientation % 360, "dpi_estimate": None},
    )


def prepare_pdf_image(path: str | Path, orientation: int = 0, deskew_enabled: bool = True) -> PreparedImage:
    """Prepare a rasterized PDF page without perspective warps that would misalign PDF text boxes."""
    original, flags = load_rgb(path)
    image = rotate(original, orientation)
    if orientation:
        flags.append(f"ROTATED_{orientation % 360}")
    skew = 0.0
    if deskew_enabled:
        image, skew = deskew(image)
    enhanced, quality = enhance(image)
    return PreparedImage(
        original=original,
        enhanced=enhanced,
        flags=flags,
        quality={"perspective_corrected": False, **quality, "skew_deg": skew,
                 "orientation": orientation % 360, "dpi_estimate": None},
    )
