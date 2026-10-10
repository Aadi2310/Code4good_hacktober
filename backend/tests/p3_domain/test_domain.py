from __future__ import annotations

import unittest
import sys
import types
from decimal import Decimal

from vyom.domain.dates import parse_date
from vyom.domain.gstin import check_char, is_valid, make_gstin
from vyom.domain.money import parse_money
from vyom.domain.normalize import normalize_field
from vyom.validation.confidence import combine_confidences
from vyom.validation.engine import validate
from vyom.validation.repair import gstin_candidates
from vyom.extraction.grounding import ground_value
from vyom.extraction.merge import merge_hypotheses
from vyom.extraction.vlm import get_extractor, _validate_output
from vyom.domain.common import load_reference
from vyom.domain.words import parse_amount_words, to_words
from unittest.mock import patch


class DomainTests(unittest.TestCase):
    def test_gstin_vectors_and_generation(self):
        for value in ("27AAPFU0939F1ZV", "29ABCDE1234F1ZW", "07AAACR5055K1Z9", "33AAACH7409R1Z8", "24AABCU9603R1ZT"):
            self.assertTrue(is_valid(value), value)
            self.assertEqual(check_char(value[:14]), value[-1])
        for value in ("27AAPFU0939F1ZX", "27AAPFU0939F1YV", "00AAPFU0939F1ZV", "27AAPFU093F91ZV"):
            self.assertFalse(is_valid(value), value)
        invalid_export_prefix = "96AAPFU0939F1Z"
        self.assertFalse(is_valid(invalid_export_prefix + check_char(invalid_export_prefix)))
        with self.assertRaises(ValueError):
            make_gstin("96", "AAPFU0939F")
        self.assertTrue(is_valid(make_gstin("MH", "AAPFU0939F")))

    def test_money_and_date_normalization(self):
        self.assertEqual(parse_money("₹ 12,34,567.505")[0], "1234567.51")
        self.assertEqual(parse_money("(1,234.50)")[0], "-1234.50")
        self.assertEqual(parse_money("1.234,56", locale_hint="eu")[0], "1234.56")
        self.assertIsNone(parse_money("12,34,56")[0])
        self.assertEqual(parse_money("123-45")[0], "123.45")
        self.assertEqual(parse_money("Rs. 1O0/-")[0], "100.00")
        self.assertIn("OCR_CONFUSION_USED", parse_money("Rs. 1O0/-")[1])
        value, notes = parse_date("03/04/2025")
        self.assertEqual(value, "2025-04-03")
        self.assertIn("DATE_AMBIGUOUS_DAY_FIRST", notes)
        self.assertEqual(parse_date("31/02/2025")[0], None)
        self.assertEqual(normalize_field("invoice_date", "03/04/2025")[0], value)
        words, confidence = parse_amount_words("one lakh twenty-three thousand fourty rupees and fiften paise only")
        self.assertEqual(words, Decimal("123040.15"))
        self.assertGreaterEqual(confidence, .85)
        self.assertEqual(parse_amount_words("one unicorn rupees")[0], None)
        self.assertEqual(parse_amount_words(to_words(Decimal("118.50")))[0], Decimal("118.50"))

    def test_merge_confidence_and_qr_precedence(self):
        self.assertGreater(combine_confidences([("ocr", .8), ("rules", .75), ("qr", .99)]), .99 - 1e-9)
        self.assertEqual(combine_confidences([("ocr", .8), ("rules", .75)]), .8)
        merged = merge_hypotheses([
            {"value": "INV-1", "source": "qr", "confidence": .99},
            {"value": "INV-2", "source": "rules", "confidence": .99},
        ], "invoice_no")
        self.assertEqual(merged["value"], "INV-1")
        self.assertEqual(len(merged["alternatives"]), 1)

    def test_grounding(self):
        result = ground_value("27AAPFU0939F1ZV", [{"text": "27AAPFU0939F1ZV", "page": 1, "box": [.1,.1,.3,.2]}])
        self.assertTrue(result["grounded"])
        self.assertEqual(result["bbox"]["page"], 1)
        self.assertFalse(ground_value("unknown", [{"text":"other"}])["grounded"])

    def test_reference_data_and_vlm_disabled(self):
        self.assertTrue(load_reference("state_codes.json")["38"]["utgst"])
        with patch.dict("os.environ", {"VLM_PROVIDER": "none"}):
            self.assertIsNone(get_extractor())
        output = _validate_output({"invoice": {"invoice_no": "A1", "bad_key": 5, "total_amount": {"oops": 1}}, "line_items": [{"quantity": "2", "bad": 3}], "illegible": ["invoice.total_amount", 2]}, {"invoice_fields": ["invoice_no", "total_amount"], "line_fields": ["quantity"]})
        self.assertEqual(output, {"invoice": {"invoice_no": "A1"}, "line_items": [{"quantity": "2"}], "illegible": ["invoice.total_amount"]})

    def test_tax_type_and_arithmetic_errors(self):
        field = lambda value: {"value": value, "confidence": .99, "status": "accepted", "sources": ["excel"], "alternatives": []}
        record = {"invoice": {"invoice_no": field("A"), "invoice_date": field("2025-01-01"), "document_type": field("tax_invoice"),
            "supplier_gstin": field("27AAPFU0939F1ZV"), "buyer_gstin": field(None), "place_of_supply": field("29-Karnataka"),
            "taxable_value": field("100.00"), "discount": field("0.00"), "cgst": field("9.00"), "sgst": field("9.00"),
            "igst": field("0.00"), "cess": field("0.00"), "total_tax": field("18.00"), "round_off": field("0.00"),
            "total_amount": field("130.00"), "reverse_charge": field("false")}, "line_items": [], "validation": {}, "quality": {}, "review": {}}
        result = validate(record, repair=False)
        codes = {check["code"] for check in result["validation"]["checks"] if not check["passed"]}
        self.assertIn("TAX_TYPE_MISMATCH", codes)
        self.assertIn("GRAND_TOTAL", codes)

    def test_rules_only_extraction_builds_complete_record(self):
        header = ["invoice_no", "invoice_date", "due_date", "document_type", "currency", "place_of_supply", "purchase_order_no", "reverse_charge", "price_includes_tax", "supplier_name", "supplier_address", "supplier_gstin", "buyer_name", "buyer_address", "buyer_gstin", "subtotal", "discount", "taxable_value", "cgst", "sgst", "igst", "cess", "total_tax", "round_off", "total_amount", "amount_in_words"]
        line_fields = ["line_no", "description", "hsn_sac", "quantity", "unit", "unit_price", "discount", "taxable_value", "tax_rate", "cgst", "sgst", "igst", "cess", "line_total"]
        class Model:
            def __init__(self, **kwargs): self.__dict__.update(kwargs)
        fake = types.ModuleType("vyom.models")
        fake.HEADER_FIELDS = header; fake.LINE_FIELDS = line_fields
        fake.FV = type("FV", (Model,), {})
        fake.Alt = type("Alt", (Model,), {})
        fake.Validation = type("Validation", (Model,), {"__init__": lambda self, **kw: Model.__init__(self, **({"status":"needs_review", "score":0, "checks":[], "corrections":[], "repair_candidates":[]} | kw))})
        fake.Review = type("Review", (Model,), {"__init__": lambda self, **kw: Model.__init__(self, **({"required":True, "state":"pending", "history":[]} | kw))})
        fake.RecordSource = type("RecordSource", (Model,), {})
        fake.Quality = type("Quality", (Model,), {"__init__": lambda self, **kw: Model.__init__(self, **({"overall_confidence":0} | kw))})
        fake.InvoiceRecord = type("InvoiceRecord", (Model,), {})
        previous = sys.modules.get("vyom.models")
        sys.modules["vyom.models"] = fake
        try:
            from vyom.extraction.merge import run
            values = {"invoice_no":"INV-1", "invoice_date":"2025-04-03", "document_type":"tax_invoice", "supplier_gstin":"27AAPFU0939F1ZV", "taxable_value":"100.00", "total_amount":"118.00", "cgst":"9.00", "sgst":"9.00", "igst":"0.00", "cess":"0.00", "total_tax":"18.00", "round_off":"0.00", "place_of_supply":"27-Maharashtra", "discount":"0.00"}
            raw = {"source":"rules", "invoice": {key:[{"value":value, "confidence":.8, "source":"rules", "page":1, "evidence_text":str(value)}] for key,value in values.items()}, "line_items":[]}
            bundle = {"degraded_reasons":[], "segments":[{"page_range":[1], "qr":None, "rules":raw}], "pages":[], "engines":{}, "timings_ms":[]}
            records = run(bundle, {"filename":"sample.pdf", "sha256":"x", "detected_type":"application/pdf", "input_kind":"pdf_digital"})
            self.assertEqual(len(records), 1)
            self.assertEqual(set(records[0].invoice), set(header))
            self.assertTrue(records[0].quality.degraded)
            self.assertIn("VLM_UNAVAILABLE", records[0].quality.degraded_reasons)
            self.assertEqual(records[0].source.page_range, [1])
        finally:
            if previous is None: sys.modules.pop("vyom.models", None)
            else: sys.modules["vyom.models"] = previous

    def test_validation_is_idempotent_and_non_mutating(self):
        def field(value, confidence=.99):
            return {"value": value, "raw": str(value) if value is not None else None, "confidence": confidence, "status": "accepted" if value is not None else "missing", "sources": ["excel"], "alternatives": []}
        record = {"invoice": {
            "invoice_no": field("A-1"), "invoice_date": field("2025-05-01"), "document_type": field("tax_invoice"),
            "supplier_gstin": field("27AAPFU0939F1ZV"), "buyer_gstin": field(None), "taxable_value": field("100.00"),
            "discount": field("0.00"), "cgst": field("9.00"), "sgst": field("9.00"), "igst": field("0.00"),
            "cess": field("0.00"), "total_tax": field("18.00"), "round_off": field("0.00"), "total_amount": field("118.00"),
            "place_of_supply": field("27-Maharashtra"), "due_date": field(None), "reverse_charge": field("false"),
        }, "line_items": [], "validation": {}, "review": {}, "quality": {"overall_confidence":0}}
        before = record["invoice"].copy()
        result = validate(record, repair=False)
        self.assertEqual(record["invoice"], before)
        self.assertEqual(result["validation"]["status"], "needs_review")
        again = validate(result, repair=False)
        self.assertEqual(result["validation"], again["validation"])

    def test_amount_in_words_safe_repair(self):
        field = lambda value, confidence=.8, sources=None: {"value": value, "raw": str(value) if value is not None else None, "confidence": confidence, "status": "accepted", "sources": sources or ["rules"], "alternatives": []}
        record = {"invoice": {"invoice_no": field("A-2"), "invoice_date": field("2025-05-01"), "document_type": field("tax_invoice"),
            "supplier_gstin": field("27AAPFU0939F1ZV"), "buyer_gstin": field(None), "taxable_value": field("100.00"), "discount": field("0.00"),
            "cgst": field("9.00"), "sgst": field("9.00"), "igst": field("0.00"), "cess": field("0.00"), "total_tax": field("18.00"),
            "round_off": field("0.00"), "total_amount": field("120.00"), "amount_in_words": field("one hundred eighteen rupees only"),
            "place_of_supply": field("27-Maharashtra"), "due_date": field(None), "reverse_charge": field("false")},
            "line_items": [], "validation": {"corrections": [], "repair_candidates": []}, "review": {}, "quality": {}}
        result = validate(record, repair=True)
        self.assertEqual(result["invoice"]["total_amount"]["value"], "118.00")
        self.assertEqual(result["invoice"]["total_amount"]["status"], "corrected")
        self.assertEqual(result["invoice"]["total_amount"]["alternatives"][0]["value"], "120.00")
        self.assertEqual(result["validation"]["corrections"][0]["method"], "amount_in_words")


if __name__ == "__main__":
    unittest.main()
