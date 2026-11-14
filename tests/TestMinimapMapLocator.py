import argparse
import json
import math
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError, Future
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from scripts._minimap_baseline import read_baseline_source
from src.core.NavConfig import DEFAULT_SCALE_CONSTANT
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MapCandidate,
    MinimapGeometry,
    MinimapMapLocator,
    MinimapTracker,
    _has_minimap_texture,
    _PreparedTemplate,
    _subpixel_offset,
    deduplicate_candidates,
    masked_zncc,
    minimap_map_scale,
    robust_masked_zncc,
)
from src.localization.minimap_odometry import region_geometry


class TestMinimapMapLocatorMath(unittest.TestCase):
    def test_background_coarse_wait_adopts_once_and_releases_failed_or_cancelled_futures(self):
        locator = MinimapMapLocator(np.zeros((60, 70, 3), np.uint8), MapCalibration(np.eye(2, 3), np.eye(2, 3)))
        executor = Mock()
        bad = Future()
        bad.set_exception(ValueError("broken"))
        executor.submit.return_value = bad
        with self.assertRaisesRegex(ValueError, "broken"):
            locator.prepare_global_search(1920, executor, wait=True)
        self.assertFalse(locator._pending_coarse)
        self.assertFalse(locator._coarse_cache)
        pending, stop = Future(), threading.Event()
        executor.submit.return_value = pending
        stop.set()
        with self.assertRaises(CancelledError):
            locator.prepare_global_search(1920, executor, wait=True, stop=stop)
        self.assertTrue(pending.cancelled())
        self.assertFalse(locator._pending_coarse)
        ready = Future()
        pixels = np.zeros((30, 35, 3), np.uint8)
        ready.set_result(pixels)
        executor.submit.return_value = ready
        self.assertTrue(locator.prepare_global_search(1920, executor, wait=True))
        self.assertIs(next(iter(locator._coarse_cache.values())), pixels)
        self.assertFalse(locator._pending_coarse)

    def test_batched_color_scores_match_scalar_including_dim_background_and_flat_patches(self):
        rng = np.random.default_rng(1801)
        pixels = rng.integers(5, 38, (61, 61, 3), np.uint8)
        pixels[22:39, 22:39] = rng.integers(105, 180, (17, 17, 3), np.uint8)
        mask = rng.random(pixels.shape[:2]) > 0.12
        template = _PreparedTemplate(pixels.copy(), pixels, mask, mask.astype(np.uint8) * 255)
        self.assertIsNotNone(template.dim_terrain)
        patches = [pixels.copy(), np.zeros_like(pixels)] + [
            rng.integers(0, 180, pixels.shape, np.uint8) for _ in range(19)
        ]
        positions = [(i / 3, i / 7, 0.7) for i in range(len(patches))]
        locator = MinimapMapLocator(pixels, MapCalibration(np.eye(2, 3), np.eye(2, 3)))
        expected = [
            locator._candidate_from_patch(template, p, x, y, coarse_score=s)
            for p, (x, y, s) in zip(patches, positions, strict=True)
        ]
        actual = locator._candidates_from_patches(template, patches, positions)
        for a, b in zip(actual, expected, strict=True):
            self.assertAlmostEqual(a.ncc, b.ncc, places=12)
            self.assertAlmostEqual(a.full_ncc, b.full_ncc, places=12)
            self.assertEqual((a.world_x, a.world_z, a.coarse_score), (b.world_x, b.world_z, b.coarse_score))

    def test_circle_coverage_keeps_subpixel_boundary_but_rejects_distant_corner(self):
        locator = MinimapMapLocator(np.zeros((70, 70, 3), np.uint8), MapCalibration(np.eye(2, 3), np.eye(2, 3)))
        allowed = np.zeros((30, 30), bool)
        allowed[0, 10] = True
        self.assertTrue(locator._coverage_reaches_radius(allowed, (0, 0), 1, 1, (0, 0), (0, 0), 9.6))
        allowed[:] = False
        allowed[10, 10] = True
        self.assertFalse(locator._coverage_reaches_radius(allowed, (0, 0), 1, 1, (0, 0), (0, 0), 10))

    def test_coarse_preparation_does_not_wait_or_publish_before_completion(self):
        pixels = np.random.default_rng(714).integers(0, 200, (60, 70, 3), np.uint8)
        locator = MinimapMapLocator(pixels, MapCalibration(np.eye(2, 3), np.eye(2, 3)), scale_override=1)
        future = Future()
        executor = Mock()
        executor.submit.return_value = future
        self.assertFalse(locator.prepare_global_search(1920, executor))
        self.assertFalse(locator.prepare_global_search(1920, executor))
        executor.submit.assert_called_once()
        self.assertFalse(locator._coarse_cache)
        call = executor.submit.call_args
        expected = call.args[0](*call.args[1:])
        future.set_result(expected)
        self.assertTrue(locator.prepare_global_search(1920, executor))
        self.assertIs(locator._coarse_map(locator.coarse_factor), expected)
        self.assertTrue(locator.prepare_global_search(1920, executor))
        executor.submit.assert_called_once()
        self.assertFalse(locator._pending_coarse)

    def test_coarse_preparation_failure_and_close_do_not_retain_results(self):
        locator = MinimapMapLocator(np.zeros((60, 70, 3), np.uint8), MapCalibration(np.eye(2, 3), np.eye(2, 3)))
        future = Future()
        executor = Mock()
        executor.submit.return_value = future
        self.assertFalse(locator.prepare_global_search(1920, executor))
        future.set_exception(ValueError("invalid resource"))
        with self.assertRaisesRegex(ValueError, "invalid resource"):
            locator.prepare_global_search(1920, executor)
        self.assertFalse(locator._coarse_cache)
        self.assertFalse(locator._pending_coarse)
        pending = Future()
        executor.submit.return_value = pending
        self.assertFalse(locator.prepare_global_search(1920, executor))
        locator.close()
        self.assertTrue(pending.cancelled())
        self.assertFalse(locator._pending_coarse)
        with self.assertRaisesRegex(ValueError, "locator closed"):
            locator.prepare_global_search(1920, executor)

    def test_coarse_preparation_discards_superseded_frame_widths(self):
        locator = MinimapMapLocator(np.zeros((60, 70, 3), np.uint8), MapCalibration(np.eye(2, 3), np.eye(2, 3)))
        old, current = Future(), Future()
        executor = Mock()
        executor.submit.side_effect = (old, current)
        self.assertFalse(locator.prepare_global_search(1920, executor))
        self.assertFalse(locator.prepare_global_search(2560, executor))
        self.assertTrue(old.cancelled())
        self.assertEqual(list(locator._pending_coarse.values()), [current])
        locator.close()
        self.assertTrue(current.cancelled())

    def test_color_zncc_matches_opencv_without_residual_trimming(self):
        rng = np.random.default_rng(204)
        left = rng.integers(0, 256, (24, 24, 3), dtype=np.uint8)
        right = rng.integers(0, 256, left.shape, dtype=np.uint8)
        mask = rng.random(left.shape[:2]) > 0.2
        expected = cv2.matchTemplate(left, right, cv2.TM_CCOEFF_NORMED, mask=mask.astype(np.uint8))[0, 0]
        actual = robust_masked_zncc(left, right, mask)
        self.assertAlmostEqual(actual.score, float(expected), places=5)
        self.assertEqual(actual.score, actual.full_score)
        self.assertEqual(actual.inlier_ratio, 1.0)

    def test_color_mask_removes_exact_white_with_two_pixel_padding(self):
        roi = np.zeros((31, 31, 3), dtype=np.uint8)
        roi[10, 10] = 255
        roi[20, 20] = 254
        mask = np.ones((31, 31), dtype=bool)
        visible, _ = MinimapMapLocator._white_visible_masks(roi, mask, mask.astype(np.uint8) * 255, 15)
        expected = mask.copy()
        expected[8:13, 8:13] = False
        np.testing.assert_array_equal(visible, expected)

    def test_color_tracker_uses_color_score_thresholds(self):
        calibration = MapCalibration(np.eye(2, 3), np.eye(2, 3))
        locator = MinimapMapLocator(np.zeros((10, 10, 3), np.uint8), calibration)
        tracker = MinimapTracker(locator)
        self.assertEqual(tracker.initial_min_ncc, 0.45)
        self.assertEqual(tracker.initial_min_full_ncc, 0.45)
        self.assertEqual(tracker.initial_min_margin, 0.10)

    def test_color_tracker_still_requires_score_margin_and_two_frames(self):
        for score, margin, expected in ((0.44, 0.20, False), (0.50, 0.09, False), (0.50, 0.12, True)):
            with self.subTest(score=score, margin=margin):
                candidate = MapCandidate(40, 40, 10, 10, score, full_ncc=score, tier_id="__main__", color=True)
                result = LocateResult("accepted", "test", candidate, (candidate,), 0.01, 1, spatial_margin=margin)
                locator = Mock(color=True)
                locator.locate_global.return_value = result
                locator.locate_near.return_value = result
                tracker = MinimapTracker(locator)
                self.assertFalse(tracker.update(None, None, now=1).accepted)
                self.assertEqual(tracker.update(None, None, now=1.2).accepted, expected)

    def test_map_world_matrix_and_inverse(self):
        matrix = np.array([[4.0, 0.25, -100.0], [0.5, -4.0, 200.0]])
        homogeneous = np.vstack((matrix, np.array([0.0, 0.0, 1.0])))
        calibration = MapCalibration(matrix=matrix, inverse_matrix=np.linalg.inv(homogeneous)[:2])

        world = calibration.map_to_world(120.0, 340.0)
        recovered = calibration.world_to_map(*world)

        self.assertAlmostEqual(world[0], 465.0)
        self.assertAlmostEqual(world[1], -1100.0)
        self.assertAlmostEqual(recovered[0], 120.0)
        self.assertAlmostEqual(recovered[1], 340.0)

    def test_minimap_scale_matches_width_contract(self):
        self.assertAlmostEqual(minimap_map_scale(1924), 1924 / (4 * DEFAULT_SCALE_CONSTANT))
        self.assertAlmostEqual(minimap_map_scale(2564), 2564 / (4 * DEFAULT_SCALE_CONSTANT))
        with self.assertRaises(ValueError):
            minimap_map_scale(0)

    def test_geometry_uses_production_region_helper(self):
        geometry = MinimapGeometry.from_frame(1924, 1127)
        center_x, center_y, _inner, outer = region_geometry(1924, 1127)

        self.assertEqual((geometry.center_x, geometry.center_y), (center_x, center_y))
        self.assertEqual(geometry.outer_radius, outer)
        self.assertAlmostEqual(geometry.match_inner_radius, outer * 0.10)
        self.assertAlmostEqual(geometry.match_outer_radius, outer * 0.85)

    def test_geometry_rejects_invalid_ring_and_frame_placement(self):
        with self.assertRaises(ValueError):
            MinimapGeometry(center_x=10, center_y=10, outer_radius=8, ring_inner_ratio=0.9, ring_outer_ratio=0.8)

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        locator = MinimapMapLocator(
            np.zeros((200, 200), dtype=np.uint8),
            MapCalibration(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), np.eye(2, 3)),
            scale_override=0.5,
        )
        self.assertIsNone(locator.locate_global(image, MinimapGeometry(5, 5, 20)))

    def test_masked_zncc_is_one_zero_and_minus_one(self):
        left = np.arange(25, dtype=np.float32).reshape(5, 5)
        mask = np.ones((5, 5), dtype=bool)

        self.assertAlmostEqual(masked_zncc(left, left, mask), 1.0)
        self.assertAlmostEqual(masked_zncc(left, -left, mask), -1.0)
        self.assertEqual(masked_zncc(left, left, np.zeros((5, 5), dtype=bool)), 0.0)
        self.assertEqual(masked_zncc(np.ones((5, 5)), np.ones((5, 5)), mask), 0.0)

    def test_robust_ncc_downweights_fixed_center_outlier(self):
        rng = np.random.default_rng(7)
        left = rng.integers(0, 255, size=(64, 64), dtype=np.uint8)
        right = left.copy()
        right[22:42, 22:42] = 255
        mask = np.ones_like(left, dtype=bool)

        full = masked_zncc(left, right, mask)
        robust = robust_masked_zncc(left, right, mask, trim_ratio=0.25)

        self.assertLess(full, 0.95)
        self.assertGreater(robust.score, full)
        self.assertLessEqual(robust.score, full + 0.20 + 1e-12)
        self.assertAlmostEqual(robust.full_score, full)
        self.assertAlmostEqual(robust.inlier_ratio, 0.75)

    def test_ties_at_trim_boundary_preserve_original_score(self):
        indices = np.arange(400).reshape(20, 20)
        left = indices % 11
        right = left + indices % 3
        score = robust_masked_zncc(left, right, np.ones_like(left, dtype=bool), trim_ratio=0.25)
        self.assertAlmostEqual(score.score, 0.9826366742125515, places=12)
        self.assertAlmostEqual(score.full_score, 0.968370310205452, places=12)
        self.assertEqual(score.inlier_ratio, 0.75)

    def test_subpixel_offset_recovers_fractional_peak(self):
        expected = 0.3
        negative = math.exp(-((expected + 1.0) ** 2))
        center = math.exp(-(expected**2))
        positive = math.exp(-((expected - 1.0) ** 2))

        self.assertAlmostEqual(_subpixel_offset(negative, center, positive), expected, places=6)

    def test_candidate_dedup_removes_adjacent_peaks_before_margin(self):
        candidates = [
            MapCandidate(1.0, 1.0, 100.0, 100.0, ncc=0.75),
            MapCandidate(1.0, 1.0, 101.0, 100.0, ncc=0.65),
            MapCandidate(1.0, 1.0, 120.0, 100.0, ncc=0.50),
        ]

        kept = deduplicate_candidates(candidates, min_world_distance_m=4.0)

        self.assertEqual([candidate.ncc for candidate in kept], [0.75, 0.50])
        locator = MinimapMapLocator(
            np.zeros((20, 20), dtype=np.uint8),
            MapCalibration(np.eye(2, 3), np.eye(2, 3)),
            scale_override=1.0,
        )
        self.assertEqual(locator._status(kept), ("accepted", "high NCC with clear margin"))


