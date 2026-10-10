"""PDF/image-to-Bundle pipeline. OCR and QR backends remain optional at import time."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from vyom.models import Bundle, PageData, Token
from vyom.docai._compat import PipelineError
from vyom.docai.gate import gate_pages
from vyom.docai.segment import segment_pages
from vyom.extraction.qr import extract_qr
from vyom.handwriting.detect import apply_occlusion_confidence, classify_handwriting
from vyom.imaging.preprocess import prepare_image, prepare_pdf_image
from vyom.ocr.engine import RapidOcrEngine, read_with_orientation, read_with_tiles
from vyom.pdfio.classify import classify_page
from vyom.pdfio.digital import extract_digital_page
from vyom.pdfio.render import render_page

_ENGINE: RapidOcrEngine | None = None
MAX_PAGES = 100
MAX_IMAGE_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _engine() -> RapidOcrEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOcrEngine()
    return _ENGINE


def warmup() -> None:
    """Load the default OCR model once; raise only if explicitly requested by caller."""
    _engine()._load()


def available() -> dict[str, bool]:
    """Report optional P2 engines without making the document path depend on them."""
    engine = _engine()
    try:
        from vyom.handwriting import available as hw_available
        has_hw = hw_available()
    except Exception:
        has_hw = False
    return {"ocr": engine.available(), "trocr": has_hw}


def build_bundle(path: Path, kind: str, work_dir: Path, options: dict) -> Bundle:
    """Build a page-evidence bundle from a PDF or a JPEG/PNG image."""
    path, work_dir = Path(path), Path(work_dir)
    if not path.is_file():
        raise PipelineError("NO_FILE", "The source file does not exist.")
    if path.stat().st_size == 0:
        raise PipelineError("EMPTY_FILE", "The source file is empty.")
    kind = str(kind).lower().lstrip(".")
    if kind in {"jpg", "jpeg", "webp"}:
        kind = "jpeg"
    if kind not in {"pdf", "jpeg", "png"}:
        raise PipelineError("UNSUPPORTED_FORMAT", "Document AI accepts PDF, JPEG, and PNG files.", {"kind": kind})
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    reasons: list[str] = []
    try:
        from vyom.handwriting import available as hw_available
        has_hw = hw_available()
    except Exception:
        has_hw = False
    engines: dict[str, str] = {"handwriting": "trained_gnhk" if has_hw else "heuristic"}
    pages: list[PageData] = []
    qrs: dict[int, list] = {}
    handwriting_weight = total_chars = 0
    image_input = kind != "pdf"
    engine = _engine()
    engine_available = engine.available()
    engines["ocr"] = "rapidocr" if engine_available else "unavailable"

    if image_input:
        _build_image(path, work_dir, pages, qrs, options, engine, engine_available)
    else:
        _build_pdf(path, work_dir, pages, qrs, options, engine, engine_available, reasons)

    for page in pages:
        if page.kind in {"scanned", "mixed"} and page.tokens:
            tokens, ratio, _scores = classify_handwriting(page.tokens, _read_rgb(page.enhanced_path))
            apply_occlusion_confidence(tokens, page.quality.get("occlusion_boxes", []))
            page.tokens = tokens
            page.quality["handwriting_ratio"] = ratio
            char_count = sum(len(token.text) for token in tokens)
            handwriting_weight += int(char_count * ratio)
            total_chars += char_count
        else:
            page.quality.setdefault("handwriting_ratio", 0.0)
    handwriting_ratio = handwriting_weight / max(total_chars, 1)
    if (handwriting_ratio >= 0.15 or options.get("expect_handwritten")) and not has_hw:
        reasons.append("TROCR_UNAVAILABLE")

    signals = gate_pages(pages)
    if len(signals) < 3:
        seen = sorted(signals)
        raise PipelineError("NOT_AN_INVOICE", f"Invoice gate found only {len(seen)} distinct signals: {', '.join(seen) or 'none'}.", {"signals": seen})
    segments = segment_pages(pages, qrs)
    if not segments:
        raise PipelineError("NOT_AN_INVOICE", "No invoice pages were found.", {"signals": sorted(signals)})

    if image_input:
        input_kind = "image_printed" if handwriting_ratio < 0.15 else "image_handwritten" if handwriting_ratio >= 0.5 else "image_mixed"
    else:
        kinds = {page.kind for page in pages if page.kind != "skipped"}
        input_kind = "pdf_digital" if kinds == {"digital"} else "pdf_scanned" if kinds == {"scanned"} else "pdf_mixed"
    if options.get("expect_handwritten"):
        for page in pages:
            if page.kind in {"scanned", "mixed"}:
                page.flags.append("HANDWRITING_EXPECTED")
    elapsed = int((time.perf_counter() - started) * 1000)
    timings = {"docai_total": elapsed}
    return Bundle(
        input_kind=input_kind, pages=pages, segments=segments, handwriting_ratio=handwriting_ratio,
        degraded_reasons=sorted(set(reasons)), engines=engines, timings_ms=timings,
    )


def _build_image(path: Path, work_dir: Path, pages: list[PageData], qrs: dict, options: dict, engine: RapidOcrEngine, engine_available: bool) -> None:
    image, _ = _read_source_image(path)
    if image is None:
        raise PipelineError("CORRUPT_FILE", "The image could not be decoded.")
    if max(image.shape[:2]) > 10000:
        raise PipelineError("IMAGE_TOO_LARGE", "The decoded image dimensions exceed the supported limit.")
    enhanced_path, original_path = work_dir / "page-1.jpg", work_dir / "page-1-original.jpg"
    # Save an original RGB page before preprocessing; EXIF orientation is represented in the pixels.
    Image.fromarray(image).save(original_path, format="JPEG", quality=92, optimize=True)
    prepared = prepare_image(original_path, orientation=0)
    orientation = 0
    if engine_available:
        tokens, orientation = read_with_orientation(engine, prepared.enhanced, 1)
        if orientation:
            prepared = prepare_image(original_path, orientation=orientation)
            tokens = read_with_tiles(engine, prepared.enhanced, page=1)
    else:
        raise PipelineError("NO_EXTRACTOR_AVAILABLE", "RapidOCR is unavailable for this image.", {"degraded_reasons": ["OCR_UNAVAILABLE"]})
    prepared.enhanced = _apply_illumination(prepared.enhanced)
    Image.fromarray(prepared.enhanced).save(enhanced_path, format="JPEG", quality=92, optimize=True)
    layout = _tokens_to_layout(tokens)
    page = PageData(index=1, kind="scanned", original_path=str(original_path), enhanced_path=str(enhanced_path),
                    width=prepared.enhanced.shape[1], height=prepared.enhanced.shape[0], tokens=tokens,
                    layout_text=layout, flags=prepared.flags + (["STAMP_OCCLUSION"] if prepared.quality.get("occlusion_boxes") else []), quality=prepared.quality)
    pages.append(page)
    qrs[1] = extract_qr(prepared.enhanced, page=1)
    _write_ocr_json(work_dir, pages)


def _build_pdf(path: Path, work_dir: Path, pages: list[PageData], qrs: dict, options: dict, engine: RapidOcrEngine, engine_available: bool, reasons: list[str]) -> None:
    try:
        import pdfplumber  # type: ignore[import-not-found]
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PipelineError("NO_EXTRACTOR_AVAILABLE", f"Required PDF support is unavailable: {type(exc).__name__}.") from exc
    try:
        with pdfplumber.open(path, password=options.get("pdf_password")) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise PipelineError("TOO_MANY_PAGES", f"The PDF exceeds the {MAX_PAGES}-page limit.")
            classifications = [classify_page(page) for page in pdf.pages]
            pdfium_doc = pdfium.PdfDocument(str(path), password=options.get("pdf_password"))
            if len(pdfium_doc) > MAX_PAGES:
                raise PipelineError("TOO_MANY_PAGES", f"The PDF exceeds the {MAX_PAGES}-page limit.")
            needs_ocr = any(item.kind in {"scanned", "mixed"} for item in classifications)
            if needs_ocr and not engine_available and any(item.kind == "scanned" for item in classifications):
                raise PipelineError("NO_EXTRACTOR_AVAILABLE", "RapidOCR is unavailable and the PDF has no usable text layer.", {"degraded_reasons": ["OCR_UNAVAILABLE"]})
            if needs_ocr and not engine_available:
                reasons.append("OCR_UNAVAILABLE")
            for index, (pdf_page, classification) in enumerate(zip(pdf.pages, classifications), 1):
                original_path, enhanced_path = work_dir / f"page-{index}-original.jpg", work_dir / f"page-{index}.jpg"
                try:
                    rendered, render_info = render_page(pdfium_doc, index - 1, original_path)
                except Exception as exc:
                    if classification.kind == "digital":
                        raise PipelineError("CORRUPT_FILE", f"PDF page {index} could not be rendered.") from exc
                    raise
                prepared = prepare_pdf_image(original_path, deskew_enabled=classification.kind == "scanned")
                tokens: list[Token] = []
                layout = ""
                if classification.kind in {"digital", "mixed"}:
                    tokens, layout, _ = extract_digital_page(pdf_page, index)
                if classification.kind in {"scanned", "mixed"} and engine_available:
                    ocr_tokens, orientation = read_with_orientation(engine, prepared.enhanced, index)
                    if orientation:
                        prepared = prepare_pdf_image(original_path, orientation, deskew_enabled=classification.kind == "scanned")
                        ocr_tokens = read_with_tiles(engine, prepared.enhanced, page=index)
                        if classification.kind == "mixed":
                            _rotate_pdf_tokens(tokens, orientation)
                    if classification.kind == "mixed":
                        tokens.extend(ocr_tokens)
                        # Keep PDF-text and OCR lines separate but stable on the same page.
                        offset = max((token.line_id for token in tokens if token.source == "pdf_text"), default=0)
                        for token in ocr_tokens:
                            token.line_id += offset
                    else:
                        tokens = ocr_tokens
                    layout = _tokens_to_layout(tokens)
                elif classification.kind == "scanned" and not engine_available:
                    reasons.append("OCR_UNAVAILABLE")
                enhanced = _apply_illumination(prepared.enhanced)
                Image.fromarray(enhanced).save(enhanced_path, format="JPEG", quality=92, optimize=True)
                page_kind = classification.kind
                flags = list(prepared.flags)
                if classification.chars == 0:
                    flags.append("EMPTY_TEXT_LAYER")
                page = PageData(index=index, kind=page_kind, original_path=str(original_path), enhanced_path=str(enhanced_path),
                                width=enhanced.shape[1], height=enhanced.shape[0], tokens=tokens, layout_text=layout,
                                flags=flags + (["STAMP_OCCLUSION"] if prepared.quality.get("occlusion_boxes") else []), quality={**prepared.quality, **render_info, "garbled_ratio": classification.garbled_ratio,
                                                     "image_coverage": classification.image_coverage})
                pages.append(page)
                qrs[index] = extract_qr(enhanced, page=index)
            _write_ocr_json(work_dir, pages)
            if _has_embedded_attachments(pdf):
                reasons.append("EMBEDDED_ATTACHMENT_IGNORED")
            pdfium_doc.close()
    except PipelineError:
        if "pdfium_doc" in locals():
            pdfium_doc.close()
        raise
    except Exception as exc:
        if "pdfium_doc" in locals():
            try:
                pdfium_doc.close()
            except Exception:
                pass
        message = str(exc).casefold()
        code = "PDF_ENCRYPTED" if "password" in message or "encrypt" in message else "CORRUPT_FILE"
        raise PipelineError(code, "The PDF could not be opened or parsed.") from exc


def _read_source_image(path: Path) -> tuple[np.ndarray | None, list[str]]:
    try:
        with Image.open(path) as source:
            if max(source.size) > 10000 or source.width * source.height > MAX_IMAGE_PIXELS:
                raise PipelineError("IMAGE_TOO_LARGE", "The decoded image exceeds the supported pixel limit.")
            source = source if source.mode == "RGB" else source.convert("RGBA" if source.mode in {"P", "LA", "RGBA"} else "RGB")
            from PIL import ImageOps
            source = ImageOps.exif_transpose(source)
            if source.mode == "RGBA":
                background = Image.new("RGBA", source.size, "white")
                source = Image.alpha_composite(background, source).convert("RGB")
            return np.asarray(source.convert("RGB"), dtype=np.uint8), []
    except PipelineError:
        raise
    except Image.DecompressionBombError as exc:
        raise PipelineError("IMAGE_TOO_LARGE", "The image exceeds Pillow's pixel-safety limit.") from exc
    except Exception:
        return None, []


def _read_rgb(path: str) -> np.ndarray:
    with Image.open(path) as source:
        return np.asarray(source.convert("RGB"), dtype=np.uint8)


def _apply_illumination(image: np.ndarray) -> np.ndarray:
    """Return the contrast-enhanced RGB image; kept separate for unit-level tests."""
    prepared = prepare_image_from_array(image)
    return prepared


def prepare_image_from_array(image: np.ndarray) -> np.ndarray:
    from vyom.imaging.preprocess import enhance
    enhanced, _ = enhance(image)
    return enhanced


def _tokens_to_layout(tokens: list[Token]) -> str:
    by_line: dict[tuple[int, int], list[Token]] = {}
    for token in tokens:
        by_line.setdefault((token.page, token.line_id), []).append(token)
    lines = []
    for (_, line_id), line in sorted(by_line.items()):
        line.sort(key=lambda token: token.box[0])
        lines.append(f"L{line_id:03d}| " + " ".join(token.text for token in line))
    return "\n".join(lines)


def _rotate_pdf_tokens(tokens: list[Token], degrees: int) -> None:
    """Rotate embedded-text boxes when OCR forces a right-angle mixed-page rotation."""
    for token in tokens:
        if token.source != "pdf_text":
            continue
        x0, y0, x1, y1 = token.box
        if degrees % 360 == 90:
            token.box = [1 - y1, x0, 1 - y0, x1]
        elif degrees % 360 == 180:
            token.box = [1 - x1, 1 - y1, 1 - x0, 1 - y0]
        elif degrees % 360 == 270:
            token.box = [y0, 1 - x1, y1, 1 - x0]


def _write_ocr_json(work_dir: Path, pages: list[PageData]) -> None:
    payload = [{"index": p.index, "kind": p.kind, "tokens": [t.model_dump() for t in p.tokens], "layout_text": p.layout_text} for p in pages]
    (work_dir / "ocr.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_embedded_attachments(pdf: Any) -> bool:
    try:
        return bool(getattr(pdf, "attachments", None))
    except Exception:
        return False
