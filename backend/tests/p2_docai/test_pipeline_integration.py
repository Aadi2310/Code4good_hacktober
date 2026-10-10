from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from vyom.docai import build_bundle


def _minimal_pdf(lines: list[str]) -> bytes:
    """Create a tiny standards-compliant text PDF without an extra test dependency."""
    commands = ["BT", "/F1 12 Tf", "50 740 Td"]
    for index, line in enumerate(lines):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            commands.append("0 -18 Td")
        commands.append(f"({safe}) Tj")
    commands.append("ET")
    for y in (500, 480, 460, 440, 420):
        commands.extend((f"50 {y} m 550 {y} l S",))
    for x in (50, 150, 300, 350, 400, 475, 550):
        commands.append(f"{x} 420 m {x} 500 l S")
    commands.extend(("BT", "/F1 9 Tf"))
    table = [
        (485, ("Sr No", "Description", "HSN", "Qty", "Rate", "Amount")),
        (465, ("1", "Widget", "1234", "2", "500.00", "1000.00")),
    ]
    xs = (55, 155, 305, 355, 405, 480)
    for y, cells in table:
        for x, cell in zip(xs, cells):
            commands.extend((f"1 0 0 1 {x} {y} Tm", f"({cell}) Tj"))
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii"))
    return bytes(document)


class PipelineIntegrationTests(unittest.TestCase):
    def test_digital_pdf_builds_evidence_bundle(self) -> None:
        lines = [
            "TAX INVOICE", "Invoice No: INV-42", "Invoice Date: 04/05/2026",
            "Supplier GSTIN: 27ABCDE1234F1Z5", "Buyer GSTIN: 29FGHIJ5678K1Z2",
            "HSN 1234", "Total Amount: 1180.00", "Amount 1180.00",
        ]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            pdf = root / "invoice.pdf"
            pdf.write_bytes(_minimal_pdf(lines))
            bundle = build_bundle(pdf, "pdf", root / "artifacts", {})
            self.assertTrue(Path(bundle.pages[0].original_path).is_file())
            self.assertTrue(Path(bundle.pages[0].enhanced_path).is_file())
            self.assertTrue((root / "artifacts" / "ocr.json").is_file())
        self.assertEqual(bundle.input_kind, "pdf_digital")
        self.assertEqual(len(bundle.pages), 1)
        self.assertEqual(bundle.pages[0].kind, "digital")
        self.assertIn("invoice_no", bundle.segments[0].rules.invoice)
        self.assertEqual(bundle.segments[0].rules.invoice["invoice_no"][0].value, "INV-42")
        self.assertEqual(len(bundle.segments[0].rules.line_items), 1)

    def test_printed_image_runs_ocr_and_returns_a_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            image = Image.new("RGB", (1800, 1200), "white")
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=48)
            for index, text in enumerate((
                "TAX INVOICE", "Invoice No: INV-42", "Supplier GSTIN: 27ABCDE1234F1Z5",
                "Buyer GSTIN: 29FGHIJ5678K1Z2", "HSN Code: 1234", "Total Amount: 1180.00",
            )):
                draw.text((80, 70 + index * 150), text, fill="black", font=font)
            source = root / "printed.png"
            image.save(source)
            bundle = build_bundle(source, "png", root / "artifacts", {})
            self.assertTrue(Path(bundle.pages[0].enhanced_path).is_file())
        self.assertEqual(len(bundle.pages), 1)
        self.assertTrue(bundle.pages[0].tokens)
        self.assertGreaterEqual(len(bundle.segments), 1)
        self.assertEqual(bundle.input_kind, "image_printed")
        self.assertEqual(bundle.segments[0].rules.invoice["invoice_no"][0].value, "INV-42")


if __name__ == "__main__":
    unittest.main()
