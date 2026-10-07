"""关键帧视觉锚定链校正的单测：验证它能把累计漂移拉回真实轨迹。"""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from src.localization.minimap_visual_anchor_chain import (
    MinimapKeyframeVisualAnchorChain,
    MinimapVisualAnchorChainCorrector,
)


def _texture(size: int = 140, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 40, (size, size)).astype(np.float32)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    base += 30 * np.sin(xx / 9.0) + 25 * np.cos(yy / 11.0)
    return np.clip(base, 0, 255).astype(np.uint8)


class TestMinimapVisualAnchorChainCorrection(unittest.TestCase):
    def test_keyframe_chain_propagates_anchor_across_nonoverlapping_gap(self):
        texture = _texture(600, seed=11)
        size = 226
        offsets = [(0, 0), (120, 40), (240, 80)]
        chain = MinimapKeyframeVisualAnchorChain(
            max_keyframes=10,
            max_edges=3,
            min_overlap_px=400,
            prior_weight=0.01,
            anchor_weight=50.0,
            scale_m_per_px=1.0,
            max_correction_m=100.0,
        )
        raw_positions = []
        corrected_positions = []
        for index, (x, y) in enumerate(offsets):
            patch = texture[y : y + size, x : x + size]
            raw = np.asarray([x + 5.0 * index, y + 2.0 * index], dtype=np.float64)
            anchor = np.asarray([0.0, 0.0], dtype=np.float64) if index == 0 else None
            result = chain.add(
                uid=index,
                time=float(index),
                gray=patch,
                alpha=np.ones((size, size), dtype=np.float32),
                initial_pos=raw,
                anchor=anchor,
            )
            raw_positions.append(raw)
            corrected_positions.append(result.position.copy())
        true_c = np.asarray([240.0, 80.0], dtype=np.float64)
        raw_error = float(np.linalg.norm(raw_positions[-1] - true_c))
        corrected_error = float(np.linalg.norm(corrected_positions[-1] - true_c))
        self.assertLess(corrected_error, raw_error * 0.5, (raw_error, corrected_error))

    def test_reduces_injected_drift(self):
        size = 140
        base = _texture(size)
        alpha = np.ones((size, size), dtype=np.float32)
        corrector = MinimapVisualAnchorChainCorrector(
            window_size=30,
            edge_gap=8,
            min_overlap_px=1000,
            prior_weight=0.01,
            window_anchor_weight=5.0,
            max_correction_m=100.0,
            scale_m_per_px=1.0,
        )
        true_positions = []
        raw_positions = []
        corrected_positions = []
        for index in range(24):
            true_x, true_y = 2.0 * index, 1.0 * index
            shifted = cv2.warpAffine(
                base,
                np.float32([[1, 0, -true_x], [0, 1, -true_y]]),
                (size, size),
                borderMode=cv2.BORDER_REPLICATE,
            )
            raw = np.asarray([true_x + 0.35 * index, true_y], dtype=np.float64)
            result = corrector.add(
                time=float(index),
                gray=shifted,
                alpha=alpha,
                initial_pos=raw,
            )
            true_positions.append(np.asarray([true_x, true_y], dtype=np.float64))
            raw_positions.append(raw)
            corrected_positions.append(result.position.copy())

        raw_error = float(np.linalg.norm(raw_positions[-1] - true_positions[-1]))
        corrected_error = float(np.linalg.norm(corrected_positions[-1] - true_positions[-1]))
        self.assertLess(corrected_error, raw_error * 0.6, (raw_error, corrected_error))

    def test_window_slides_without_resetting_drift(self):
        size = 140
        base = _texture(size)
        alpha = np.ones((size, size), dtype=np.float32)
        corrector = MinimapVisualAnchorChainCorrector(
            window_size=20,
            edge_gap=6,
            min_overlap_px=800,
            prior_weight=0.01,
            window_anchor_weight=5.0,
            max_correction_m=100.0,
            scale_m_per_px=1.0,
        )
        true_positions = []
        raw_positions = []
        corrected_positions = []
        last_nodes = 0
        for index in range(60):
            true_x, true_y = 1.5 * index, 0.8 * index
            shifted = cv2.warpAffine(
                base,
                np.float32([[1, 0, -true_x], [0, 1, -true_y]]),
                (size, size),
                borderMode=cv2.BORDER_REPLICATE,
            )
            raw = np.asarray([true_x + 0.25 * index, true_y + 0.05 * index], dtype=np.float64)
            result = corrector.add(
                time=float(index),
                gray=shifted,
                alpha=alpha,
                initial_pos=raw,
            )
            true_positions.append(np.asarray([true_x, true_y], dtype=np.float64))
            raw_positions.append(raw)
            corrected_positions.append(result.position.copy())
            last_nodes = result.nodes
        self.assertLessEqual(last_nodes, 20)
        raw_error = float(np.linalg.norm(raw_positions[-1] - true_positions[-1]))
        corrected_error = float(np.linalg.norm(corrected_positions[-1] - true_positions[-1]))
        self.assertLess(corrected_error, raw_error * 0.6, (raw_error, corrected_error))

    def test_ws_anchor_pulls_position_toward_absolute(self):
        size = 140
        base = _texture(size)
        alpha = np.ones((size, size), dtype=np.float32)
        corrector = MinimapVisualAnchorChainCorrector(
            window_size=30,
            edge_gap=8,
            min_overlap_px=1000,
            prior_weight=0.01,
            anchor_weight=50.0,
            window_anchor_weight=0.1,
            max_correction_m=100.0,
            scale_m_per_px=1.0,
        )
        true_positions = []
        raw_positions = []
        corrected_positions = []
        for index in range(24):
            true_x, true_y = 2.0 * index, 1.0 * index
            shifted = cv2.warpAffine(
                base,
                np.float32([[1, 0, -true_x], [0, 1, -true_y]]),
                (size, size),
                borderMode=cv2.BORDER_REPLICATE,
            )
            raw = np.asarray([true_x + 5.0, true_y + 5.0], dtype=np.float64)
            anchor = np.asarray([true_x, true_y], dtype=np.float64) if index == 12 else None
            result = corrector.add(
                time=float(index),
                gray=shifted,
                alpha=alpha,
                initial_pos=raw,
                anchor=anchor,
            )
            true_positions.append(np.asarray([true_x, true_y], dtype=np.float64))
            raw_positions.append(raw)
            corrected_positions.append(result.position.copy())
        raw_error = float(np.linalg.norm(raw_positions[-1] - true_positions[-1]))
        corrected_error = float(np.linalg.norm(corrected_positions[-1] - true_positions[-1]))
        self.assertLess(corrected_error, raw_error)

    def test_persistent_ws_anchor_survives_window_slide(self):
        size = 140
        base = _texture(size)
        alpha = np.ones((size, size), dtype=np.float32)
        corrector = MinimapVisualAnchorChainCorrector(
            window_size=8,
            edge_gap=4,
            min_overlap_px=800,
            prior_weight=0.01,
            anchor_weight=50.0,
            window_anchor_weight=0.1,
            max_correction_m=100.0,
            scale_m_per_px=1.0,
        )
        true_positions = []
        raw_positions = []
        corrected_positions = []
        for index in range(30):
            true_x, true_y = 2.0 * index, 1.0 * index
            shifted = cv2.warpAffine(
                base,
                np.float32([[1, 0, -true_x], [0, 1, -true_y]]),
                (size, size),
                borderMode=cv2.BORDER_REPLICATE,
            )
            raw = np.asarray([true_x + 0.3 * index, true_y + 0.1 * index], dtype=np.float64)
            anchor = np.asarray([true_x, true_y], dtype=np.float64) if index == 0 else None
            result = corrector.add(
                time=float(index),
                gray=shifted,
                alpha=alpha,
                initial_pos=raw,
                anchor=anchor,
            )
            true_positions.append(np.asarray([true_x, true_y], dtype=np.float64))
            raw_positions.append(raw)
            corrected_positions.append(result.position.copy())
        raw_error = float(np.linalg.norm(raw_positions[-1] - true_positions[-1]))
        corrected_error = float(np.linalg.norm(corrected_positions[-1] - true_positions[-1]))
        self.assertLess(corrected_error, raw_error * 0.5, (raw_error, corrected_error))


if __name__ == "__main__":
    unittest.main()
