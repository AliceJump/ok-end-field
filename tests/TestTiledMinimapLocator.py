import json
import math
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.localization.layer_search_index import LayerSearchIndex
from src.localization.layered_minimap_locator import LayeredMinimapLocator, layer_calibration
from src.localization.localization_workers import DecodeBudget, _ResourceExecutor
from src.localization.minimap_map_locator import MapCalibration, MinimapGeometry, MinimapMapLocator, MinimapTracker
from src.localization.tile_preparation import prepare_image
from src.localization.tiled_map_image import TiledMapImage, floor_options


class TestTiledMinimapLocator(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.path = self.root / "map.png"
        rng = np.random.default_rng(241)
        self.image = cv2.GaussianBlur(rng.integers(20, 180, (800, 900, 3), np.uint8), (3, 3), 0)
        cv2.imwrite(str(self.path), self.image)
        self.calibration = MapCalibration(
            np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]])
        )
        self.scale = 0.5
        self.center = (512, 384)
        self.geometry = MinimapGeometry(center_x=220, center_y=170, outer_radius=70)
        size = math.ceil(self.geometry.match_outer_radius * 2 + 1)
        radius = math.ceil((size - 1) / (2 * self.scale))
        x, y = self.center
        self.patch = self.image[y - radius : y + radius + 1, x - radius : x + radius + 1].copy()
        self.frame = np.full((340, 440, 3), 127, np.uint8)
        self.frame[170 - size // 2 : 170 - size // 2 + size, 220 - size // 2 : 220 - size // 2 + size] = cv2.resize(
            self.patch, (size, size), interpolation=cv2.INTER_AREA
        )

    def prepare(self):
        prepare_image(self.path, (900, 800), (0.25,), cache_root=self.cache, tile_size=128)
        return TiledMapImage.find(self.path, (900, 800), cache_root=self.cache)

    def locators(self):
        tiled = self.prepare()
        return MinimapMapLocator(self.image, self.calibration, scale_override=self.scale), MinimapMapLocator(
            tiled, self.calibration, scale_override=self.scale
        )

    def assert_same_candidates(self, left, right):
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        self.assertEqual(left.status, right.status)
        self.assertEqual(len(left.candidates), len(right.candidates))
        for a, b in zip(left.candidates, right.candidates, strict=True):
            self.assertAlmostEqual(a.map_x, b.map_x, places=6)
            self.assertAlmostEqual(a.map_y, b.map_y, places=6)
            self.assertAlmostEqual(a.ncc, b.ncc, places=7)
            self.assertAlmostEqual(a.full_ncc, b.full_ncc, places=7)
        self.assertAlmostEqual(left.margin, right.margin, places=7)

    def test_region_crosses_four_tile_corners_without_pixel_changes(self):
        tiled = self.prepare()
        tiled._limit = 128 * 128 * 3
        for box in ((120, 120, 270, 270), (890, 790, 900, 800), (0, 0, 130, 130), (250, 380, 630, 650)):
            x0, y0, x1, y1 = box
            with patch("src.localization.tiled_map_image.cv2.imread", side_effect=AssertionError("decoded original")):
                np.testing.assert_array_equal(tiled[y0:y1, x0:x1], self.image[y0:y1, x0:x1])
            self.assertLessEqual(tiled.nbytes, tiled._limit)
        tiled.close()
        self.assertEqual(tiled.nbytes, 0)
        with self.assertRaisesRegex(ValueError, "closed"):
            tiled[:10, :10]

    def test_prepared_overview_exactly_matches_original_global_resize(self):
        tiled = self.prepare()
        with patch("src.localization.tiled_map_image.cv2.imread", side_effect=AssertionError("decoded original")):
            image = tiled.overview(0.25)
        np.testing.assert_array_equal(
            image, cv2.resize(self.image, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        )
        self.assertFalse(image.flags.writeable)

    def test_unprepared_resolution_uses_exact_compatibility_overview(self):
        tiled = self.prepare()
        image = tiled.overview(0.3)
        np.testing.assert_array_equal(image, cv2.resize(self.image, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA))

    def test_fallback_releases_shared_memory_budget_on_decode_failure(self):
        tiled = self.prepare()
        budget = DecodeBudget(available=lambda: 0)

        def fail(*args, **kwargs):
            self.assertEqual(budget.active, 1)
            self.assertGreater(budget.reserved_bytes, self.image.nbytes)
            raise ValueError("decode failed")

        with (
            patch("src.localization.tiled_map_image._DECODE_BUDGET", budget),
            patch("src.localization.tiled_map_image.cv2.imread", side_effect=fail),
            self.assertRaisesRegex(ValueError, "decode failed"),
        ):
            tiled.overview(0.3)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_resource_worker_fallback_reserves_its_full_peak_only_once(self):
        tiled = self.prepare()
        locator = MinimapMapLocator(tiled, self.calibration, scale_override=0.6)
        budget = DecodeBudget(available=lambda: 0)
        workers = _ResourceExecutor(budget=budget)
        try:
            with patch("src.localization.tiled_map_image._DECODE_BUDGET", budget):
                self.assertFalse(locator.prepare_global_search(440, workers))
                pixels = next(iter(locator._pending_coarse.values())).result(timeout=5)
                self.assertTrue(locator.prepare_global_search(440, workers))
                np.testing.assert_array_equal(
                    pixels, cv2.resize(self.image, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
                )
        finally:
            tiled.close()
            workers.shutdown(wait=True, cancel_futures=True)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_non_object_prepared_indexes_fall_back_to_original_image(self):
        tiled = self.prepare()
        for value in (None, [], "invalid", 1):
            with self.subTest(value=value):
                (tiled.root / "index.json").write_text(json.dumps(value), encoding="utf-8")
                self.assertIsNone(TiledMapImage.open_prepared(self.path, (900, 800), cache_root=self.cache))

    def test_closed_source_does_not_publish_late_decoded_tiles(self):
        tiled = self.prepare()
        started, release = threading.Event(), threading.Event()
        read = tiled._read

        def delayed(record):
            result = read(record)
            started.set()
            if not release.wait(5):
                raise TimeoutError("test did not release decoder")
            return result

        with ThreadPoolExecutor(1) as workers, patch.object(tiled, "_read", side_effect=delayed):
            future = workers.submit(lambda: tiled[:20, :20])
            try:
                self.assertTrue(started.wait(5))
                tiled.close()
            finally:
                release.set()
            with self.assertRaisesRegex(ValueError, "closed"):
                future.result(timeout=5)
        self.assertEqual(tiled.nbytes, 0)
        self.assertFalse(tiled._cache)

    def test_adding_resolution_preserves_prepared_scales_and_live_readers(self):
        old = self.prepare()
        prepare_image(self.path, (900, 800), (0.3,), cache_root=self.cache, tile_size=128)
        current = TiledMapImage.find(self.path, (900, 800), cache_root=self.cache)
        with patch("src.localization.tiled_map_image.cv2.imread", side_effect=AssertionError("decoded original")):
            for scale in (0.25, 0.3):
                np.testing.assert_array_equal(
                    current.overview(scale),
                    cv2.resize(self.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
                )
            np.testing.assert_array_equal(old[:20, :20], current[:20, :20])

    def test_global_candidates_and_continuous_confirmation_match_dense_locator(self):
        dense, tiled = self.locators()
        tiled.map_image._limit = 256 * 1024
        with patch("src.localization.tiled_map_image.cv2.imread", side_effect=AssertionError("decoded original")):
            self.assert_same_candidates(
                dense.locate_global(self.frame, self.geometry), tiled.locate_global(self.frame, self.geometry)
            )
            tracker = MinimapTracker(tiled)
            self.assertFalse(tracker.update(self.frame, self.geometry, now=1).accepted)
            self.assertTrue(tracker.update(self.frame, self.geometry, now=1.2).accepted)
        self.assertLess(tiled.resident_bytes, self.image.nbytes * 0.8)

    def test_local_match_on_tile_seams_preserves_affine_coordinates(self):
        dense, tiled = self.locators()
        matrix = np.array([[0.125, 0.04, 0], [0.06, -0.5, 0]])
        calibration = MapCalibration(matrix, np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2])
        dense.calibration = tiled.calibration = calibration
        world = calibration.map_to_world(*self.center)
        self.assert_same_candidates(
            dense.locate_near(self.frame, world, self.geometry, search_radius_m=8),
            tiled.locate_near(self.frame, world, self.geometry, search_radius_m=8),
        )
        with patch.object(tiled.map_image, "_read", side_effect=AssertionError("outside ROI decoded")):
            self.assertIsNone(tiled.locate_near(self.frame, (-5000, 5000), self.geometry))

    def test_far_duplicate_remains_a_global_competitor(self):
        h, w = self.patch.shape[:2]
        self.image[180 - h // 2 : 180 - h // 2 + h, 180 - w // 2 : 180 - w // 2 + w] = self.patch
        cv2.imwrite(str(self.path), self.image)
        dense, tiled = self.locators()
        result = tiled.locate_global(self.frame, self.geometry)
        self.assert_same_candidates(dense.locate_global(self.frame, self.geometry), result)
        self.assertLess(result.margin, 0.1)
        tracker = MinimapTracker(tiled)
        self.assertFalse(tracker.update(self.frame, self.geometry, now=1).accepted)
        self.assertFalse(tracker.update(self.frame, self.geometry, now=1.2).accepted)

    def test_identical_overlapping_tiled_floors_cannot_prove_floor_identity(self):
        entry = {
            "id": "floor1",
            "path": self.path.name,
            "width": 900,
            "height": 800,
            "bounds": {"west": 0, "east": 225, "south": -200, "north": 0},
            "map_name": "map02",
            "map_category": "layered",
        }
        options = floor_options(entry)
        prepare_image(self.path, (900, 800), (0.25,), cache_root=self.cache, **options)
        entries = [entry, dict(entry, id="floor2")]
        base = MinimapMapLocator(np.roll(self.image, 200, axis=0), self.calibration, scale_override=self.scale)
        layered = LayeredMinimapLocator(base, "map02", self.root, {"layers": entries})
        self.addCleanup(layered.close)
        layered._cache_limit_bytes = 1
        for item in entries:
            source = TiledMapImage.find(self.path, (900, 800), cache_root=self.cache, **options)
            layered._cache[item["id"]] = MinimapMapLocator(source, self.calibration, scale_override=self.scale)
        world = self.calibration.map_to_world(*self.center)
        with patch("src.localization.tiled_map_image.cv2.imread", side_effect=AssertionError("decoded original")):
            result = layered.locate_near(self.frame, world, self.geometry, search_radius_m=8)
        self.assertGreater(result.best.ncc, 0.9)
        self.assertAlmostEqual(result.layer_margin, 0, places=7)
        self.assertIsNone(result.best.tier_id)
        self.assertEqual(set(layered._cache), {"floor1", "floor2"})
        self.assertGreater(layered._cache_bytes, 1)

    def test_corrupt_candidate_tile_cannot_be_skipped_to_prove_uniqueness(self):
        _, tiled = self.locators()
        source = tiled.map_image
        record = source.metadata["tiles"]["4,3"]
        (source.root / record["path"]).write_bytes(b"broken tile")
        tracker = MinimapTracker(tiled)
        with self.assertRaisesRegex(ValueError, "corrupt"):
            tracker.update(self.frame, self.geometry, now=1)
        self.assertIsNone(tracker.convinced)

    def test_corrupt_global_overview_rejects_the_whole_search(self):
        _, tiled = self.locators()
        source = tiled.map_image
        record = source.metadata["overviews"][(0.25).hex()]
        (source.root / record["path"]).write_bytes(b"broken overview")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            tiled.locate_global(self.frame, self.geometry)

    def test_changed_source_does_not_reuse_old_tiles(self):
        self.prepare()
        cv2.imwrite(str(self.path), np.zeros_like(self.image))
        self.assertIsNone(TiledMapImage.find(self.path, (900, 800), cache_root=self.cache))

    def test_incomplete_rebuild_does_not_replace_live_generation(self):
        tiled = self.prepare()
        before = (tiled.root / "index.json").read_bytes()
        with (
            patch("src.localization.tile_preparation._write_image", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            prepare_image(self.path, (900, 800), (0.25,), cache_root=self.cache, tile_size=128, force=True)
        self.assertEqual((tiled.root / "index.json").read_bytes(), before)
        np.testing.assert_array_equal(tiled[:20, :20], self.image[:20, :20])

    def test_missing_grid_entry_and_escaping_path_are_rejected(self):
        tiled = self.prepare()
        metadata = json.loads((tiled.root / "index.json").read_text(encoding="utf-8"))
        metadata["tiles"].pop("0,0")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            TiledMapImage(tiled.root, metadata)
        tiled.metadata["tiles"]["0,0"]["path"] = "../outside.webp"
        with self.assertRaisesRegex(ValueError, "escapes"):
            tiled[:20, :20]

    def test_normalized_floor_tiles_and_proposal_keep_original_alpha_and_pixel_centers(self):
        entry = {
            "id": "floor",
            "path": self.path.name,
            "width": 900,
            "height": 800,
            "bounds": {"west": -100, "east": 100, "south": -100, "north": 100},
            "map_name": "map02",
            "map_category": "layered",
        }
        image = cv2.cvtColor(self.image, cv2.COLOR_BGR2BGRA)
        image[:35, :, 3] = 0
        cv2.imwrite(str(self.path), image)
        expected_proposal = LayerSearchIndex.build(self.root, [entry], color=True)
        options = floor_options(entry)
        prepare_image(self.path, (900, 800), (0.25,), cache_root=self.cache, **options)
        tiled = TiledMapImage.find(self.path, (900, 800), cache_root=self.cache, **options)
        expected = self.image.copy()
        expected[:35] = 0
        _, (fx, fy) = layer_calibration(entry["bounds"], 900, 800)
        expected = cv2.resize(expected, None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
        np.testing.assert_array_equal(tiled[100:400, 200:600], expected[100:400, 200:600])
        np.testing.assert_array_equal(tiled.proposal(), expected_proposal.images[0][1])
        with (
            patch.object(TiledMapImage, "open_prepared", return_value=tiled),
            patch(
                "src.localization.layered_minimap_locator.cv2.imread", side_effect=AssertionError("decoded original")
            ),
        ):
            base = MinimapMapLocator(np.zeros((10, 10, 3), np.uint8), self.calibration)
            layered = LayeredMinimapLocator(base, "map02", self.root, {"layers": [entry]})
            floor = layered._decode(entry)
            np.testing.assert_array_equal(
                floor.calibration.matrix, layer_calibration(entry["bounds"], 900, 800)[0].matrix
            )


if __name__ == "__main__":
    unittest.main()