class TestMinimapMapLocatorFiles(unittest.TestCase):
    def _write_fixture(
        self,
        root: Path,
        *,
        width: int = 20,
        height: int = 10,
        mapping_width: int | None = None,
        mapping_height: int | None = None,
        include_size: bool = True,
    ) -> tuple[Path, Path]:
        map_path = root / "map.png"
        cv2.imwrite(str(map_path), np.zeros((height, width), dtype=np.uint8))
        data = {
            "map_name": "map.png",
            "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "inverse_matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        }
        if include_size:
            data["image_size"] = {
                "width": width if mapping_width is None else mapping_width,
                "height": height if mapping_height is None else mapping_height,
            }
        mapping_path = root / "map_mapping.json"
        mapping_path.write_text(json.dumps(data), encoding="utf-8")
        return map_path, mapping_path

    def test_from_files_accepts_matching_image_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_path, mapping_path = self._write_fixture(Path(tmp))
            locator = MinimapMapLocator.from_files(
                map_path,
                mapping_path,
                scale_override=1.0,
            )

        self.assertEqual(locator.map_image.shape, (10, 20, 3))
        self.assertEqual(locator.calibration.image_width, 20)
        self.assertEqual(locator.calibration.image_height, 10)

    def test_from_files_rejects_mismatched_image_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_path, mapping_path = self._write_fixture(Path(tmp), mapping_width=21)

            with self.assertRaisesRegex(ValueError, "does not match mapping image_size"):
                MinimapMapLocator.from_files(map_path, mapping_path)

    def test_from_files_rejects_missing_image_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            map_path, mapping_path = self._write_fixture(Path(tmp), include_size=False)

            with self.assertRaisesRegex(ValueError, "does not declare image_size"):
                MinimapMapLocator.from_files(map_path, mapping_path)


