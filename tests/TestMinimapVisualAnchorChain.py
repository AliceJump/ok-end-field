"""关键帧视觉锚定链校正的单测：验证它能把累计漂移拉回真实轨迹。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.localization.minimap_sample_stitch import PoseGraphEdge
from src.localization.minimap_visual_anchor_chain import (
    MinimapKeyframeVisualAnchorChain,
    MinimapVisualAnchorChainCorrector,
    MinimapVisualChainEvidence,
)


def _texture(size: int = 140, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 40, (size, size)).astype(np.float32)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    base += 30 * np.sin(xx / 9.0) + 25 * np.cos(yy / 11.0)
    return np.clip(base, 0, 255).astype(np.uint8)


class TestMinimapVisualAnchorChainCorrection(unittest.TestCase):
    def test_window_slide_cannot_anchor_a_disconnected_component(self):
        for connected, late_bridge in ((False, False), (False, True), (True, False)):
            with self.subTest(connected=connected, late_bridge=late_bridge):
                chain = MinimapVisualAnchorChainCorrector(window_size=3, max_correction_m=100)
                image = _texture()
                alpha = np.ones_like(image, np.float32)

                def edges(node, connected=connected, late_bridge=late_bridge):
                    if not node.index or (node.index == 1 and not connected):
                        return []
                    edges = [PoseGraphEdge(node.index - 1, node.index, 1.0, 0.0, 0.0, 1.0, 1.0, 1)]
                    if late_bridge and node.uid == 3:
                        edges.append(PoseGraphEdge(0, node.index, 1.0, 0.0, 0.0, 1.0, 1.0, 3))
                    return edges

                with patch.object(chain, "_edges_to_previous", side_effect=edges):
                    for index in range(7):
                        result = chain.add(
                            time=index,
                            gray=image,
                            alpha=alpha,
                            initial_pos=[0, 0],
                            anchor=[0, 0] if index == 0 else None,
                        )
                        if index >= 2:
                            self.assertTrue(result.valid)
                            self.assertEqual(result.connected_to_anchor, connected)

    def test_long_visual_loop_uses_direct_keyframes_without_refreshing_anchor_age(self):
        atlas = _texture(500, seed=35)
        evidence = MinimapVisualChainEvidence(
            scale_m_per_px=0.25, max_error_m=1.0, keyframe_interval=5, max_keyframes=4
        )
        errors = []
        checks = 0
        # Actual image measurements over 600 synthetic moving frames with
        # periodic returns to old views, approximately six minutes.
        # Positions deliberately have a small growing prior bias.
        for i in range(600):
            x = 100 + (i % 80 if i % 160 < 80 else 80 - i % 80) * 0.5
            image = cv2.getRectSubPix(atlas, (140, 140), (x + 70, 170))
            truth = np.array([x - 100, 0.0])
            result = evidence.observe(
                time=i * 0.6,
                gray=image,
                alpha=np.ones((140, 140), np.float32),
                position=truth + np.array([0.001 * i, 0]),
                anchor=truth if i == 0 else None,
            )
            self.assertTrue(result.trusted, (i, result))
            errors.append(float(np.linalg.norm(result.position - truth)) * 0.25)
            checks += result.keyframe_checks
            self.assertEqual(result.anchor_time, 0)
            self.assertLessEqual(len(evidence.keyframes), 4)
        self.assertGreater(checks, 1000)
        self.assertLess(max(errors), 0.5)
        self.sequence_metrics = {
            "frames": 600,
            "duration_s": 599 * 0.6,
            "max_error_m": max(errors),
            "direct_keyframe_checks": checks,
            "final_error_allowance_m": result.error_m,
        }

    def test_error_cost_survives_keyframe_eviction_and_stops_long_unchecked_travel(self):
        atlas = _texture(1600, seed=61)
        evidence = MinimapVisualChainEvidence(scale_m_per_px=1, max_error_m=3, keyframe_interval=2, max_keyframes=3)
        result = None
        for i in range(300):
            image = atlas[100:240, i * 4 : i * 4 + 140]
            result = evidence.observe(
                time=i * 0.2,
                gray=image,
                alpha=np.ones((140, 140), np.float32),
                position=[i * 4, 0],
                anchor=[0, 0] if i == 0 else None,
            )
            if not result.trusted:
                break
        self.assertGreater(i, 1)
        self.assertLess(i, 299)
        self.assertEqual(result.reason, "error_budget_exhausted")
        self.assertGreater(result.error_m, 3)
        self.assertEqual(result.anchor_time, 0)
        self.assertGreaterEqual(evidence.tick, 4)

    def test_gap_or_unrelated_image_cannot_be_rescued_by_an_old_keyframe(self):
        image = _texture(140, seed=30)
        for gap, frame in ((3, image), (0.2, _texture(140, seed=70))):
            with self.subTest(gap=gap):
                evidence = MinimapVisualChainEvidence(scale_m_per_px=0.25)
                evidence.observe(
                    time=0, gray=image, alpha=np.ones_like(image, np.float32), position=[0, 0], anchor=[0, 0]
                )
                result = evidence.observe(time=gap, gray=frame, alpha=np.ones_like(image, np.float32), position=[0, 0])
                self.assertFalse(result.trusted)

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
