"""QR and GST e-invoice payload extraction (signature intentionally unverified)."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from vyom.models import BBox, RawField, RawInvoice

try:
    import zxingcpp  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    zxingcpp = None
try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    cv2 = None


def _decode_jwt(payload: str) -> dict[str, Any] | None:
    parts = payload.split(".")
    if len(parts) != 3:
        return None
    try:
        middle = parts[1] + "=" * (-len(parts[1]) % 4)
        content = json.loads(base64.urlsafe_b64decode(middle.encode("ascii")))
        data = content.get("data", content)
        if isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def _read_payloads(image: np.ndarray) -> list[tuple[str, list[float] | None]]:
    h, w = image.shape[:2]
    candidates: list[tuple[str, np.ndarray, float, int, int]] = []
    for factor in (1.0, 0.75, 1.5):
        resized = cv2.resize(image, None, fx=factor, fy=factor) if cv2 is not None else image
        candidates.append(("full", resized, factor if cv2 is not None else 1.0, 0, 0))
    for side in ("top", "bottom"):
        crop = image[:h // 2, w // 2:] if side == "top" else image[h // 2:, w // 2:]
        x_offset, y_offset = w // 2, 0 if side == "top" else h // 2
        if crop.size:
            candidates.append((side, cv2.resize(crop, None, fx=2, fy=2) if cv2 is not None else crop, 2.0 if cv2 is not None else 1.0, x_offset, y_offset))
    found: dict[str, list[float] | None] = {}
    for _where, sample, scale, x_offset, y_offset in candidates:
        if zxingcpp is not None:
            try:
                results = zxingcpp.read_barcodes(sample)
                for item in results or []:
                    text = str(getattr(item, "text", "") or "")
                    if text:
                        points = _zxing_points(getattr(item, "position", None))
                        found.setdefault(text, _normalized_box(points, scale, x_offset, y_offset, w, h))
            except Exception:
                pass
        if cv2 is not None:
            try:
                detector = cv2.QRCodeDetector()
                ok, texts, points, _ = detector.detectAndDecodeMulti(sample)
                if ok and points is not None:
                    for text, polygon in zip(texts, points):
                        if text:
                            point = np.asarray(polygon).reshape(-1, 2)
                            x0, y0 = point.min(axis=0); x1, y1 = point.max(axis=0)
                            found.setdefault(text, _normalized_box([[x0, y0], [x1, y1]], scale, x_offset, y_offset, w, h))
                else:
                    text, points, _ = detector.detectAndDecode(sample)
                    if text:
                        found.setdefault(text, None)
            except Exception:
                pass
    return list(found.items())


def _zxing_points(position: Any) -> list[list[float]]:
    if position is None:
        return []
    points = []
    for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
        point = getattr(position, name, None)
        if point is not None and hasattr(point, "x") and hasattr(point, "y"):
            points.append([float(point.x), float(point.y)])
    return points


def _normalized_box(points: list[list[float]], scale: float, x_offset: int, y_offset: int,
                    width: int, height: int) -> list[float] | None:
    if not points:
        return None
    values = np.asarray(points, dtype=float)
    x0, y0 = values.min(axis=0)
    x1, y1 = values.max(axis=0)
    return [
        min(1.0, max(0.0, (x_offset + x0 / scale) / width)),
        min(1.0, max(0.0, (y_offset + y0 / scale) / height)),
        min(1.0, max(0.0, (x_offset + x1 / scale) / width)),
        min(1.0, max(0.0, (y_offset + y1 / scale) / height)),
    ]


def extract_qr(image: np.ndarray, page: int = 1) -> list[RawInvoice]:
    """Return each decoded invoice QR as a RawInvoice; ignore payment values."""
    invoices: list[RawInvoice] = []
    for payload, box in _read_payloads(image):
        if payload.lower().startswith(("upi://", "http://", "https://")):
            parsed = urlparse(payload)
            if parsed.scheme.casefold() == "upi":
                params = parse_qs(parsed.query)
                note = "; ".join(f"{key}={params[key][0]}" for key in ("pa", "am") if params.get(key))
                if note:
                    x0, y0, x1, y1 = box or [0.0, 0.0, 1.0, 1.0]
                    invoices.append(RawInvoice(source="qr", invoice={"qr_note": [RawField(
                        value=None, confidence=0.0, source="qr", page=page,
                        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1), evidence_text=f"UPI payment QR note: {note}",
                    )]}))
            # URL and payment QR values remain evidence notes, never invoice fields.
            continue
        data = _decode_jwt(payload)
        if not data:
            continue
        left, top, right, bottom = box or [0.0, 0.0, 1.0, 1.0]
        bbox = BBox(page=page, x0=left, y0=top, x1=right, y1=bottom)
        fields: dict[str, list[RawField]] = {}
        mapping = {
            "SellerGstin": "supplier_gstin", "BuyerGstin": "buyer_gstin", "DocNo": "invoice_no",
            "DocDt": "invoice_date", "TotInvVal": "total_amount",
        }
        for source_key, field_name in mapping.items():
            value = data.get(source_key)
            if value is not None:
                fields[field_name] = [RawField(value=str(value), confidence=0.99, source="qr", page=page, bbox=bbox, evidence_text=f"QR {source_key}; IRN {data.get('Irn', '')}".strip())]
        doc_type = {"INV": "tax_invoice", "CRN": "credit_note", "DBN": "debit_note"}.get(str(data.get("DocTyp", "")).upper())
        if doc_type:
            fields["document_type"] = [RawField(value=doc_type, confidence=0.99, source="qr", page=page, bbox=bbox, evidence_text=f"QR DocTyp; IRN {data.get('Irn', '')}".strip())]
        if fields:
            invoices.append(RawInvoice(source="qr", invoice=fields))
    return invoices