class TestMinimapMapLocatorSynthetic(unittest.TestCase):
    def test_single_sided_dim_proposal_finds_background_without_changing_formal_scores(self):
        rng = np.random.default_rng(2031)
        atlas = rng.integers(20, 180, (260, 360), np.uint8)
        atlas = cv2.cvtColor(cv2.GaussianBlur(atlas, (3, 3), 0), cv2.COLOR_GRAY2BGR)
        pixels = np.rint(atlas[80:161, 100:181] * 0.3).astype(np.uint8)
        pixels[:, 40:] = rng.integers(120, 200, pixels[:, 40:].shape, np.uint8)
        mask = np.ones(pixels.shape[:2], bool)
        template = _PreparedTemplate(pixels, pixels, mask, mask.astype(np.uint8) * 255)
        self.assertIsNone(template.dim_terrain)
        self.assertIsNotNone(template.proposal_dim_mask)
        self.assertFalse(template.proposal_dim_mask.flags.writeable)
        locator = MinimapMapLocator(atlas, self.calibration, scale_override=1)
        locator._prepare_template = lambda *_: template
        frame = np.zeros((160, 160, 3), np.uint8)
        before = locator.locate_global(frame, None)
        expected = self.calibration.map_to_world(140, 120)
        score = locator.proposal_background_score(template, frame.shape[1], expected, radius=2)
        wrong = locator.proposal_background_score(template, frame.shape[1], (70, -30), radius=2)
        self.assertGreater(score, 0.99)
        self.assertLess(wrong, 0.5)
        after = locator.locate_global(frame, None)
        self.assertEqual(before.candidates, after.candidates)
        self.assertEqual((before.status, before.margin), (after.status, after.margin))
        self.assertIsNone(template.dim_terrain)

    def test_flat_or_single_mode_pixels_do_not_create_single_sided_proposals(self):
        rng = np.random.default_rng(2032)
        flat = np.full((81, 81, 3), 20, np.uint8)
        flat[:, 40:] = 140
        single_mode = rng.integers(90, 150, (81, 81, 3), np.uint8)
        for pixels in (flat, single_mode):
            mask = np.ones(pixels.shape[:2], bool)
            template = _PreparedTemplate(pixels, pixels, mask, mask.astype(np.uint8) * 255)
            self.assertIsNone(template.proposal_dim_mask)

    def test_dim_background_separates_identical_bright_floors_in_both_searches(self):
        rng = np.random.default_rng(917)
        atlas = rng.integers(10, 56, (260, 560), np.uint8)
        atlas = cv2.cvtColor(cv2.GaussianBlur(atlas, (3, 3), 0), cv2.COLOR_GRAY2BGR)
        for x in (130, 410):
            for y in range(60, 181):
                atlas[y, x - 10 : x + 11] = 130 + (y % 12) * 3
        geometry = MinimapGeometry(70, 70, 50)
        frame = atlas[50:190, 60:200].copy()
        locator = MinimapMapLocator(atlas, self.calibration, scale_override=1, top_k=4)
        # Full-image NCC assigns nearly identical scores to the two bright
        # strips, although their surrounding dark terrain is entirely distinct.
        with patch.object(_PreparedTemplate, "dim_terrain", property(lambda self: None)):
            old = locator.locate_near(frame, (67.5, -30), geometry, search_radius_m=50)
        self.assertIsNotNone(old)
        self.assertLess(old.margin, 0.10)
        template = locator._prepare_template(frame, geometry)
        self.assertIsNotNone(template.dim_terrain)
        mask, mask8, (centered, _) = template.dim_terrain
        for value in (mask, mask8, centered):
            self.assertFalse(value.flags.writeable)
        for result in (
            locator.locate_global(frame, geometry),
            locator.locate_near(frame, (67.5, -30), geometry, search_radius_m=50),
        ):
            self.assertEqual(result.status, "accepted")
            self.assertGreater(result.margin, 0.10)
            self.assertGreater(result.best.full_ncc, 0.9)
            self.assertAlmostEqual(result.best.map_x, 130, delta=1)
            self.assertAlmostEqual(result.best.map_y, 120, delta=1)
        tracker = MinimapTracker(locator)
        self.assertFalse(tracker.update(frame, geometry, now=1).accepted)
        self.assertTrue(tracker.update(frame, geometry, now=1.2).accepted)

    def test_flat_dark_region_and_single_mode_texture_do_not_gain_extra_weight(self):
        rng = np.random.default_rng(918)
        uniform = np.full((81, 81, 3), 25, np.uint8)
        uniform[25:55, 25:55] = 130
        single_mode = rng.integers(90, 150, (81, 81, 3), np.uint8)
        for ring in (uniform, single_mode):
            mask = np.ones(ring.shape[:2], bool)
            template = _PreparedTemplate(ring, ring, mask, mask.astype(np.uint8) * 255)
            self.assertIsNone(template.dim_terrain)

    def test_coverage_excludes_stronger_background_alias_before_peak_selection(self):
        class CoveredMap(np.ndarray):
            def candidate_mask(self, xs, ys):
                return (np.abs(np.asarray(ys)[:, None] - 400) < 10) & (np.abs(np.asarray(xs) - 200) < 10)

        image = self.map_image.copy().view(CoveredMap)
        # The exact match at x=500 is background. The slightly noisy copy at
        # x=200 is the only current-floor location, even with a one-peak budget.
        patch = image[260:541, 360:641].copy().astype(np.int16)
        noise = np.random.default_rng(12).integers(-8, 9, patch.shape)
        image[260:541, 60:341] = np.clip(patch + noise, 0, 255).astype(np.uint8)
        locator = MinimapMapLocator(image, self.calibration, scale_override=self.scale, top_k=1)
        for search in (
            lambda: locator.locate_global(self.frame, self.geometry),
            lambda: locator.locate_near(self.frame, (87.5, -100), self.geometry, search_radius_m=80),
        ):
            result = search()
            self.assertIsNotNone(result, locator.last_failure_reason)
            self.assertGreater(result.best.ncc, 0.8)
            self.assertAlmostEqual(result.best.map_x, 200, delta=2)
            self.assertAlmostEqual(result.best.map_y, 400, delta=2)

    def test_visible_texture_without_floor_coverage_cannot_produce_a_candidate(self):
        class UncoveredMap(np.ndarray):
            def candidate_mask(self, xs, ys):
                return np.zeros((len(ys), len(xs)), bool)

        locator = MinimapMapLocator(self.map_image.view(UncoveredMap), self.calibration, scale_override=self.scale)
        self.assertIsNone(locator.locate_global(self.frame, self.geometry))
        with patch.object(locator, "_match_scores", side_effect=AssertionError("uncovered floor must not correlate")):
            self.assertIsNone(locator.locate_near(self.frame, (125, -100), self.geometry))
        self.assertEqual(locator.last_failure_reason, "minimap_search_region_uncovered")

    def test_dense_antialiased_icons_reacquire_without_changing_acquisition_gates(self):
        frame = self.frame.copy()
        for x, y in ((190, 145), (225, 145), (195, 180), (230, 180), (210, 205)):
            cv2.rectangle(frame, (x - 5, y - 5), (x + 5, y + 5), (0, 0, 0), -1)
            cv2.rectangle(frame, (x - 3, y - 3), (x + 3, y + 3), (200, 200, 200), -1)
            cv2.rectangle(frame, (x - 1, y - 1), (x + 1, y + 1), (255, 255, 255), -1)
        locator = MinimapMapLocator(self.map_image, self.calibration, scale_override=self.scale)
        tracker = MinimapTracker(locator)
        self.assertFalse(tracker.update(frame, self.geometry, now=1).accepted)
        result = tracker.update(frame, self.geometry, now=1.2)
        self.assertTrue(result.accepted, result)
        self.assertGreaterEqual(result.locate.best.full_ncc, 0.65)
        self.assertGreaterEqual(result.locate.best.ncc, 0.85)
        self.assertAlmostEqual(result.position.map_x, self.center[0], delta=2)
        self.assertAlmostEqual(result.position.map_y, self.center[1], delta=2)

    def test_local_search_radius_uses_affine_world_meters(self):
        for matrix in (
            np.array([[0.125, 0, 0], [0, -0.125, 0]]),
            np.array([[0.125, 0.04, 0], [0.06, -0.5, 0]]),
        ):
            calibration = MapCalibration(matrix, np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2])
            locator = MinimapMapLocator(self.map_image, calibration, scale_override=self.scale)
            target = calibration.map_to_world(*self.center)
            # At 8 px/m this 7 m shift is 56 pixels, outside the old 4 px/m ROI.
            prior = (target[0] - 7, target[1])
            accepted = locator.locate_near(self.frame, prior, self.geometry, search_radius_m=8)
            self.assertIsNotNone(accepted)
            self.assertAlmostEqual(accepted.best.map_x, self.center[0], delta=2)
            self.assertAlmostEqual(accepted.best.map_y, self.center[1], delta=2)
            self.assertLessEqual(math.dist((accepted.best.world_x, accepted.best.world_z), prior), 8)
            smaller = locator.locate_near(self.frame, prior, self.geometry, search_radius_m=3)
            if smaller is not None:
                self.assertLessEqual(math.dist((smaller.best.world_x, smaller.best.world_z), prior), 3)

    def setUp(self):
        rng = np.random.default_rng(20260919)
        self.map_image = rng.integers(20, 230, size=(800, 900), dtype=np.uint8)
        self.map_image = cv2.GaussianBlur(self.map_image, (3, 3), 0)
        self.calibration = MapCalibration(
            matrix=np.array([[0.25, 0.0, 0.0], [0.0, -0.25, 0.0]]),
            inverse_matrix=np.array([[4.0, 0.0, 0.0], [0.0, -4.0, 0.0]]),
        )
        self.scale = 0.5
        self.center = (500.0, 400.0)
        self.geometry = MinimapGeometry(center_x=220, center_y=170, outer_radius=70)

        size = math.ceil(self.geometry.match_outer_radius * 2 + 1)
        map_radius = math.ceil((size - 1) / (2 * self.scale))
        cx, cy = map(int, self.center)
        patch = self.map_image[cy - map_radius : cy + map_radius + 1, cx - map_radius : cx + map_radius + 1]
        patch = cv2.resize(patch, (size, size), interpolation=cv2.INTER_AREA)
        self.frame = np.full((340, 440, 3), 127, dtype=np.uint8)
        x0 = self.geometry.center_x - size // 2
        y0 = self.geometry.center_y - size // 2
        self.frame[y0 : y0 + size, x0 : x0 + size] = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)

    def test_global_search_finds_exact_map_location(self):
        locator = MinimapMapLocator(
            self.map_image,
            self.calibration,
            scale_override=self.scale,
            top_k=5,
        )

        result = locator.locate_global(self.frame, self.geometry)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "accepted")
        self.assertAlmostEqual(result.best.map_x, self.center[0], delta=2.0)
        self.assertAlmostEqual(result.best.map_y, self.center[1], delta=2.0)
        self.assertGreater(result.confidence, 0.80)

    def test_prior_search_stays_inside_requested_radius(self):
        locator = MinimapMapLocator(
            self.map_image,
            self.calibration,
            scale_override=self.scale,
            top_k=5,
        )
        previous_world = self.calibration.map_to_world(self.center[0] + 12, self.center[1] - 8)

        result = locator.locate_near(
            self.frame,
            previous_world,
            self.geometry,
            search_radius_m=8.0,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.used_prior)
        self.assertGreater(result.margin, 0.15)
        self.assertLessEqual(
            math.hypot(result.best.world_x - previous_world[0], result.best.world_z - previous_world[1]),
            8.0,
        )


