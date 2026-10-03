from __future__ import annotations

import unittest

import numpy as np

from src.image.rotated_template import ArrowAngleMatcher, _safe_roi


class TestRotatedTemplate(unittest.TestCase):
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
