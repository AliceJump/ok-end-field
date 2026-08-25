import unittest

import cv2
import numpy as np

from src.image.gray_bar_detector import detect_gray_bars


class TestGrayBarDetector(unittest.TestCase):

    def _frame(self, scale=1.0, brightness=0, widths=(70, 70, 70, 70), bar_height=12):
        width, height = round(2560 * scale), round(1440 * scale)
        background = np.tile(np.linspace(28, 58, width, dtype=np.float32), (height, 1))
        y, x = round(1104 * scale), round(162 * scale)
        for index, bar_width in enumerate(widths):
            bar_width = round(bar_width * scale)
            half_height = round(bar_height * scale / 2)
            background[y - half_height:y + half_height, x:x + bar_width] += 64 + index * 3
            x += bar_width + round(8 * scale)
        return cv2.cvtColor(np.clip(background + brightness, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    def test_scaling_and_brightness(self):
        for scale in (0.75, 1.25, 1.5):
            for brightness in (-20, 20):
                with self.subTest(scale=scale, brightness=brightness):
                    bars = detect_gray_bars(self._frame(scale, brightness))
                    self.assertEqual(len(bars), 4)
                    self.assertTrue(all(abs(bar.center_y / round(1440 * scale) - 1104 / 1440) < 0.03 for bar in bars))

    def test_width_bounds(self):
        for width, expected in ((11, 0), (12, 1), (104, 1), (105, 0)):
            with self.subTest(width=width):
                frame = self._frame(widths=(width,), bar_height=4)
                bars = detect_gray_bars(
                    frame,
                    min_width_ratio=12 / 2560,
                    max_width_ratio=78 / 1920,
                    min_aspect_ratio=3.0,
                )
                self.assertEqual(len(bars), expected)

    def test_many_narrow_bars_can_be_found_with_full_image_search(self):
        """组内条数增加时，单条宽度可以低于四条组的默认样本宽度。"""
        frame = self._frame(widths=(24,) * 12, bar_height=4)
        bars = detect_gray_bars(
            frame,
            x_min_ratio=0,
            x_max_ratio=1,
            y_min_ratio=0,
            y_max_ratio=1,
            min_width_ratio=24 / 2560,
            max_width_ratio=24 / 2560,
            min_aspect_ratio=3.0,
        )
        self.assertEqual(len(bars), 12)
        self.assertTrue(all(bar.width == 24 for bar in bars))

    def test_excludes_bright_white_bars(self):
        frame = self._frame(widths=(70, 70))
        frame[1098:1110, 600:670] = 250
        bars = detect_gray_bars(frame, x_min_ratio=0, x_max_ratio=1)
        self.assertEqual(len(bars), 2)
        self.assertTrue(all(bar.x < 300 for bar in bars))

    