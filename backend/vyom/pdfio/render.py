"""PDF page rasterization using PDFium, bounded for safe browser previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def render_page(pdf: Any, page_index: int, output_path: str | Path, dpi: int = 300, max_edge: int = 3500) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one PDFium page to RGB JPEG and report its effective DPI."""
    page = pdf[page_index]
    scale = dpi / 72
    bitmap = page.render(scale=scale, rotation=0)
    image = bitmap.to_pil().convert("RGB")
    longest = max(image.size)
    if longest > max_edge:
        ratio = max_edge / longest
        image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
    image.save(output_path, format="JPEG", quality=92, optimize=True)
    width, height = image.size
    actual_dpi = dpi * (max_edge / longest) if longest > max_edge else dpi
    return np.asarray(image, dtype=np.uint8), {"width": width, "height": height, "dpi_estimate": actual_dpi}
