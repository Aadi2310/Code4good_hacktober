from __future__ import annotations

import base64
import json
import unittest

import pytest

np = pytest.importorskip("numpy", reason="P2 imaging dependencies not installed")
zxingcpp = pytest.importorskip("zxingcpp", reason="P2 zxing-cpp not installed")
from PIL import Image

from vyom.extraction.qr import extract_qr


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class QrTests(unittest.TestCase):
    def test_gst_einvoice_jwt_maps_contract_fields_without_signature_validation(self) -> None:
        data = {
            "SellerGstin": "27ABCDE1234F1Z5", "BuyerGstin": "29FGHIJ5678K1Z2", "DocNo": "INV-42",
            "DocDt": "04/05/2026", "TotInvVal": 1180.0, "DocTyp": "INV", "Irn": "a" * 64,
        }
        payload = _b64('{"alg":"none"}') + "." + _b64(json.dumps({"data": json.dumps(data)})) + ".unsigned"
        barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.QRCode)
        image = np.asarray(Image.fromarray(np.asarray(barcode.to_image(scale=8))).convert("RGB"))
        invoices = extract_qr(image, page=3)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0].invoice["supplier_gstin"][0].value, "27ABCDE1234F1Z5")
        self.assertEqual(invoices[0].invoice["invoice_no"][0].value, "INV-42")
        self.assertEqual(invoices[0].invoice["document_type"][0].value, "tax_invoice")
        self.assertEqual(invoices[0].invoice["total_amount"][0].confidence, 0.99)
        self.assertEqual(invoices[0].invoice["invoice_date"][0].page, 3)
        self.assertLess(invoices[0].invoice["invoice_no"][0].bbox.x1 - invoices[0].invoice["invoice_no"][0].bbox.x0, 1.0)

    def test_upi_qr_is_saved_as_a_note_not_invoice_fields(self) -> None:
        barcode = zxingcpp.create_barcode("upi://pay?pa=acct%40bank&am=125.50", zxingcpp.BarcodeFormat.QRCode)
        image = np.asarray(Image.fromarray(np.asarray(barcode.to_image(scale=8))).convert("RGB"))
        invoices = extract_qr(image, page=1)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(set(invoices[0].invoice), {"qr_note"})
        self.assertIn("pa=acct@bank", invoices[0].invoice["qr_note"][0].evidence_text)
        self.assertIn("am=125.50", invoices[0].invoice["qr_note"][0].evidence_text)


if __name__ == "__main__":
    unittest.main()