class _FakeLocator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def locate_global(self, frame, geometry):
        self.calls.append("global")
        return self.result

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0):
        self.calls.append("near")
        return self.result


def _candidate(x: float, z: float, ncc: float = 0.8) -> MapCandidate:
    return MapCandidate(
        map_x=x * 4.0,
        map_y=-z * 4.0,
        world_x=x,
        world_z=z,
        ncc=ncc,
        full_ncc=ncc,
    )


def _result(x: float, z: float, *, status: str = "accepted", ncc: float = 0.9) -> LocateResult:
    candidate = _candidate(x, z, ncc=ncc)
    return LocateResult(
        status=status,
        reason="test",
        best=candidate,
        candidates=(candidate,),
        elapsed_s=0.01,
        scale=0.5,
        used_prior=True,
    )


class TestMinimapTracker(unittest.TestCase):
    def test_live_recovery_uses_small_windows_between_same_frame_wide_checks(self):
        candidate = replace(_candidate(10, 10, 0.95), map_id="map02", tier_id="floor6")
        result = replace(_result(10, 10, ncc=0.95), best=candidate, candidates=(candidate,), spatial_margin=0.2)
        locator = _FakeLocator(result)
        locator.locate_on_map = Mock(return_value=result)
        tracker = MinimapTracker(locator, pending_max_gap_s=1)
        tracker._set_convinced(candidate, 1.0)
        for now in (1.2, 1.4, 1.6, 2.3):
            match = tracker.update(None, None, now=now, recovery_world=(10, 10), recovery_map_id="map02")
            self.assertTrue(match.accepted)
        radii = [call.kwargs["search_radius_m"] for call in locator.locate_on_map.call_args_list]
        np.testing.assert_allclose(radii, [80, 12, 12, 80])
        self.assertFalse(locator.calls)

    def test_weak_or_changed_floor_small_search_must_recheck_wide(self):
        candidate = replace(_candidate(10, 10, 0.95), map_id="map02", tier_id="floor6")
        result = replace(_result(10, 10, ncc=0.95), best=candidate, candidates=(candidate,), spatial_margin=0.2)
        for local in (
            replace(result, status="ambiguous", spatial_margin=0.01),
            replace(result, best=replace(candidate, tier_id="floor7")),
        ):
            locator = _FakeLocator(result)
            rejected = replace(result, status="rejected", reason="layer_scan_pending", spatial_margin=0)
            locator.locate_on_map = Mock(side_effect=[result, local, rejected, rejected])
            tracker = MinimapTracker(locator, pending_max_gap_s=1)
            tracker._set_convinced(candidate, 1)
            self.assertTrue(
                tracker.update(None, None, now=1.2, recovery_world=(10, 10), recovery_map_id="map02").accepted
            )
            for now in (1.4, 1.6):
                self.assertFalse(
                    tracker.update(None, None, now=now, recovery_world=(10, 10), recovery_map_id="map02").accepted
                )
            np.testing.assert_allclose(
                [c.kwargs["search_radius_m"] for c in locator.locate_on_map.call_args_list], [80, 12, 80, 80]
            )
            self.assertFalse(locator.calls)

    def test_prior_seed_cannot_promote_repeated_weak_frames(self):
        locator = _FakeLocator(_result(10.0, 0.0, status="ambiguous", ncc=0.6))
        tracker = MinimapTracker(locator, promote_hits=2, promote_window_s=10.0)

        first = tracker.update(None, MinimapGeometry(50, 50, 20), now=1.0, prior_world=(0.0, 0.0))
        second = tracker.update(None, MinimapGeometry(50, 50, 20), now=1.2, prior_world=(0.0, 0.0))

        self.assertEqual(first.status, "lost")
        self.assertFalse(first.accepted)
        self.assertTrue(first.used_prior)
        self.assertEqual(locator.calls, ["near", "near"])
        self.assertEqual(second.status, "lost")
        self.assertFalse(second.accepted)
        self.assertEqual(second.pending_hits, 0)

    def test_ambiguous_near_convinced_refreshes_without_waiting(self):
        locator = _FakeLocator(_result(1.0, 0.0, status="ambiguous", ncc=0.6))
        tracker = MinimapTracker(locator, promote_hits=3)
        tracker._set_convinced(_candidate(0.0, 0.0), now=1.0)

        result = tracker.update(None, MinimapGeometry(50, 50, 20), now=1.2)

        self.assertEqual(result.status, "convinced")
        self.assertTrue(result.accepted)
        self.assertEqual(result.position.world_x, 1.0)

    def test_far_candidate_waits_for_consistency(self):
        locator = _FakeLocator(_result(100.0, 0.0))
        tracker = MinimapTracker(locator, promote_hits=2, promote_window_s=10.0)
        tracker._set_convinced(_candidate(0.0, 0.0), now=1.0)

        first = tracker.update(None, MinimapGeometry(50, 50, 20), now=1.2)
        second = tracker.update(None, MinimapGeometry(50, 50, 20), now=1.4)

        self.assertEqual(first.status, "pending")
        self.assertFalse(first.accepted)
        self.assertEqual(second.status, "convinced")
        self.assertTrue(second.accepted)
        self.assertEqual(second.position.world_x, 100.0)


