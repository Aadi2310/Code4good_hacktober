from __future__ import annotations

import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vyom.models import Token
from vyom.ocr.engine import RapidOcrEngine, merge_lines, read_with_tiles


class OcrEngineTests(unittest.TestCase):
    def test_tokens_are_normalized_and_grouped_into_lines(self) -> None:
        image = Image.new("RGB", (1200, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.text((50, 50), "GST TAX INVOICE", fill="black", font=ImageFont.load_default(size=48))
        tokens = RapidOcrEngine().read(np.asarray(image), page=1)
        self.assertTrue(tokens)
        self.assertTrue(all(0 <= coordinate <= 1 for token in tokens for coordinate in token.box))
        self.assertTrue(all(token.source == "ocr" and token.page == 1 for token in tokens))
        self.assertGreaterEqual(min(token.line_id for token in tokens), 1)

    def test_merge_lines_keeps_every_word(self) -> None:
        tokens = [
            Token(text="TAX", conf=0.9, box=[0.1, 0.1, 0.2, 0.15], page=1, line_id=0),
            Token(text="INVOICE", conf=0.9, box=[0.22, 0.1, 0.4, 0.15], page=1, line_id=0),
            Token(text="TOTAL", conf=0.9, box=[0.1, 0.3, 0.2, 0.35], page=1, line_id=0),
        ]
        merged = merge_lines(tokens)
        self.assertEqual([token.text for token in merged], ["TAX", "INVOICE", "TOTAL"])
        self.assertEqual({token.line_id for token in merged}, {1, 2})

    def test_large_page_uses_full_page_and_four_overlapping_tiles(self) -> None:
        class StubEngine:
            name = "stub"

            def __init__(self) -> None:
                self.calls = 0

            def read(self, image: np.ndarray, page: int = 1) -> list[Token]:
                self.calls += 1
                return [Token(text=f"word{self.calls}", conf=0.7, box=[0.1, 0.1, 0.2, 0.2], page=page, line_id=1)]

        engine = StubEngine()
        tokens = read_with_tiles(engine, np.zeros((3000, 3000, 3), dtype=np.uint8))
        self.assertEqual(engine.calls, 5)
        self.assertTrue(tokens)
        self.assertTrue(all(0 <= value <= 1 for token in tokens for value in token.box))


if __name__ == "__main__":
    unittest.main()
