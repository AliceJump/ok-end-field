from __future__ import annotations

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.image.rotated_template import ArrowAngleMatcher, _safe_roi


class TestRotatedTemplate(unittest.TestCase):
    def test_warm_match_reuses_rotations_and_does_not_convert_full_frame(self):
        matcher = ArrowAngleMatcher(benchmark_width=320)
        frame = np.random.default_rng(120).integers(0, 180, (180, 320, 3), np.uint8)
        first = matcher.match(frame, center=(0.5, 0.5))
        before = frame.copy()
        with (
            patch("src.image.rotated_template._to_rgba", side_effect=AssertionError("full-frame conversion")),
            patch.object(cv2, "warpAffine", side_effect=AssertionError("rotation cache missed")),
        ):
            second = matcher.match(frame, center=(0.5, 0.5))
        self.assertEqual(first, second)
        np.testing.assert_array_equal(frame, before)

    def test_gray_and_bgra_screenshots_keep_the_same_roi_scores(self):
        matcher = ArrowAngleMatcher(benchmark_width=320)
        gray = np.random.default_rng(121).integers(0, 255, (180, 320), np.uint8)
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        for center in ((0.5, 0.5), (0.005, 0.01)):
            expected = matcher.match(bgr, center)
            self.assertEqual(matcher.match(gray, center), expected)
            self.assertEqual(matcher.match(cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA), center), expected)

    def test_safe_roi_accepts_float_bounds(self):
        frame = np.zeros((80, 100, 3), dtype=np.uint8)

        roi = _safe_roi(frame, 10.4, 20.6, 5.2, 7.8)

        self.assertIsNotNone(roi)
        self.assertEqual(roi.shape, (8, 5, 3))

    def test_match_accepts_normalized_float_center(self):
        matcher = ArrowAngleMatcher()
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)

        angle, score = matcher.match(
            frame,
            center=(215.0 / 2560.0, 222.0 / 1440.0),
            two_stage=False,
        )

        self.assertTrue(np.isfinite(angle))
        self.assertTrue(np.isfinite(score))


if __name__ == "__main__":
    unittest.main()
