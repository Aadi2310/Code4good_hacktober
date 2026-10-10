from __future__ import annotations

import unittest

from vyom.docai.gate import gate_pages, invoice_signals
from vyom.models import PageData
from vyom.pdfio.classify import classify_page
from vyom.pdfio.digital import infer_column_grid


class FakePage:
    width = 100
    height = 100

    def __init__(self, text: str, images: list[dict] | None = None):
        self.text = text
        self.images = images or []

    def extract_text(self) -> str:
        return self.text


class ClassifierAndGateTests(unittest.TestCase):
    def test_digital_page_threshold(self) -> None:
        result = classify_page(FakePage("Tax invoice GSTIN HSN total amount " + "x" * 35))
        self.assertEqual(result.kind, "digital")
        self.assertGreaterEqual(result.chars, 50)

    def test_scanned_garbled_cid_layer(self) -> None:
        result = classify_page(FakePage("(cid:12) (cid:13) (cid:14) " + "x" * 10))
        self.assertEqual(result.kind, "scanned")
        self.assertGreater(result.garbled_ratio, 0.2)

    def test_mixed_threshold(self) -> None:
        result = classify_page(FakePage("A" * 25))
        self.assertEqual(result.kind, "mixed")

    def test_blank_and_carbon_copy_pages_are_skipped(self) -> None:
        pages = [self._page(1, ""), self._page(2, "Duplicate for transporter")]
        signals = gate_pages(pages)
        self.assertEqual(signals, set())
        self.assertTrue(all(page.kind == "skipped" for page in pages))
        self.assertTrue(all("PAGE_SKIPPED" in page.flags for page in pages))

    def test_duplicate_invoice_page_is_not_mistaken_for_a_cover_page(self) -> None:
        page = self._page(1, "TAX INVOICE\nGSTIN HSN Total Amount\nDuplicate for transporter")
        signals = gate_pages([page])
        self.assertGreaterEqual(len(signals), 3)
        self.assertNotEqual(page.kind, "skipped")

    def test_non_invoice_gate_has_fewer_than_three_signals(self) -> None:
        signals = invoice_signals("A packing list with an amount ₹100.00")
        self.assertLess(len(signals), 3)

    def test_digital_text_table_without_ruling_is_banded(self) -> None:
        words = []
        def add(text: str, x: float, y: float) -> None:
            words.append({"text": text, "x0": x, "x1": x + len(text) * 5, "top": y, "bottom": y + 10})
        for text, x in (("Sr", 10), ("Description", 100), ("HSN", 280), ("Qty", 380), ("Rate", 450), ("Amount", 540)):
            add(text, x, 100)
        for text, x in (("1", 10), ("Widget", 100), ("1234", 280), ("2", 380), ("500.00", 450), ("1000.00", 540)):
            add(text, x, 125)
        rows = infer_column_grid(words)
        self.assertEqual(len(rows), 2)
        self.assertIn("Widget", rows[1])

    @staticmethod
    def _page(index: int, text: str) -> PageData:
        return PageData(index=index, kind="digital", original_path="original.jpg", enhanced_path="page.jpg",
                        width=100, height=100, tokens=[], layout_text=text)


if __name__ == "__main__":
    unittest.main()
