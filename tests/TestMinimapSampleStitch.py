"""小地图定位采样记录/拼接辅助模块的单测。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.localization.minimap_sample_stitch import (
    ContinuousOdomTrack,
    PatchNode,
    crop_minimap_patch,
    fused_map_px,
    imread_unicode,
    imwrite_unicode,
    measure_edge,
    solve_component,
    stitch_records,
    summarize_records,
)


class TestFusedMapPixels(unittest.TestCase):
    def test_inverts_axis_matrix(self):
        result = fused_map_px(((2.0, 0.0), (0.0, -2.0)), 4.0, 6.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 2.0, delta=1e-9)
        self.assertAlmostEqual(result[1], -3.0, delta=1e-9)

    def test_rejects_missing_or_degenerate(self):
        self.assertIsNone(fused_map_px(((1.0, 0.0), (0.0, -1.0)), None, 1.0))
        self.assertIsNone(fused_map_px(((1.0, 0.0), (2.0, 0.0)), 1.0, 1.0))


class TestContinuousOdomTrack(unittest.TestCase):
    def test_track_stays_continuous_across_anchor_reset(self):
        track = ContinuousOdomTrack()
        first, first_step = track.update((0.0, 0.0))
        second, second_step = track.update((1.5, 0.0))
        after_reset, reset_step = track.update((0.0, 0.0), reset_anchor=True)
        fourth, fourth_step = track.update((0.5, 0.0))

        self.assertEqual(first, (0.0, 0.0))
        self.assertEqual(first_step, (0.0, 0.0))
        self.assertEqual(second, (1.5, 0.0))
        self.assertEqual(second_step, (1.5, 0.0))
        self.assertEqual(after_reset, (1.5, 0.0))
        self.assertEqual(reset_step, (0.0, 0.0))
        self.assertEqual(fourth, (2.0, 0.0))
        self.assertEqual(fourth_step, (0.5, 0.0))


class TestPatchCrop(unittest.TestCase):
    def test_crop_has_annulus_alpha_and_center_hole(self):
        frame = np.full((1000, 1000, 3), 120, dtype=np.uint8)
        patch, meta = crop_minimap_patch(frame, 1000, 1000)
        self.assertIsNotNone(patch)
        self.assertEqual(patch.shape[2], 4)
        self.assertEqual(meta["size"], [patch.shape[1], patch.shape[0]])
        alpha = patch[:, :, 3]
        self.assertGreater(int(alpha.max()), 0)
        cx, cy = meta["center"]
        self.assertEqual(int(alpha[round(cy), round(cx)]), 0)


class TestStitchRecords(unittest.TestCase):
    def test_writes_composite_for_overlapping_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "patches").mkdir()
            base = np.zeros((40, 40, 3), dtype=np.uint8)
            base[:, :, 0] = np.arange(40, dtype=np.uint8)[None, :] * 5
            base[:, :, 1] = np.arange(40, dtype=np.uint8)[:, None] * 5
            base[:, :, 2] = ((np.arange(40)[None, :] + np.arange(40)[:, None]) % 8 * 30).astype(np.uint8)
            shifted = cv2.warpAffine(base, np.float32([[1, 0, 8], [0, 1, 0]]), (40, 40))
            records = []
            for index, (px, image) in enumerate(((0.0, base), (8.0, shifted)), start=1):
                patch = np.dstack([image, np.zeros((40, 40), dtype=np.uint8)])
                patch[4:36, 4:36, 3] = 255
                rel = f"patches/{index:06d}.png"
                self.assertTrue(imwrite_unicode(root / rel, patch))
                records.append(
                    {
                        "i": index,
                        "map_id": "map01",
                        "segment": 0,
                        "fused_px": [px, 0.0],
                        "patch": rel,
                        "patch_size": [40, 40],
                        "patch_center": [20.0, 20.0],
                        "patch_r_inner": 4.0,
                        "patch_r_outer": 16.0,
                        "just_synced": index == 2,
                        "sync_residual": {"dist": 0.25},
                    }
                )
            outputs = stitch_records(
                records,
                patch_root=root,
                out_dir=root,
                margin=4,
                max_side=256,
                draw_overlay=True,
                min_overlap_px=100,
            )
            self.assertEqual(len(outputs), 1)
            output = Path(outputs[0]["path"])
            overlay = Path(outputs[0]["overlay_path"])
            self.assertTrue(output.is_file())
            self.assertTrue(overlay.is_file())
            image = imread_unicode(output)
            self.assertIsNotNone(image)
            self.assertGreater(int(image.sum()), 0)
            self.assertEqual(outputs[0]["nodes"], 2)
            self.assertGreaterEqual(outputs[0]["edges"], 1)
            self.assertEqual(outputs[0]["placed"], 2)
            self.assertTrue(Path(outputs[0]["pose_graph_path"]).is_file())


class TestPoseGraph(unittest.TestCase):
    def test_pcg_distributes_chain_error(self):
        positions, iterations = solve_component(
            3,
            edge_i=np.array([0, 1, 0]),
            edge_j=np.array([1, 2, 2]),
            edge_w=np.array([1.0, 1.0, 1.0]),
            edge_d=np.array([10.0, 10.0, 18.0]),
            anchor_i=np.array([0]),
            anchor_w=np.array([1.0]),
            anchor_a=np.array([0.0]),
            prior_w=0.0,
            prior_p=np.zeros(3),
            x0=np.zeros(3),
            max_iter=200,
            tol=1e-12,
        )
        self.assertGreater(iterations, 0)
        self.assertAlmostEqual(positions[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(positions[1], 28.0 / 3.0, delta=1e-4)
        self.assertAlmostEqual(positions[2], 56.0 / 3.0, delta=1e-4)


class TestEdgeMeasurement(unittest.TestCase):
    def test_phase_correlation_recovers_known_translation(self):
        rng = np.random.default_rng(7)
        base = rng.normal(128, 35, (80, 80)).astype(np.float32)
        yy, xx = np.mgrid[0:80, 0:80].astype(np.float32)
        base += 25 * np.sin(xx / 7.0) + 20 * np.cos(yy / 9.0)
        base = np.clip(base, 0, 255).astype(np.uint8)
        shifted = cv2.warpAffine(
            base,
            np.float32([[1, 0, -5], [0, 1, -3]]),
            (80, 80),
            borderMode=cv2.BORDER_REPLICATE,
        )
        alpha = np.ones((80, 80), dtype=np.float32)
        node_a = PatchNode(
            index=0,
            record={},
            path=Path(),
            bgr=cv2.cvtColor(base, cv2.COLOR_GRAY2BGR),
            gray=base,
            alpha=alpha,
            patch_center=(40.0, 40.0),
            initial_pos=np.array([0.0, 0.0]),
        )
        node_b = PatchNode(
            index=1,
            record={},
            path=Path(),
            bgr=cv2.cvtColor(shifted, cv2.COLOR_GRAY2BGR),
            gray=shifted,
            alpha=alpha,
            patch_center=(40.0, 40.0),
            initial_pos=np.array([5.0, 3.0]),
        )
        measured = measure_edge(
            node_a,
            node_b,
            base=np.array([5, 3]),
            alpha_threshold=0.5,
            min_overlap_px=400,
        )
        self.assertIsNotNone(measured)
        dx, dy, response, overlap_fraction, residual = measured
        self.assertAlmostEqual(dx, 5.0, delta=0.6)
        self.assertAlmostEqual(dy, 3.0, delta=0.6)
        self.assertGreater(response, 0.05)
        self.assertGreater(overlap_fraction, 0.0)
        self.assertLess(residual, 1.0)


class TestSummary(unittest.TestCase):
    def test_reports_step_and_residual_stats(self):
        records = [
            {
                "t": 1.0,
                "step_px": [3.0, 4.0],
                "odom_response": 0.5,
                "odom_reason": "ok",
                "map_id": "map01",
                "segment": 0,
            },
            {
                "t": 2.0,
                "step_px": [0.0, 1.0],
                "odom_response": 0.2,
                "odom_reason": "shift_too_small",
                "map_id": "map01",
                "segment": 0,
            },
        ]
        calibrations = [
            {"residual_dist_m": 0.3},
            {"residual_dist_m": 0.5},
        ]
        summary = summarize_records(records, calibrations=calibrations, scale_m_per_px=0.5)
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["calibrations"], 2)
        self.assertEqual(summary["path_px"], 6.0)
        self.assertEqual(summary["path_m"], 3.0)
        self.assertEqual(summary["step_px"]["median"], 3.0)
        self.assertEqual(summary["sync_residual_m"]["median"], 0.4)
        self.assertEqual(summary["odom_reason_counts"]["ok"], 1)
        self.assertEqual(summary["duration_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
