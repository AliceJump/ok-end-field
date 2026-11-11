import argparse
import json
import math
import subprocess
import tempfile
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

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
    _subpixel_offset,
    deduplicate_candidates,
    masked_zncc,
    minimap_map_scale,
    robust_masked_zncc,
)
from src.localization.minimap_odometry import region_geometry


class TestMinimapMapLocatorMath(unittest.TestCase):
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

        self.assertEqual(locator.map_image.shape, (10, 20))
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
