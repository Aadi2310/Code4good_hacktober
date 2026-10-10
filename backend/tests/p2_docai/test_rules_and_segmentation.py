from __future__ import annotations

import unittest

from vyom.docai.segment import segment_pages
from vyom.docai.pipeline import _rotate_pdf_tokens
from vyom.extraction.rules import extract_rules
from vyom.models import PageData, Token


class RulesAndSegmentationTests(unittest.TestCase):
    def test_anchored_headers_gstins_totals_and_table_rows(self) -> None:
        layout = "\n".join([
            "L001| TAX INVOICE",
            "L002| Invoice No: INV-42",
            "L003| Invoice Date: 04/05/2026",
            "L004| Supplier GSTIN: 27ABCDE1234F1Z5",
            "L005| Buyer GSTIN: 29FGHIJ5678K1Z2",
            "L006| Grand Total: ₹ 1180.00",
            "| Sr No | Description | HSN | Qty | Rate | Amount |",
            "| 1 | Widget | 1234 | 2 | 500.00 | 1000.00 |",
        ])
        page = PageData(index=1, kind="digital", original_path="original.jpg", enhanced_path="page.jpg",
                        width=1000, height=1400, tokens=[], layout_text=layout)
        raw = extract_rules([page])
        self.assertEqual(raw.invoice["invoice_no"][0].value, "INV-42")
        self.assertEqual(raw.invoice["supplier_gstin"][0].value, "27ABCDE1234F1Z5")
        self.assertEqual(raw.invoice["buyer_gstin"][0].value, "29FGHIJ5678K1Z2")
        self.assertEqual(raw.invoice["total_amount"][0].value, "₹ 1180.00")
        self.assertEqual(len(raw.line_items), 1)
        self.assertEqual(raw.line_items[0]["description"].value, "Widget")

    def test_multi_invoice_pages_split_on_changed_number(self) -> None:
        first = self._page(1, "TAX INVOICE\nInvoice No: A-1\nSupplier GSTIN: 27ABCDE1234F1Z5\nGSTIN HSN TOTAL")
        second = self._page(2, "TAX INVOICE\nInvoice No: B-2\nSupplier GSTIN: 27ABCDE1234F1Z5\nGSTIN HSN TOTAL")
        segments = segment_pages([first, second], {})
        self.assertEqual([segment.page_range for segment in segments], [[1, 1], [2, 2]])

    def test_same_invoice_copy_pages_merge(self) -> None:
        first = self._page(1, "TAX INVOICE\nInvoice No: A-1\nSupplier GSTIN: 27ABCDE1234F1Z5\nGrand Total: 1180.00")
        copy = self._page(2, "TAX INVOICE\nInvoice No: A-1\nSupplier GSTIN: 27ABCDE1234F1Z5\nGrand Total: 1180.00\nDuplicate for transporter")
        minority = self._page(3, "TAX INVOICE\nInvoice No: A-1\nSupplier GSTIN: 27ABCDE1234F1Z5\nGrand Total: 1181.00\nTriplicate for supplier")
        segments = segment_pages([first, copy, minority], {})
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].page_range, [1, 3])
        self.assertEqual([field.value for field in segments[0].rules.invoice["total_amount"]], ["1180.00", "1181.00"])

    def test_carried_forward_table_rows_are_excluded_and_flagged(self) -> None:
        page = self._page(1, "\n".join((
            "| Sr No | Description | HSN | Qty | Amount |",
            "| 1 | Widget | 1234 | 2 | 1000.00 |",
            "| | Carried forward | | | 1000.00 |",
        )))
        raw = extract_rules([page])
        self.assertEqual(len(raw.line_items), 1)
        self.assertIn("CARRY_FORWARD_ROW_IGNORED", page.flags)

    def test_ocr_table_column_bands_and_wrapped_descriptions(self) -> None:
        entries = [
            ("Sr", 0.05, 0.08, 1, 0.10), ("No", 0.09, 0.12, 1, 0.10),
            ("Description", 0.20, 0.35, 1, 0.10), ("HSN", 0.45, 0.50, 1, 0.10),
            ("Qty", 0.60, 0.64, 1, 0.10), ("Rate", 0.70, 0.76, 1, 0.10),
            ("Amount", 0.84, 0.92, 1, 0.10),
            ("1", 0.05, 0.08, 2, 0.20), ("Widget", 0.20, 0.35, 2, 0.20),
            ("1234", 0.45, 0.50, 2, 0.20), ("2", 0.60, 0.64, 2, 0.20),
            ("500.00", 0.70, 0.76, 2, 0.20), ("1000.00", 0.84, 0.92, 2, 0.20),
            ("premium", 0.20, 0.32, 3, 0.24), ("grade", 0.33, 0.40, 3, 0.24),
        ]
        tokens = [Token(text=text, conf=0.8, box=[left, y, right, y + 0.02], page=1, line_id=line, source="ocr")
                  for text, left, right, line, y in entries]
        page = PageData(index=1, kind="scanned", original_path="original.jpg", enhanced_path="page.jpg",
                        width=1000, height=1000, tokens=tokens, layout_text="")
        raw = extract_rules([page])
        self.assertEqual(len(raw.line_items), 1)
        self.assertEqual(raw.line_items[0]["description"].value, "Widget premium grade")
        self.assertEqual(raw.line_items[0]["quantity"].value, "2")
        self.assertLess(raw.line_items[0]["description"].bbox.x0, 0.4)

    def test_mixed_page_embedded_text_boxes_follow_right_angle_rotation(self) -> None:
        embedded = Token(text="TOTAL", conf=0.98, box=[0.1, 0.2, 0.3, 0.4], page=1, line_id=1, source="pdf_text")
        recognized = Token(text="TOTAL", conf=0.8, box=[0.5, 0.6, 0.7, 0.8], page=1, line_id=2, source="ocr")
        _rotate_pdf_tokens([embedded, recognized], 90)
        self.assertEqual(embedded.box, [0.6, 0.1, 0.8, 0.3])
        self.assertEqual(recognized.box, [0.5, 0.6, 0.7, 0.8])

    @staticmethod
    def _page(index: int, text: str) -> PageData:
        return PageData(index=index, kind="digital", original_path=f"original-{index}.jpg", enhanced_path=f"page-{index}.jpg",
                        width=1000, height=1400, tokens=[], layout_text=text)


if __name__ == "__main__":
    unittest.main()