class TestMinimapLocatorOptimizations(unittest.TestCase):
    def setUp(self):
        self.geometry = MinimapGeometry(100.25, 99.75, 50)
        gray = np.random.default_rng(1).integers(20, 200, (1080, 1920), dtype=np.uint8)
        self.frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        self.locator = MinimapMapLocator(
            np.zeros((800, 900), dtype=np.uint8),
            MapCalibration(np.eye(2, 3), np.eye(2, 3)),
            scale_override=1.0,
        )

    def test_roi_mask_matches_full_frame_and_reuses_storage(self):
        from src.localization.minimap_odometry import annulus_mask

        g = self.geometry
        half = math.ceil(g.match_outer_radius)
        x0, y0 = round(g.center_x) - half, round(g.center_y) - half
        expected = annulus_mask(1080, 1920, (g.center_x, g.center_y), g.match_inner_radius, g.match_outer_radius)
        with patch("src.localization.minimap_map_locator.annulus_mask", wraps=annulus_mask) as build:
            first = self.locator._prepare_template(self.frame, g)
            second = self.locator._prepare_template(self.frame, g)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(build.call_args.args[:2], (2 * half + 1, 2 * half + 1))
        np.testing.assert_array_equal(first.mask, expected[y0 : y0 + 2 * half + 1, x0 : x0 + 2 * half + 1] >= 0.5)
        self.assertIs(first.mask, second.mask)
        self.assertIs(first.mask8, second.mask8)
        self.assertFalse(first.mask.flags.writeable)

    def test_mask_cache_is_bounded_and_keys_fractional_geometry(self):
        first = self.locator._prepare_template(self.frame, self.geometry)
        other = self.locator._prepare_template(self.frame, MinimapGeometry(100.4, 99.75, 50))
        self.assertIsNot(first.mask, other.mask)
        for i in range(12):
            self.locator._prepare_template(self.frame, MinimapGeometry(100 + i, 100, 50))
        self.assertLessEqual(len(self.locator._mask_cache), 8)

    def test_reused_color_template_preserves_full_candidate_scores(self):
        locator = MinimapMapLocator(np.zeros((100, 100, 3), dtype=np.uint8), self.locator.calibration)
        template = locator._prepare_template(self.frame, self.geometry)
        self.assertIsNotNone(template)
        for candidate in (
            np.roll(template.ring, 3, axis=1),
            np.full_like(template.ring, 80),
            template.ring.copy(),
        ):
            expected = robust_masked_zncc(template.ring, candidate, template.mask)
            actual = locator._candidate_from_patch(template, candidate, 50, 50, coarse_score=0.5)
            self.assertEqual(actual.ncc, expected.score)
            self.assertEqual(actual.full_ncc, expected.full_score)
            self.assertEqual(actual.inlier_ratio, expected.inlier_ratio)

    def test_exact_pixel_center_in_global_and_local_search(self):
        rng = np.random.default_rng(42)
        image = cv2.GaussianBlur(rng.integers(20, 230, (800, 900), dtype=np.uint8), (3, 3), 0)
        g = MinimapGeometry(100, 100, 50)
        frame = np.full((200, 200), 127, dtype=np.uint8)
        frame[57:144, 57:144] = image[357:444, 457:544]
        locator = MinimapMapLocator(image, self.locator.calibration, scale_override=1)
        for result in (locator.locate_global(frame, g), locator.locate_near(frame, (500, 400), g, search_radius_m=5)):
            self.assertEqual(result.status, "accepted")
            self.assertAlmostEqual(result.best.map_x, 500, delta=0.02)
            self.assertAlmostEqual(result.best.map_y, 400, delta=0.02)

    def test_resize_pixel_centers_at_multiple_scales(self):
        rng = np.random.default_rng(22)
        image = cv2.GaussianBlur(rng.integers(20, 230, (800, 900), dtype=np.uint8), (3, 3), 0)
        g = MinimapGeometry(100, 100, 50)
        for scale in (0.5, 0.75, 1.25):
            with self.subTest(scale=scale):
                margin = math.ceil(5 + 87 / (2 * scale))
                x0, y0 = 500 - margin, 400 - margin
                resized = cv2.resize(
                    image[y0 : 400 + margin + 1, x0 : 500 + margin + 1],
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )
                left = round(margin * scale - 43.5)
                frame = np.full((200, 200), 127, dtype=np.uint8)
                frame[57:144, 57:144] = resized[left : left + 87, left : left + 87]
                locator = MinimapMapLocator(image, self.locator.calibration, scale_override=scale)
                result = locator.locate_near(frame, (500, 400), g, search_radius_m=5)
                self.assertEqual(result.status, "accepted")
                self.assertAlmostEqual(result.best.map_x, x0 + (left + 43.5) / scale - 0.5, delta=0.03)
                self.assertAlmostEqual(result.best.map_y, y0 + (left + 43.5) / scale - 0.5, delta=0.03)

    def test_fractional_geometry_center_is_preserved(self):
        rng = np.random.default_rng(42)
        image = cv2.GaussianBlur(rng.integers(20, 230, (800, 900), dtype=np.uint8), (3, 3), 0)
        frame = np.full((200, 200), 127, dtype=np.uint8)
        frame[57:144, 57:144] = image[357:444, 457:544]
        locator = MinimapMapLocator(image, self.locator.calibration, scale_override=1)
        result = locator.locate_near(frame, (500, 400), self.geometry, search_radius_m=5)
        self.assertAlmostEqual(result.best.map_x, 500.25, delta=0.02)
        self.assertAlmostEqual(result.best.map_y, 399.75, delta=0.02)


