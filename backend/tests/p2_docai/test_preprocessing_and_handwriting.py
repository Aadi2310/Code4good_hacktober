from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="P2 imaging dependencies not installed")
cv2 = pytest.importorskip("cv2", reason="P2 opencv-python-headless not installed")
pytestmark = pytest.mark.skipif(cv2 is None, reason="cv2 unavailable")
from PIL import Image, ImageDraw

from vyom.handwriting.detect import apply_occlusion_confidence, score_line
from vyom.imaging.preprocess import correct_perspective, deskew, enhance, find_blue_occlusions, load_rgb, rotate
from vyom.models import Token


class PreprocessingAndHandwritingTests(unittest.TestCase):
    def test_low_resolution_image_is_upscaled_and_flagged(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "low.png"
            Image.new("RGB", (200, 300), "white").save(path)
            image, flags = load_rgb(path)
        self.assertEqual(image.shape[:2], (600, 400))
        self.assertIn("LOW_RESOLUTION", flags)
        self.assertIn("VERY_LOW_RESOLUTION", flags)

    def test_right_angle_rotation_and_deskew_handle_synthetic_input(self) -> None:
        image = np.zeros((80, 120, 3), dtype=np.uint8)
        self.assertEqual(rotate(image, 90).shape[:2], (120, 80))
        corrected, angle = deskew(image)
        self.assertEqual(corrected.shape, image.shape)
        self.assertGreaterEqual(abs(angle), 0.0)

    def test_perspective_detector_keeps_frame_when_no_page_quad(self) -> None:
        image = np.full((80, 120, 3), 255, dtype=np.uint8)
        corrected, info = correct_perspective(image)
        self.assertEqual(corrected.shape, image.shape)
        self.assertFalse(info["perspective_corrected"])

    def test_perspective_detector_rectifies_synthetic_page(self) -> None:
        image = np.zeros((800, 600, 3), dtype=np.uint8)
        polygon = np.array([[100, 80], [500, 110], [540, 700], [60, 730]], dtype=np.int32)
        cv2.fillConvexPoly(image, polygon, (255, 255, 255))
        corrected, info = correct_perspective(image)
        self.assertTrue(info["perspective_corrected"])
        self.assertGreater(corrected.shape[0], 0)
        self.assertGreater(corrected.shape[1], 0)

    def test_deskew_corrects_a_synthetic_ten_degree_line(self) -> None:
        image = np.full((600, 900, 3), 255, dtype=np.uint8)
        cv2.line(image, (100, 340), (800, 217), (0, 0, 0), 8)
        corrected, angle = deskew(image)
        self.assertAlmostEqual(corrected.shape[0], image.shape[0])
        self.assertGreater(abs(angle), 5.0)

    def test_contrast_enhancement_preserves_dimensions(self) -> None:
        image = np.tile(np.arange(120, dtype=np.uint8)[None, :, None], (80, 1, 3))
        enhanced, quality = enhance(image)
        self.assertEqual(enhanced.shape, image.shape)
        self.assertIn("blur_score", quality)

    def test_blue_stamp_is_reported_and_lowers_overlapped_confidence(self) -> None:
        image = np.full((200, 200, 3), 255, dtype=np.uint8)
        image[50:100, 50:120] = [0, 0, 230]
        boxes, ratio = find_blue_occlusions(image)
        self.assertGreater(ratio, 0.005)
        self.assertTrue(boxes)
        token = Token(text="TOTAL", conf=0.9, box=[0.3, 0.3, 0.5, 0.4], page=1, line_id=1)
        apply_occlusion_confidence([token], boxes)
        self.assertAlmostEqual(token.conf, 0.72, places=5)

    def test_colored_carbon_stock_uses_channel_enhancement_without_stamp_flag(self) -> None:
        image = np.full((200, 200, 3), [100, 135, 210], dtype=np.uint8)
        cv2.putText(image, "INVOICE", (10, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
        enhanced, quality = enhance(image)
        self.assertTrue(quality["carbon_paper"])
        self.assertEqual(quality["occlusion_boxes"], [])
        self.assertEqual(enhanced.shape, image.shape)

    def test_handwriting_score_separates_low_confidence_irregular_lines(self) -> None:
        printed = score_line(0.97, [10, 10, 11, 10], [20, 20, 20, 21], 12, 0.0)
        handwritten = score_line(0.50, [7, 13, 9, 18], [10, 16, 28, 35], 12, 0.8)
        self.assertLess(printed, 0.40)
        self.assertGreater(handwritten, 0.55)


if __name__ == "__main__":
    unittest.main()