class TestMinimapTrackerRecovery(unittest.TestCase):
    def setUp(self):
        self.g = MinimapGeometry(50, 50, 20)

    def test_long_gap_restarts_pending_confirmation(self):
        tracker = MinimapTracker(_FakeLocator(_result(10, 0, status="ambiguous")), promote_hits=10)
        tracker.update(None, self.g, now=0)
        result = tracker.update(None, self.g, now=100)
        self.assertFalse(result.accepted)
        self.assertEqual(result.pending_hits, 1)

    def test_continuous_observations_can_promote_by_duration(self):
        tracker = MinimapTracker(_FakeLocator(_result(10, 0)), promote_hits=10, promote_window_s=1)
        for now in (0, 0.4, 0.8):
            self.assertFalse(tracker.update(None, self.g, now=now).accepted)
        self.assertTrue(tracker.update(None, self.g, now=1.2).accepted)

    def test_stale_convinced_does_not_bypass_confirmation(self):
        tracker = MinimapTracker(_FakeLocator(_result(0, 0, status="ambiguous")))
        tracker._set_convinced(_candidate(0, 0), 0)
        self.assertFalse(tracker.update(None, self.g, now=10).accepted)

    def test_radius_grows_with_elapsed_time_and_is_capped(self):
        locator = _FakeLocator(_result(0, 0))
        tracker = MinimapTracker(locator)
        with patch.object(locator, "locate_near", wraps=locator.locate_near) as near:
            for dt, expected in ((0.1, 10), (1, 28), (2, 48)):
                tracker._set_convinced(_candidate(0, 0), 0)
                tracker.update(None, self.g, now=dt)
                self.assertAlmostEqual(near.call_args.kwargs["search_radius_m"], expected)
        tracker = MinimapTracker(locator, search_radius_m=5)
        tracker._set_convinced(_candidate(0, 0), 0)
        with patch.object(locator, "locate_near", wraps=locator.locate_near) as near:
            tracker.update(None, self.g, now=0.1)
            self.assertEqual(near.call_args.kwargs["search_radius_m"], 5)

    def test_failed_small_window_retries_wide_then_global(self):
        locator = _FakeLocator(_result(100, 0))
        tracker = MinimapTracker(locator)
        tracker._set_convinced(_candidate(0, 0), 0)
        with patch.object(locator, "locate_near", side_effect=[None, _result(0, 0, status="rejected")]) as near:
            result = tracker.update(None, self.g, now=0.1)
        self.assertEqual([c.kwargs["search_radius_m"] for c in near.call_args_list], [10, 80])
        self.assertEqual(locator.calls, ["global"])
        self.assertFalse(result.used_prior)
        self.assertFalse(result.accepted)

    def test_prediction_promotion_requires_wide_competition(self):
        local = _result(10, 0, ncc=0.95)
        competitor = _candidate(30, 0, 0.94)
        wide = replace(
            _result(10, 0, ncc=0.95),
            status="ambiguous",
            candidates=(_candidate(10, 0, 0.95), competitor),
            spatial_margin=0.01,
        )
        locator = _FakeLocator(local)
        tracker = MinimapTracker(locator, promote_hits=2)
        tracker._set_convinced(_candidate(0, 0), now=1.0)
        with patch.object(locator, "locate_near", side_effect=[local, wide, local, wide]) as near:
            first = tracker.update(None, self.g, now=3.5, prediction_world=(10, 0), prediction_radius_m=8)
            second = tracker.update(None, self.g, now=3.7, prediction_world=(10, 0), prediction_radius_m=8)
        self.assertFalse(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual([call.kwargs["search_radius_m"] for call in near.call_args_list], [8, 80, 8, 80])

    def test_prediction_competition_acceptance_is_cached_between_frames(self):
        local = _result(10, 0, ncc=0.95)
        wide = _result(10, 0, ncc=0.95)
        locator = _FakeLocator(local)
        tracker = MinimapTracker(locator, promote_hits=2)
        tracker._set_convinced(_candidate(0, 0), now=1.0)
        with patch.object(locator, "locate_near", side_effect=[local, wide, local]) as near:
            first = tracker.update(None, self.g, now=3.5, prediction_world=(10, 0), prediction_radius_m=8)
            second = tracker.update(None, self.g, now=3.7, prediction_world=(10, 0), prediction_radius_m=8)
        self.assertFalse(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual([call.kwargs["search_radius_m"] for call in near.call_args_list], [8, 80, 8])

    def test_rejected_wide_search_cannot_refresh_or_promote_prediction(self):
        local = _result(10, 0, ncc=0.95)
        wide = replace(local, status="rejected", reason="layer_scan_pending", spatial_margin=0.0)
        for first_time, min_margin, verification in product((1.2, 3.5), (0.0, 0.08), (wide, None)):
            with self.subTest(first_time=first_time, min_margin=min_margin, unavailable=verification is None):
                locator = _FakeLocator(local)
                tracker = MinimapTracker(locator, promote_hits=2, initial_min_margin=min_margin)
                anchor = _candidate(10, 0)
                tracker._set_convinced(anchor, now=1.0)
                with patch.object(locator, "locate_near", side_effect=[local, verification, local, verification]):
                    for now in (first_time, first_time + 0.2):
                        result = tracker.update(None, self.g, now=now, prediction_world=(10, 0))
                        self.assertFalse(result.accepted)
                        self.assertEqual(result.pending_hits, 0)
                        self.assertEqual(result.locate.margin, 0.0)
                        self.assertEqual(result.locate.status, "rejected")
                        self.assertIs(tracker.convinced, anchor)
                        self.assertEqual(tracker._convinced_t, 1.0)
                        self.assertIsNone(tracker._prediction_verified)

    def test_ordinary_fallback_invalidates_prediction_competition(self):
        local = _result(10, 0, ncc=0.95)
        for fallback in (local, replace(local, status="rejected")):
            with self.subTest(status=fallback.status):
                locator = _FakeLocator(local)
                tracker = MinimapTracker(locator)
                with patch.object(locator, "locate_near", return_value=local) as near:
                    tracker.update(None, self.g, now=1, prediction_world=(10, 0))
                    self.assertIsNotNone(tracker._prediction_verified)
                    with patch.object(tracker, "_locate_frame", return_value=(fallback, False, False)):
                        tracker.update(None, self.g, now=1.1)
                    self.assertIsNone(tracker._prediction_verified)
                    before = near.call_count
                    tracker.update(None, self.g, now=1.2, prediction_world=(10, 0))
                    self.assertEqual(near.call_count - before, 2)
                    self.assertEqual(near.call_args.kwargs["search_radius_m"], tracker.search_radius_m)

    def test_prediction_preserves_wide_margin_when_candidates_are_truncated(self):
        local = _result(10, 0, ncc=0.95)
        wide = replace(local, status="accepted", spatial_margin=0.01)
        locator = _FakeLocator(local)
        tracker = MinimapTracker(locator)
        tracker._set_convinced(local.best, now=1.0)
        with patch.object(locator, "locate_near", side_effect=[local, wide]):
            result = tracker.update(None, self.g, now=1.2, prediction_world=(10, 0))
        self.assertFalse(result.accepted)
        self.assertEqual(result.locate.margin, 0.01)
        self.assertIsNone(tracker._prediction_verified)

    def test_ambiguous_wide_margin_above_acquisition_threshold_cannot_promote(self):
        for now, margin in product((1.2, 3.5), (0.08, 0.10, 0.149)):
            with self.subTest(now=now, margin=margin):
                local = _result(10, 0, ncc=0.95)
                wide = replace(local, status="ambiguous", spatial_margin=margin)
                locator = _FakeLocator(local)
                tracker = MinimapTracker(locator)
                tracker._set_convinced(local.best, 1.0)
                with patch.object(locator, "locate_near", side_effect=[local, wide, local, wide]):
                    for stamp in (now, now + 0.2):
                        result = tracker.update(None, self.g, now=stamp, prediction_world=(10, 0))
                        self.assertFalse(result.accepted)
                        self.assertEqual(result.pending_hits, 0)
                        self.assertEqual(tracker._convinced_t, 1.0)

    def test_fresh_nonprediction_can_track_without_strong_acquisition_margin(self):
        local = replace(_result(10, 0), status="ambiguous", spatial_margin=0.05)
        tracker = MinimapTracker(_FakeLocator(local))
        tracker._set_convinced(local.best, 1.0)
        tracked = tracker.update(None, self.g, now=1.2)
        self.assertTrue(tracked.accepted)
        self.assertEqual(tracker._convinced_t, 1.2)
        self.assertLess(tracked.locate.margin, tracker.initial_min_margin)

    def test_ambiguous_small_window_is_verified_in_wide_window(self):
        locator = _FakeLocator(_result(100, 0))
        tracker = MinimapTracker(locator)
        tracker._set_convinced(_candidate(0, 0), 0)
        with patch.object(
            locator, "locate_near", side_effect=[_result(0, 0, status="ambiguous"), _result(100, 0)]
        ) as near:
            result = tracker.update(None, self.g, now=0.1)
        self.assertEqual(near.call_count, 2)
        self.assertFalse(result.accepted)

    def test_backwards_clock_resets_pending_and_trusted_position(self):
        tracker = MinimapTracker(_FakeLocator(_result(0, 0)))
        tracker.update(None, self.g, now=5)
        tracker.update(None, self.g, now=5.1)
        result = tracker.update(None, self.g, now=1)
        self.assertFalse(result.accepted)
        self.assertEqual(result.pending_hits, 1)


class TestMinimapAcquisitionSafety(unittest.TestCase):
    def test_expanded_mask_falls_back_to_original_coverage_without_lowering_limits(self):
        half = 140
        roi = np.full((281, 281, 3), 100, dtype=np.uint8)
        roi[:, ::8] = 255
        mask = np.ones((281, 281), dtype=bool)
        mask8 = mask.astype(np.uint8) * 255
        expected = MinimapMapLocator._legacy_visible_masks(roi, mask, mask8, half)
        result = MinimapMapLocator._visible_masks(roi, mask, mask8, half)
        self.assertIsNotNone(expected)
        np.testing.assert_array_equal(result[0], expected[0])
        self.assertGreaterEqual(result[0].mean(), 0.55)

    def test_icon_outline_padding_scales_without_erasing_dim_gray_terrain(self):
        for width in (640, 1920, 3840):
            with self.subTest(width=width):
                half = math.ceil(MinimapGeometry.from_frame(width, width * 9 // 16).match_outer_radius)
                size = 2 * half + 1
                roi = np.full((size, size, 3), 100, dtype=np.uint8)
                mask = np.ones((size, size), dtype=bool)
                mask8 = mask.astype(np.uint8) * 255
                center = half + half // 2
                body = max(1, round(3 * width / 1920))
                outline = max(1, round(2 * width / 1920))
                cv2.rectangle(
                    roi,
                    (center - body - outline, half - body - outline),
                    (center + body + outline, half + body + outline),
                    (0, 0, 0),
                    -1,
                )
                cv2.rectangle(roi, (center - body, half - body), (center + body, half + body), (200, 200, 200), -1)
                roi[half, center] = (255, 255, 255)
                visible, _ = MinimapMapLocator._visible_masks(roi, mask, mask8, half)
                self.assertFalse(visible[half, center - body - outline])
                self.assertTrue(visible[half, center - body - outline - 2])
                roi[:] = (199, 199, 199)
                unchanged, unchanged8 = MinimapMapLocator._visible_masks(roi, mask, mask8, half)
                self.assertIs(unchanged, mask)
                self.assertIs(unchanged8, mask8)

    def setUp(self):
        self.g = MinimapGeometry(50, 50, 30)

    def test_blank_and_localized_loading_logo_are_not_minimaps(self):
        for value in (0, 127, 255):
            patch = np.full((101, 101), value, dtype=np.uint8)
            self.assertFalse(_has_minimap_texture(patch, (50, 50), 50))
        patch = np.zeros((101, 101), dtype=np.uint8)
        cv2.putText(patch, "EF", (3, 35), cv2.FONT_HERSHEY_SIMPLEX, 1, 255, 3)
        self.assertGreater(patch.std(), 10)
        self.assertFalse(_has_minimap_texture(patch, (50, 50), 50))

    def test_empty_layer_roi_skips_correlation_without_rejecting_frame_texture(self):
        locator = MinimapMapLocator(
            np.zeros((300, 300), np.uint8), MapCalibration(np.eye(2, 3), np.eye(2, 3)), scale_override=1
        )
        frame = np.random.default_rng(8).integers(20, 200, (100, 100), dtype=np.uint8)
        self.assertIsNotNone(locator._prepare_template(frame, self.g))
        with patch("src.localization.minimap_map_locator.cv2.matchTemplate") as match:
            self.assertIsNone(locator.locate_near(frame, (100, 100), self.g, search_radius_m=4))
        match.assert_not_called()

    def test_distributed_grayscale_terrain_is_valid(self):
        terrain = np.random.default_rng(11).integers(20, 230, (101, 101), dtype=np.uint8)
        self.assertTrue(_has_minimap_texture(terrain, (50, 50), 50))
        # Border decoration alone cannot supply terrain evidence.
        terrain[:] = 127
        cv2.circle(terrain, (50, 50), 48, 255, 2)
        self.assertFalse(_has_minimap_texture(terrain, (50, 50), 50))

    def test_repeated_low_margin_never_acquires(self):
        best = _candidate(10, 0, 0.95)
        other = _candidate(100, 100, 0.94)
        locator = _FakeLocator(replace(_result(10, 0), best=best, candidates=(best, other), status="ambiguous"))
        tracker = MinimapTracker(locator)
        for i in range(20):
            self.assertFalse(tracker.update(None, self.g, now=i * 0.2).accepted)
        self.assertIsNone(tracker.convinced)

    def test_high_trimmed_score_cannot_hide_weak_full_match(self):
        candidate = replace(_candidate(10, 0, 0.95), full_ncc=0.6)
        locator = _FakeLocator(replace(_result(10, 0), best=candidate, candidates=(candidate,)))
        tracker = MinimapTracker(locator)
        for i in range(10):
            self.assertFalse(tracker.update(None, self.g, now=i * 0.2).accepted)

    def test_strong_margin_with_partial_overlay_can_acquire(self):
        best = replace(_candidate(10, 0, 0.92), full_ncc=0.67)
        other = _candidate(100, 100, 0.55)
        locator = _FakeLocator(replace(_result(10, 0), best=best, candidates=(best, other)))
        tracker = MinimapTracker(locator)
        self.assertFalse(tracker.update(None, self.g, now=0).accepted)
        self.assertTrue(tracker.update(None, self.g, now=0.2).accepted)

    def test_locator_rejects_blank_frame_before_search(self):
        locator = MinimapMapLocator(
            np.zeros((300, 300), dtype=np.uint8),
            MapCalibration(np.eye(2, 3), np.eye(2, 3)),
            scale_override=1,
        )
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch("src.localization.minimap_map_locator.cv2.matchTemplate") as match:
            self.assertIsNone(locator.locate_global(frame, self.g))
            self.assertIsNone(locator.locate_near(frame, (100, 100), self.g))
        match.assert_not_called()

    def test_zero_window_and_single_hit_config_still_require_two_frames(self):
        tracker = MinimapTracker(_FakeLocator(_result(10, 0)), promote_hits=1, promote_window_s=0)
        self.assertFalse(tracker.update(None, self.g, now=0).accepted)
        self.assertTrue(tracker.update(None, self.g, now=0.2).accepted)

    def test_lost_minimap_discards_trusted_position(self):
        tracker = MinimapTracker(_FakeLocator(None))
        tracker._set_convinced(_candidate(0, 0), 0)
        self.assertFalse(tracker.update(None, self.g, now=0.1).accepted)
        self.assertIsNone(tracker.convinced)

    def test_invalid_acquisition_thresholds(self):
        for kwargs in ({"initial_min_ncc": float("nan")}, {"initial_min_margin": -1}, {"initial_min_full_ncc": 2}):
            locator = _FakeLocator(None)
            with self.assertRaises(ValueError):
                MinimapTracker(locator, **kwargs)


class TestGitBaselineSource(unittest.TestCase):
    def test_option_and_revision_expression_injection_is_rejected_before_git(self):
        root = Path.cwd()
        for value in ("--output=outside.py", "HEAD", "abcdef0:path", "abcdef0 --output=x", "abcdef0\n--output=x"):
            with self.subTest(value=value), patch("scripts._minimap_baseline.subprocess.check_output") as command:
                with self.assertRaises(argparse.ArgumentTypeError):
                    read_baseline_source(value, root)
                command.assert_not_called()

    def test_invalid_resolved_id_never_reaches_git_show(self):
        root = Path.cwd()
        with patch(
            "scripts._minimap_baseline.subprocess.check_output", return_value=b"--output=outside.py\n"
        ) as command:
            with self.assertRaisesRegex(ValueError, "full commit ID"):
                read_baseline_source("abcdef0", root)
            self.assertEqual(command.call_count, 1)

    def test_short_and_full_commit_ids_read_the_same_fixed_locator_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src/localization/minimap_map_locator.py"
            source.parent.mkdir(parents=True)
            source.write_text("# baseline fixture\n", encoding="utf-8", newline="\n")
            for args in (
                ["init", "-q"],
                ["add", "src/localization/minimap_map_locator.py"],
                ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
            ):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode("ascii").strip()
            self.assertEqual(read_baseline_source(commit, root), "# baseline fixture\n")
            self.assertEqual(read_baseline_source(commit[:8], root), "# baseline fixture\n")


if __name__ == "__main__":
    unittest.main()
