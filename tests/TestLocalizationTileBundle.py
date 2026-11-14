import io
import json
import math
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from scripts.nav.verify_localization import verify
from src.localization import composite_overview_cache
from src.localization.composite_floor_image import CompositeFloorImage, _blend_regions
from src.localization.layered_minimap_locator import LayeredMinimapLocator
from src.localization.localization_bundle import LocalizationTileBundle
from src.localization.minimap_map_locator import (
    COARSE_FACTOR,
    MapCalibration,
    MinimapGeometry,
    MinimapMapLocator,
    _PreparedTemplate,
    minimap_map_scale,
)
from src.localization.native_tile_image import ZeroPaddedSearchRaster
from src.localization.template_correlation import correlate_bounded, correlate_color_zncc
from tests.localization_bundle_fixture import LocalizationBundleFixture


class TestZeroPaddedSearchRaster(unittest.TestCase):
    def test_compact_shared_masks_match_opencv_for_small_and_blocked_searches(self):
        rng = np.random.default_rng(2061)
        for height, width in ((150, 150), (324, 324), (410, 610)):
            with self.subTest(shape=(height, width)):
                pixels = rng.integers(5, 240, (height, width, 3), np.uint8)
                template = pixels[3:146, 5:144].copy()
                masks = tuple((rng.random((143, 139)) > cutoff).astype(np.uint8) * 255 for cutoff in (0.2, 0.45))
                expected = sum(
                    cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED, mask=mask) / 2 for mask in masks
                )
                for allowed in (None, rng.random(expected.shape) > 0.75):
                    actual = correlate_color_zncc(pixels, template, masks=masks, allowed=allowed, compact=True)
                    selected = np.ones(expected.shape, bool) if allowed is None else allowed
                    np.testing.assert_allclose(actual[selected], expected[selected], atol=2e-5, rtol=0)
                    if allowed is None:
                        self.assertGreater(actual[3, 5], 0.99999)

    def test_compact_sparse_search_keeps_distant_aliases_and_complete_halos(self):
        rng = np.random.default_rng(2062)
        pixels = rng.integers(5, 240, (380, 610, 3), np.uint8)
        template = pixels[110:253, 120:263].copy()
        pixels[230:373, 460:603] = template
        masks = tuple((rng.random((143, 143)) > cutoff).astype(np.uint8) for cutoff in (0.15, 0.5))
        allowed = np.zeros((238, 468), bool)
        allowed[108:113, 118:123] = True
        allowed[228:233, 458:463] = True
        expected = sum(cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED, mask=m) / 2 for m in masks)
        actual = correlate_color_zncc(pixels, template, masks=masks, allowed=allowed, compact=True)
        np.testing.assert_allclose(actual[allowed], expected[allowed], atol=2e-5, rtol=0)
        self.assertGreater(actual[110, 120], 0.99999)
        self.assertGreater(actual[230, 460], 0.99999)

    def test_float_color_inputs_keep_the_existing_opencv_path(self):
        rng = np.random.default_rng(2054)
        image = rng.random((90, 110, 3), dtype=np.float32)
        template = SimpleNamespace(flat=image[10:50, 20:60].copy(), dim_terrain=None)
        locator = object.__new__(MinimapMapLocator)
        locator.color, locator.coarse_factor = True, 0.5
        with patch(
            "src.localization.minimap_map_locator.correlate_color_zncc", side_effect=AssertionError("uint8-only path")
        ):
            actual = locator._match_scores(image, template, coarse=True)
        expected = cv2.matchTemplate(
            image, cv2.resize(template.flat, (20, 20), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED
        )
        np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=0)

    def test_shared_fft_preserves_both_masks_cross_block_peaks_and_distant_aliases(self):
        rng = np.random.default_rng(2051)
        pixels = rng.integers(15, 240, (480, 630, 3), np.uint8)
        template = pixels[225:256, 231:256].copy()
        pixels[420:451, 580:605] = template
        mask = (rng.random(template.shape[:2]) > 0.35).astype(np.uint8) * 255
        expected = 0.5 * (
            cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED)
            + cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        )
        actual = correlate_color_zncc(pixels, template, masks=(None, mask))
        np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=0)
        self.assertGreater(actual[225, 231], 0.99999)
        self.assertGreater(actual[420, 580], 0.99999)

    def test_shared_fft_sparse_coverage_has_bounded_scratch_and_complete_halos(self):
        rng = np.random.default_rng(2052)
        pixels = rng.integers(10, 240, (75, 95, 3), np.uint8)
        raster = ZeroPaddedSearchRaster((700, 900, 3), (805, 625, 900, 700), pixels)
        template = pixels[40:61, 60:85].copy()
        mask = (rng.random(template.shape[:2]) > 0.4).astype(np.uint8)
        allowed = np.zeros((680, 876), bool)
        allowed[660:675, 855:875] = True
        with (
            patch.object(ZeroPaddedSearchRaster, "as_dense", side_effect=AssertionError("dense image")),
            patch.object(cv2, "dft", wraps=cv2.dft) as dft,
        ):
            result = correlate_color_zncc(
                raster,
                template,
                masks=(None, mask),
                allowed=allowed,
                output_bounds=raster.correlation_bounds(template.shape[:2]),
            )
        self.assertLessEqual(max(call.args[0].nbytes for call in dft.call_args_list), 256 * 256 * 8)
        self.assertGreater(result[665, 865], 0.99999)
        for x, y in ((855, 660), (865, 665), (874, 674)):
            region = raster[y : y + 21, x : x + 25]
            expected = 0.5 * sum(
                cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=selected)[0, 0]
                for selected in (None, mask)
            )
            self.assertAlmostEqual(float(result[y, x]), expected, places=5)
        with patch.object(ZeroPaddedSearchRaster, "__getitem__", side_effect=AssertionError("empty coverage read")):
            correlate_color_zncc(raster, template, masks=(None, mask), allowed=np.zeros_like(allowed))

    def test_shared_fft_low_variance_matches_independent_double_precision_formula(self):
        pixels = np.full((38, 47, 3), 255, np.uint8)
        pixels[11, 17, 0] = 254
        pixels[12, 19, 2] = 253
        template = pixels[8:17, 14:25].copy()
        mask = np.ones(template.shape[:2], np.uint8)
        mask[:2, :3] = 0
        actual = correlate_color_zncc(pixels, template, masks=(None, mask))
        for x, y in ((14, 8), (13, 7), (0, 0), (16, 9)):
            expected = []
            for selected in (np.ones_like(mask, bool), mask != 0):
                t = template[selected].astype(np.float64)
                p = pixels[y : y + 9, x : x + 11][selected].astype(np.float64)
                t -= t.mean(axis=0)
                p -= p.mean(axis=0)
                denominator = np.sqrt(np.sum(t * t) * np.sum(p * p))
                expected.append(float(np.sum(t * p) / denominator) if denominator else (0 if selected.all() else -1))
            self.assertAlmostEqual(float(actual[y, x]), sum(expected) / 2, places=6)

    def test_shared_fft_constant_and_empty_rasters_keep_sanitized_score_conventions(self):
        empty = ZeroPaddedSearchRaster((80, 100, 3), (0, 0, 0, 0), None)
        template = np.full((15, 17, 3), (10, 30, 50), np.uint8)
        mask = np.ones(template.shape[:2], np.uint8)
        for image in (empty, empty.as_dense()):
            actual = correlate_color_zncc(
                image, template, masks=(None, mask), output_bounds=empty.correlation_bounds(template.shape[:2])
            )
            np.testing.assert_array_equal(actual, 0)
        template[3, 5, 1] += 1
        actual = correlate_color_zncc(
            empty, template, masks=(None, mask), output_bounds=empty.correlation_bounds(template.shape[:2])
        )
        np.testing.assert_array_equal(actual, -0.5)

    def test_shared_fft_large_bright_flat_windows_do_not_create_variance_from_roundoff(self):
        pixels = np.full((210, 220, 3), 255, np.uint8)
        template = pixels[:180, :181].copy()
        template[80, 90, 0] = 254
        mask = (np.random.default_rng(2053).random((180, 181)) > 0.2).astype(np.uint8)
        mask[80, 90] = 1
        result = correlate_color_zncc(pixels, template, masks=(None, mask))
        np.testing.assert_array_equal(result, -0.5)

    def test_candidate_blocks_keep_complete_windows_and_skip_empty_regions(self):
        rng = np.random.default_rng(2050)
        pixels = rng.integers(20, 180, (250, 310, 3), np.uint8)
        template = pixels[150:181, 205:240].copy()
        mask = (rng.random(template.shape[:2]) > 0.3).astype(np.uint8)
        allowed = np.zeros((220, 276), bool)
        allowed[143:168, 198:224] = True  # Crosses a block edge.
        expected = cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        with patch.object(cv2, "matchTemplate", wraps=cv2.matchTemplate) as match:
            actual = correlate_bounded(pixels, template, mask=mask, block_size=64, allowed=allowed)
        np.testing.assert_allclose(actual[allowed], expected[allowed], atol=2e-5, rtol=0)
        self.assertLess(
            sum(c.args[0].shape[0] * c.args[0].shape[1] for c in match.call_args_list),
            pixels.shape[0] * pixels.shape[1] / 4,
        )
        self.assertGreater(actual[150, 205], 0.999)
        with patch.object(cv2, "matchTemplate", side_effect=AssertionError("empty block matched")):
            empty = correlate_bounded(pixels, template, allowed=np.zeros_like(allowed))
        self.assertTrue(np.isnan(empty).all())

    def test_large_sparse_search_keeps_empty_windows_and_boundary_peaks(self):
        pixels = np.random.default_rng(2049).integers(20, 180, (410, 720, 3), np.uint8)
        raster = ZeroPaddedSearchRaster((580, 940, 3), (190, 150, 910, 560), pixels)
        template = pixels[370:391, 680:705].copy()
        mask = np.ones(template.shape[:2], np.uint8)
        mask[4:12, 3:18] = 0
        for selected in (None, mask):
            with patch.object(ZeroPaddedSearchRaster, "as_dense", side_effect=AssertionError("full materialization")):
                actual = raster.match_scores(template, mask=selected)
            self.assertGreater(actual[520, 870], 0.999)
            for left, top in ((168, 135), (600, 490), (870, 520), (903, 555)):
                patch_pixels = raster[top : top + 21, left : left + 25]
                expected = cv2.matchTemplate(patch_pixels, template, cv2.TM_CCOEFF_NORMED, mask=selected)[0, 0]
                self.assertAlmostEqual(actual[top, left], expected, places=5)
            self.assertTrue(np.isnan(actual[:120]).all() if selected is not None else not actual[:120].any())

    def test_blocked_correlation_preserves_all_windows_and_distant_aliases(self):
        rng = np.random.default_rng(2047)
        pixels = rng.integers(20, 180, (180, 230, 3), np.uint8)
        template = pixels[52:73, 57:82].copy()
        pixels[142:163, 184:209] = template
        mask = (rng.random(template.shape[:2]) > 0.25).astype(np.uint8) * 255
        for selected in (None, mask):
            expected = cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED, mask=selected)
            actual = correlate_bounded(pixels, template, mask=selected, block_size=32)
            np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=0)
            self.assertGreater(actual[52, 57], 0.999)
            self.assertGreater(actual[142, 184], 0.999)

    def test_sparse_blocked_search_reads_halos_without_full_image_materialization(self):
        pixels = np.random.default_rng(2048).integers(20, 180, (95, 110, 3), np.uint8)
        raster = ZeroPaddedSearchRaster((700, 900, 3), (790, 605, 900, 700), pixels)
        template = pixels[50:71, 60:85]
        with patch.object(ZeroPaddedSearchRaster, "as_dense", side_effect=AssertionError("full materialization")):
            actual = correlate_bounded(raster, template, block_size=128)
        self.assertGreater(actual[655, 850], 0.999)
        self.assertEqual(actual.shape, (680, 876))

    def test_masked_background_search_preserves_sparse_coordinates_and_empty_windows(self):
        pixels = np.random.default_rng(43).integers(20, 255, (10, 12, 3), np.uint8)
        raster = ZeroPaddedSearchRaster((61, 65, 3), (53, 51, 65, 61), pixels)
        dense = raster.as_dense()
        template = dense[46:54, 48:57]
        mask = np.ones(template.shape[:2], np.uint8) * 255
        mask[1:3, 2:5] = 0
        scores = raster.match_scores(template, mask=mask)
        for x, y in ((48, 46), (47, 51), (52, 50)):
            patch = dense[y : y + template.shape[0], x : x + template.shape[1]]
            expected = cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED, mask=mask)[0, 0]
            self.assertAlmostEqual(scores[y, x], expected, places=5)
        self.assertTrue(np.isnan(scores[:44]).all())
        empty = ZeroPaddedSearchRaster((61, 65, 3), (0, 0, 0, 0), None)
        self.assertTrue(np.isnan(empty.match_scores(template, mask=mask)).all())

    def test_sparse_match_keeps_edge_windows_and_logical_coordinates(self):
        pixels = np.random.default_rng(42).integers(20, 255, (10, 12, 3), np.uint8)
        raster = ZeroPaddedSearchRaster((61, 65, 3), (53, 51, 65, 61), pixels)
        dense = raster.as_dense()
        template = dense[46:54, 48:57]
        expected = cv2.matchTemplate(dense, template, cv2.TM_CCOEFF_NORMED)
        with patch.object(cv2, "matchTemplate", wraps=cv2.matchTemplate) as match:
            scores = raster.match_scores(template)
        self.assertEqual(match.call_args.args[0].shape, (17, 20, 3))
        np.testing.assert_allclose(scores, expected, atol=1e-5, rtol=0)
        self.assertEqual(cv2.minMaxLoc(scores)[3], (48, 46))
        self.assertFalse(scores[:44].any())

    def test_empty_raster_retains_constant_template_and_zero_window_semantics(self):
        raster = ZeroPaddedSearchRaster((27, 33, 3), (0, 0, 0, 0), None)
        for template in (np.zeros((7, 9, 3), np.uint8), np.arange(7 * 9 * 3, dtype=np.uint8).reshape(7, 9, 3)):
            with self.subTest(constant=not template.any()):
                expected = cv2.matchTemplate(raster.as_dense(), template, cv2.TM_CCOEFF_NORMED)
                np.testing.assert_array_equal(raster.match_scores(template), expected)


class TestLocalizationTileBundle(LocalizationBundleFixture, unittest.TestCase):
    def test_search_calibration_rejects_even_adjacent_float_values(self):
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        LocalizationTileBundle(self.root, manifest)
        for key in ("scale_constant", "match_pixels_per_meter", "floor_rank_pixels_per_meter", "coarse_factor"):
            for direction in (-math.inf, math.inf):
                changed = {
                    **manifest,
                    "search": {**manifest["search"], key: math.nextafter(manifest["search"][key], direction)},
                }
                with (
                    self.subTest(field=key, direction=direction),
                    self.assertRaisesRegex(ValueError, "Unsupported localization tile matching scales"),
                ):
                    LocalizationTileBundle(self.root, changed)

    def test_search_restores_pixels_at_original_offset_and_handles_empty_rank(self):
        coarse = self.base_index["coarse"][0]
        height, width = coarse["height"], coarse["width"]
        left, top, right, bottom = 4, 0, 11, 8
        expected = np.zeros((height, width, 3), np.uint8)
        expected[top:bottom, left:right] = (1, 0, 170)
        expected[0, left] = (1, 0, 0)
        crop = self.write_image("maps/main/4/coarse-crop.webp", expected[top:bottom, left:right])
        coarse.update({k: crop[k] for k in ("path", "size", "sha256")}, roi=[left, top, right, bottom])
        rank = self.floor_index["floor_rank"]
        rank.update(roi=[0, 0, 0, 0], path=None, size=0, sha256=None)
        self.write_indexes()
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        image = locator.base.map_image
        np.testing.assert_array_equal(image.overview(coarse["scale"]), expected)
        sparse = locator.base._coarse_map(coarse["scale"])
        self.assertIsInstance(sparse, ZeroPaddedSearchRaster)
        self.assertEqual(sparse.nbytes, expected[top:bottom, left:right].nbytes)
        np.testing.assert_array_equal(image[60:129, 61:131], self.base_match[60:129, 61:131])
        locator.prepare_layer_index()
        ((tier, pixels),) = locator._search_index.images
        self.assertEqual(tier, "floor")
        np.testing.assert_array_equal(pixels, np.zeros_like(self.floor_rank))

    def test_rejects_search_roi_outside_its_logical_raster(self):
        self.base_index["coarse"][0]["roi"][0] = -1
        self.write_indexes()
        with self.assertRaisesRegex(ValueError, "ROI exceeds"):
            self.store.load("main")

    def test_v3_restores_native_tile_offsets_and_skips_zero_payloads(self):
        zero, full, cropped = self.base_index["tiles"][:3]
        zero.update(storage="zero", roi=[0, 0, 0, 0], path=None, size=0, sha256=None)
        expected = self.base_match.copy()
        expected[:64, :] = 0
        expected[8:60, 71:114] = self.base_match[8:60, 71:114]
        expected[10:22, 129:131] = self.base_match[10:22, 129:131]
        full.update(self.write_image("maps/main/4/native-full.webp", expected[:64, 64:128]))
        full.update(roi=[7, 8, 50, 60])
        crop = self.write_image("maps/main/4/native-crop.webp", expected[10:22, 129:131])
        cropped.update({k: crop[k] for k in ("path", "size", "sha256")}, storage="roi", roi=[1, 10, 3, 22])
        self.write_indexes()
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        image = locator.base.map_image
        with patch.object(image, "_read_tile", side_effect=AssertionError):
            np.testing.assert_array_equal(image[:8, :131], expected[:8, :131])
        with patch.object(image, "_read_tile", wraps=image._read_tile) as read:
            np.testing.assert_array_equal(image[7:61, 61:131], expected[7:61, 61:131])
            self.assertEqual(read.call_count, 2)
        np.testing.assert_array_equal(image[55:70, 61:131], expected[55:70, 61:131])
        paths = {record["path"] for record in image.resource_records()}
        self.assertNotIn(None, paths)
        self.assertIn(crop["path"], paths)

    def test_v3_requires_explicit_zero_tiles_and_valid_payloads(self):
        tile = self.base_index["tiles"][0]
        self.write_indexes()
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        payload = self.root / tile["path"]
        payload.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "corrupt tiled image"):
            locator.base.map_image[:1, :1]
        payload.unlink()
        with self.assertRaises(FileNotFoundError):
            locator.base.map_image[:1, :1]

        self.base_index["tiles"].pop(0)
        self.write_indexes()
        with self.assertRaisesRegex(ValueError, "Incomplete localization tile"):
            self.store.load("main")

    def test_v3_verifier_rejects_incorrect_roi_and_unlisted_files(self):
        tile = self.base_index["tiles"][0]
        self.write_indexes()
        self.assertEqual(verify(self.root)["base_maps"], 1)
        orphan = self.root / "unlisted.webp"
        orphan.write_bytes(b"unlisted")
        with self.assertRaisesRegex(ValueError, "Unlisted localization files"):
            verify(self.root)
        orphan.unlink()

        tile["roi"] = [1, 0, tile["width"], tile["height"]]
        self.write_indexes()
        with self.assertRaisesRegex(ValueError, "ROI disagrees with matching pixels"):
            verify(self.root)

    def test_v3_verifier_decodes_and_checks_native_crop_payload(self):
        tile = self.floor_index["tiles"][0]
        for label, pixels in (
            ("wrong shape", self.pixels[:6, :7, :3]),
            ("unexpected alpha", self.pixels[:8, 57:64, :]),
        ):
            with self.subTest(label=label):
                crop = self.write_image(tile["path"], pixels)
                tile.update({key: crop[key] for key in ("size", "sha256")}, storage="roi", roi=[0, 0, 7, 8])
                self.write_indexes()
                with self.assertRaisesRegex(ValueError, "invalid tiled image resource"):
                    verify(self.root)

    def test_standalone_search_resources_skip_old_images_and_caches(self):
        with (
            patch("cv2.imread", side_effect=AssertionError),
        ):
            _, locator = self.store.load("main")
            self.addCleanup(locator.close)
            self.assertEqual(locator.calibration.map_to_world(4, 8), (-15, 62))
            np.testing.assert_array_equal(locator.base.map_image[60:129, 61:131], self.base_match[60:129, 61:131])
            for width in (1920, 2560):
                scale = minimap_map_scale(width) * COARSE_FACTOR
                np.testing.assert_array_equal(
                    locator.base.map_image.overview(scale),
                    cv2.resize(self.base_match, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
                )
            with self.assertRaisesRegex(ValueError, "no global search image"):
                locator.base.map_image.overview(minimap_map_scale(1366) * COARSE_FACTOR)
            locator.prepare_layer_index()
            ((tier, rank),) = locator._search_index.images
            self.assertEqual(tier, "floor")
            np.testing.assert_array_equal(rank, self.floor_rank)

    def test_floor_roi_preserves_full_raster_phase_alpha_and_edges(self):
        for scale in ((0.5, 0.5), (0.8, 0.8), (0.818273, 0.905123), (1.10234, 0.89123)):
            with self.subTest(scale=scale):
                self.write_floor(scale)
                _, locator = self.store.load("main")
                try:
                    floor = locator._load(locator.entries[0])
                    for roi in (np.s_[40:83, 43:85], np.s_[-23:, -25:], np.s_[:21, :19]):
                        actual, expected = floor.map_image[roi], self.floor_match[roi]
                        np.testing.assert_allclose(actual, expected, atol=1, rtol=0)
                finally:
                    locator.close()

    def test_native_alpha_view_preserves_partial_alpha_and_opaque_black(self):
        self.pixels[10:20, 10:20, :3] = 0
        self.write_floor((0.8, 0.8))
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        floor = locator._load(locator.entries[0])
        alpha = floor.map_image.alpha_image()
        self.addCleanup(alpha.close)
        expected = cv2.resize(self.pixels[:, :, 3], None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
        actual = alpha[:, :][:, :, 0]
        self.assertEqual(alpha.shape[2], 1)
        self.assertEqual(alpha[:, :].nbytes, expected.size)
        np.testing.assert_allclose(actual, expected, atol=1, rtol=0)
        self.assertEqual(actual[12, 12], 255)
        self.assertEqual(actual[51, 15], 0)
        self.assertEqual(actual[15, 50], 64)
        alpha.close()
        self.assertGreater(floor.map_image[:4, :4].size, 0)

    def test_v3_alpha_outside_matching_roi_and_entirely_black_tiles_are_preserved(self):
        self.pixels[:, :, :3] = 0
        self.pixels[:, :, 3] = 255
        self.pixels[20:40, 20:40, :3] = (30, 60, 90)
        self.write_floor((0.8, 0.8))
        for tile in self.floor_index["tiles"]:
            tile["roi"] = [20, 20, 40, 40] if tile["column"] == tile["row"] == 0 else [0, 0, 0, 0]
        self.write_indexes()
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        source = locator._load(locator.entries[0]).map_image
        source.verify_sparse_payloads({})
        alpha = source.alpha_image()
        self.addCleanup(alpha.close)
        np.testing.assert_array_equal(alpha[:, :], np.full(alpha.shape, 255, np.uint8))
        np.testing.assert_array_equal(source[:, :], self.floor_match)

    def test_corrupt_native_alpha_is_not_treated_as_uncovered_floor(self):
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        source = locator._load(locator.entries[0]).map_image
        alpha = source.alpha_image()
        self.addCleanup(alpha.close)
        record = next(source.resource_records())
        source._resource_path(record).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "corrupt tiled image"):
            alpha[:4, :4]

    def test_overlapping_rois_reuse_the_same_aligned_raster_within_the_tile_budget(self):
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        image = locator._load(locator.entries[0]).map_image
        image._limit = 40_000
        with patch.object(image, "_read_region", wraps=image._read_region) as read:
            first = image[40:80, 43:84]
            second = image[41:79, 42:83]
        self.assertEqual(read.call_count, 1)
        np.testing.assert_allclose(first, self.floor_match[40:80, 43:84], atol=1, rtol=0)
        np.testing.assert_allclose(second, self.floor_match[41:79, 42:83], atol=1, rtol=0)
        self.assertFalse(second.flags.writeable)
        self.assertLessEqual(image.nbytes, image._limit)
        image.close()
        self.assertEqual(image.nbytes, 0)

    def test_repeated_content_addressed_tiles_are_decoded_once(self):
        first, second = self.base_index["tiles"][:2]
        second.update({key: first[key] for key in ("path", "sha256", "size")})
        self.write_json(self.base["image"], self.base_index)
        self.write_manifest()
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        image = locator.base.map_image
        with patch.object(image, "_read_tile", wraps=image._read_tile) as read:
            pixels = image[:64, :128]
        self.assertEqual(read.call_count, 1)
        np.testing.assert_array_equal(pixels[:, :64], pixels[:, 64:])

    def test_parallel_floor_results_keep_manifest_order(self):
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        entry = locator.entries[0]
        first, second = locator._decode(entry), locator._decode(entry)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        completed_second = threading.Event()

        def match_first(template, *args):
            for pixels in (template.flat, template.ring, template.mask, template.mask8, template.color_zncc[0]):
                self.assertFalse(pixels.flags.writeable)
            self.assertTrue(completed_second.wait(3))
            return "first"

        def match_second(*args):
            completed_second.set()
            return "second"

        gray = np.random.default_rng(250).integers(20, 160, self.pixels.shape[:2], dtype=np.uint8)
        frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        geometry = MinimapGeometry(64, 64, 40)
        with (
            ThreadPoolExecutor(max_workers=2) as matches,
            patch.object(locator, "_resource_workers", SimpleNamespace(matches=matches)),
            patch.object(first, "_locate_near_template", side_effect=match_first),
            patch.object(second, "_locate_near_template", side_effect=match_second),
        ):
            result = locator._match_near_layers(frame, geometry, (0, 0), 80, [(entry, first), (entry, second)])
        self.assertEqual(result, ["first", "second"])

    def test_duplicate_tile_does_not_stand_in_for_unknown_cell(self):
        self.base_index["tiles"][-1] = self.base_index["tiles"][0]
        self.write_json(self.base["image"], self.base_index)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Duplicate localization tile"):
            self.store.load("main")

    def test_missing_or_corrupt_tile_cannot_be_read_as_blank(self):
        _, locator = self.store.load("main")
        self.addCleanup(locator.close)
        tile = self.root / self.base_index["tiles"][0]["path"]
        tile.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "corrupt tiled image"):
            locator.base.map_image[:1, :1]
        tile.unlink()
        with self.assertRaises(FileNotFoundError):
            locator.base.map_image[:1, :1]

    def test_failed_base_load_keeps_global_competition_unavailable(self):
        missing = {**self.base, "map_id": "missing", "image": "maps/missing/4/tiles.json"}
        self.manifest["base_maps"].append(missing)
        self.write_manifest()
        _, locator = self.store.load()
        self.addCleanup(locator.close)
        self.assertEqual(locator.map_ids, ("main", "missing"))
        self.assertFalse(locator._warm_base_resources())
        self.assertFalse(locator._warm_base_resources())
        self.assertIn("missing", locator._resource_errors)
        self.assertFalse(locator._warm_base_resources())


class _ArrayAlpha:
    def __init__(self, pixels):
        self.pixels = pixels
        self.shape = pixels.shape
        self.closed = False

    @property
    def nbytes(self):
        return 0 if self.closed else self.pixels.nbytes

    def __getitem__(self, slices):
        if self.closed:
            raise ValueError("alpha closed")
        return self.pixels[slices]

    def close(self):
        self.closed = True


class _ArrayFloor(_ArrayAlpha):
    def __init__(self, pixels, alpha):
        super().__init__(pixels)
        self.coverage = alpha

    def alpha_image(self):
        return _ArrayAlpha(np.repeat(self.coverage[:, :, None], 3, axis=2))


class TestCompositeFloorImage(unittest.TestCase):
    def setUp(self):
        self.cal = MapCalibration(np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4, 0, 0], [0, -4, 0]]))
        self.floor = _ArrayFloor(np.full((360, 400, 3), 120, np.uint8), np.full((360, 400), 255, np.uint8))
        self.floor.coverage[100:160] = 0
        self.floor.coverage[180:220] = 128
        yy, xx = np.indices((450, 500))
        self.base = np.stack((xx % 200 + 20, yy % 200 + 20, (xx + yy) % 200 + 20), axis=2).astype(np.uint8)

    def test_corrupt_npy_headers_are_rejected_before_array_allocation(self):
        for bad_member in ("pixels", "sha256"):
            with (
                self.subTest(member=bad_member),
                tempfile.TemporaryDirectory() as directory,
                patch.object(composite_overview_cache, "CACHE_ROOT", Path(directory)),
            ):
                pixels = np.zeros((4, 4, 3), np.uint8)
                with zipfile.ZipFile(Path(directory) / "bad.npz", "w") as archive:
                    for name, value in (("pixels", pixels), ("sha256", np.asarray("0" * 64))):
                        stream = io.BytesIO()
                        if name == bad_member:
                            np.lib.format.write_array_header_1_0(
                                stream, {"descr": "|u1", "fortran_order": False, "shape": (2**40,)}
                            )
                        else:
                            np.save(stream, value, allow_pickle=False)
                        archive.writestr(f"{name}.npy", stream.getvalue())
                with patch.object(np.lib.format, "read_array", side_effect=AssertionError("must validate first")):
                    self.assertIsNone(composite_overview_cache.load_overview("bad", pixels.shape))

    def test_batched_resource_stamps_match_stat_and_refresh_changes_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / f"tile-{index}.bin" for index in range(18)]
            for path in paths:
                path.write_bytes(b"original")
            image = SimpleNamespace(
                root=root,
                metadata={"version": 1},
                shape=(1, 1, 3),
                _scale_xy=(1, 1),
                _alpha=False,
                resource_records=lambda: [{"path": path.name} for path in paths],
            )
            expected = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(paths)]
            with patch.object(
                composite_overview_cache.os, "scandir", wraps=composite_overview_cache.os.scandir
            ) as scan:
                original = composite_overview_cache.image_identity(image)
                self.assertEqual(original["files"], expected)
                scan.assert_called_once()
            paths[0].write_bytes(b"changed length")
            changed = composite_overview_cache.image_identity(image)
            self.assertNotEqual(
                composite_overview_cache.cache_key(original), composite_overview_cache.cache_key(changed)
            )
            paths[1].unlink()
            with self.assertRaises(FileNotFoundError):
                composite_overview_cache.image_identity(image)

    def test_resource_stamps_are_shared_within_overview_only_and_refresh_after_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tile.bin"
            path.write_bytes(b"original")
            image = SimpleNamespace(
                root=path.parent,
                metadata={"version": 1},
                shape=(1, 1, 3),
                _scale_xy=(1, 1),
                _alpha=False,
                resource_records=lambda: [{"path": path.name}],
            )
            shared = {}
            with patch.object(composite_overview_cache.os, "stat", wraps=composite_overview_cache.os.stat) as stat:
                original = composite_overview_cache.image_identity(image, file_stamps=shared)
                self.assertEqual(composite_overview_cache.image_identity(image, file_stamps=shared), original)
                self.assertEqual(stat.call_count, 1)
            path.write_bytes(b"changed length")
            changed = composite_overview_cache.image_identity(image)
            self.assertNotEqual(
                composite_overview_cache.cache_key(original), composite_overview_cache.cache_key(changed)
            )
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                composite_overview_cache.image_identity(image)

    def test_proposal_prefers_rendered_background_over_raw_floor_ranking(self):
        rng = np.random.default_rng(2056)
        base = rng.integers(20, 180, (480, 480, 3), np.uint8)
        foreground = rng.integers(120, 210, base.shape, np.uint8)
        alpha = np.zeros(base.shape[:2], np.uint8)
        alpha[228:253, 228:253] = 255
        floor = _ArrayFloor(foreground, alpha)
        images = {
            "right": CompositeFloorImage(floor, self.cal, base, self.cal, padding=0),
            "wrong": CompositeFloorImage(floor, self.cal, np.roll(base, 100, axis=1), self.cal, padding=0),
        }
        for image in images.values():
            self.addCleanup(image.close)
        pixels = images["right"][210:271, 210:271].copy()
        mask = np.ones(pixels.shape[:2], bool)
        template = _PreparedTemplate(pixels.copy(), pixels, mask, mask.astype(np.uint8) * 255)
        self.assertIsNotNone(template.dim_terrain)
        matches = {key: MinimapMapLocator(image, self.cal, scale_override=1) for key, image in images.items()}
        for match in matches.values():
            match._prepare_template = lambda *_: template
        entries = [
            {
                "id": key,
                "path": key,
                "map_name": "map",
                "map_category": "layered",
                "width": 480,
                "height": 480,
                "bounds": {"west": 0, "east": 120, "south": -120, "north": 0},
            }
            for key in images
        ]
        locator = LayeredMinimapLocator(matches["right"], "map", ".", {"layers": entries}, bundle=object())
        locator._load = lambda entry: matches[entry["id"]]
        locator._matching_locator = lambda entry, loaded: loaded
        locator._search_index = SimpleNamespace(rank=lambda *_: [(0.99, "wrong"), (0.5, "right")])
        locator._background_hint = (60, -60)
        ranks = locator.rank_layers(np.zeros((128, 128, 3), np.uint8), None)
        self.assertEqual([tier for _, tier in ranks], ["right", "wrong"])
        self.assertGreater(ranks[0][0], 0.99)
        self.assertIsNone(locator._recent)  # A proposal never establishes a position.

    def test_blocked_blend_preserves_every_alpha_value_and_bounds_float_scratch(self):
        rng = np.random.default_rng(2055)
        foreground = rng.integers(0, 256, (273, 611, 3), np.uint8)
        background = rng.integers(0, 256, foreground.shape, np.uint8)
        alpha = rng.integers(0, 256, (273, 611, 1), np.uint8)
        alpha[0, :256, 0] = np.arange(256, dtype=np.uint8)
        for dim in (0.0, 0.3, 0.07, 1.0):
            weight = alpha.astype(np.float32) / 255
            expected = np.rint(weight * foreground + (1 - weight) * dim * background).astype(np.uint8)
            with patch.object(np, "rint", wraps=np.rint) as rounded:
                actual = _blend_regions(foreground, alpha, background, dim)
            np.testing.assert_array_equal(actual, expected)
            self.assertLessEqual(max(call.args[0].nbytes for call in rounded.call_args_list), 128 * 256 * 3 * 4)

    def test_persisted_coarse_pixels_are_reused_and_render_changes_invalidate_them(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(composite_overview_cache, "CACHE_ROOT", Path(temporary)),
            patch("src.localization.composite_floor_image.image_identity", return_value={"version": "test"}),
        ):
            expected = self.image().coarse_overview(0.35)
            same = self.image()
            with patch.object(same, "_base_region", side_effect=AssertionError("cache missed")):
                np.testing.assert_array_equal(same.coarse_overview(0.35), expected)
            changed = self.image(dim=0.5).coarse_overview(0.35)
            self.assertFalse(np.array_equal(changed, expected))
            self.assertEqual(len(list(Path(temporary).glob("*.npz"))), 2)

    def test_corrupt_derived_overview_is_rebuilt_from_source_pixels(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(composite_overview_cache, "CACHE_ROOT", Path(temporary)),
            patch("src.localization.composite_floor_image.image_identity", return_value={"version": "test"}),
        ):
            expected = self.image().coarse_overview(0.35)
            path = next(Path(temporary).glob("*.npz"))
            with path.open("wb") as stream:
                np.savez(stream, pixels=np.zeros_like(expected), sha256="wrong checksum")
            np.testing.assert_array_equal(self.image().coarse_overview(0.35), expected)
            np.testing.assert_array_equal(composite_overview_cache.load_overview(path.stem, expected.shape), expected)

    def test_derived_overview_storage_is_bounded(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(composite_overview_cache, "CACHE_ROOT", Path(temporary)),
            patch.object(composite_overview_cache, "MAX_BYTES", 18000),
        ):
            pixels = np.zeros((60, 60, 3), np.uint8)
            for i in range(4):
                composite_overview_cache.save_overview(composite_overview_cache.cache_key({"i": i}), pixels)
            self.assertLessEqual(sum(p.stat().st_size for p in Path(temporary).glob("*.npz")), 18000)

    def image(self, **kwargs):
        image = CompositeFloorImage(self.floor, self.cal, self.base, self.cal, padding=10, **kwargs)
        self.addCleanup(image.close)
        return image

    def test_alpha_composition_and_coverage_do_not_use_pixel_brightness(self):
        self.floor.pixels[40:70, 40:70] = 0  # Black but opaque is still current-floor coverage.
        image = self.image()
        self.assertEqual(image[60:61, 60:61][0, 0].tolist(), [0, 0, 0])
        self.assertTrue(image.candidate_mask([60], [60])[0, 0])
        np.testing.assert_array_equal(image[140:141, 60:61][0, 0], np.rint(0.3 * self.base[130, 50]).astype(np.uint8))
        self.assertFalse(image.candidate_mask([60], [140])[0, 0])
        expected = np.rint(128 / 255 * 120 + (1 - 128 / 255) * 0.3 * self.base[190, 50]).astype(np.uint8)
        np.testing.assert_array_equal(image[200:201, 60:61][0, 0], expected)

    def test_base_and_floor_align_by_calibration_not_array_indices(self):
        matrix = self.cal.matrix.copy()
        matrix[:, 2] = [5, -3]
        shifted = MapCalibration(matrix, np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2])
        image = CompositeFloorImage(self.floor, shifted, self.base, self.cal, padding=10)
        self.addCleanup(image.close)
        np.testing.assert_array_equal(image[140:141, 60:61][0, 0], np.rint(0.3 * self.base[142, 70]).astype(np.uint8))
        self.assertEqual(image.calibration.map_to_world(60, 140), shifted.map_to_world(50, 130))

    def test_padded_border_keeps_base_but_cannot_become_floor_candidate(self):
        image = self.image()
        self.assertFalse(image.candidate_mask([3], [80])[0, 0])
        self.assertFalse(image.candidate_mask([-100], [-100])[0, 0])
        self.assertTrue(image.candidate_mask([9], [80])[0, 0])  # Small boundary allowance.
        self.assertFalse(image.candidate_mask([6], [80])[0, 0])

    def test_coarse_blocks_equal_full_resize_across_seams(self):
        image = self.image()
        dense = image[:, :]
        for scale in (0.37, 0.8):
            with self.subTest(scale=scale):
                actual = image.coarse_overview(scale)
                expected = cv2.resize(dense, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                np.testing.assert_allclose(actual, expected, atol=1, rtol=0)
        image.close()
        self.assertEqual(image.nbytes, 0)
        self.assertFalse(self.floor.closed)

    def test_empty_coverage_never_acquires_despite_visible_background(self):
        self.floor.coverage[:] = 0
        image = self.image()
        self.assertFalse(image.candidate_mask(np.arange(0, 420, 3), np.arange(0, 380, 3)).any())
        self.assertTrue(image[100:130, 100:130].any())

    def test_coverage_cache_is_bounded_and_closed_views_are_not_reused(self):
        image = self.image()
        xs, ys = np.arange(20, 100), np.arange(50, 150)
        first = image.candidate_mask(xs, ys)
        self.assertIs(first, image.candidate_mask(xs, ys))
        for offset in range(10):
            image.candidate_mask(xs + offset, ys)
        self.assertLessEqual(len(image._coverage_cache), 4)
        image.close()
        with self.assertRaisesRegex(ValueError, "closed"):
            image.candidate_mask(xs + 9, ys)


class TestLocalizationCache(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "localization"
        self.overviews = self.root / "composite-overviews"
        self.overviews.mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.object(composite_overview_cache, "CACHE_ROOT", self.overviews).start()

    def test_startup_enforces_age_size_and_count_without_touching_unowned_files(self):
        import os
        import time

        from src.localization.localization_cache import maintain_cache

        paths = [self.overviews / (str(i) * 64 + ".npz") for i in range(4)]
        for i, p in enumerate(paths):
            p.write_bytes(b"a" * 100)
            os.utime(p, (time.time() - 50 + i, time.time() - 50 + i))
        os.utime(paths[0], (1, 1))
        orphan = self.overviews / ("." + "a" * 64 + "." + "b" * 32 + ".tmp")
        orphan.write_bytes(b"old")
        os.utime(orphan, (1, 1))
        active = self.overviews / ("." + "c" * 64 + "." + "d" * 32 + ".tmp")
        active.write_bytes(b"writing")
        unrelated = self.overviews / "user-notes.txt"
        unrelated.write_text("keep")
        with (
            patch.object(composite_overview_cache, "MAX_BYTES", 200),
            patch.object(composite_overview_cache, "MAX_ENTRIES", 1),
        ):
            maintain_cache(threading.Event(), root=self.root)
        self.assertEqual(list(self.overviews.glob("*.npz")), [paths[-1]])
        self.assertFalse(orphan.exists())
        self.assertTrue(active.exists())
        self.assertEqual(unrelated.read_text(), "keep")

    def test_successful_read_refreshes_use_time_and_survives_pruning(self):
        import os
        import time

        key = "a" * 64
        pixels = np.arange(300, dtype=np.uint8).reshape(10, 10, 3)
        composite_overview_cache.save_overview(key, pixels)
        path = self.overviews / (key + ".npz")
        os.utime(path, (1, 1))
        np.testing.assert_array_equal(composite_overview_cache.load_overview(key, pixels.shape), pixels)
        self.assertLess(time.time() - path.stat().st_mtime, 5)
        composite_overview_cache.prune_cache()
        self.assertTrue(path.exists())

    def test_cleanup_removes_only_owned_retired_tile_generations_and_layer_arrays(self):
        from src.localization.localization_cache import maintain_cache

        descriptor = self.root / "tiled-images" / ("a" * 64)
        generation = descriptor / ("generation-" + "b" * 32)
        generation.mkdir(parents=True)
        (descriptor / "index.json").write_text("index")
        (generation / "tile-0-0.webp").write_bytes(b"tile")
        notes = generation / "notes.txt"
        notes.write_text("keep")
        layers = self.root / "layer-index"
        layers.mkdir()
        (layers / ("c" * 64 + ".npz")).write_bytes(b"cache")
        maintain_cache(threading.Event(), root=self.root)
        self.assertFalse((descriptor / "index.json").exists())
        self.assertFalse((generation / "tile-0-0.webp").exists())
        self.assertFalse((layers / ("c" * 64 + ".npz")).exists())
        self.assertEqual(notes.read_text(), "keep")

    def test_directory_junctions_cannot_escape_cleanup_or_cache_writes(self):
        import os

        from src.localization.localization_cache import maintain_cache

        outside = self.root.parent / "outside"
        outside.mkdir()
        sentinel = outside / ("a" * 64 + ".npz")
        sentinel.write_bytes(b"keep")
        link = self.root / "layer-index"
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.addCleanup(link.rmdir if os.name == "nt" else link.unlink)
        maintain_cache(threading.Event(), root=self.root)
        with patch.object(composite_overview_cache, "CACHE_ROOT", link):
            composite_overview_cache.save_overview("b" * 64, np.zeros((10, 10, 3), np.uint8))
        self.assertEqual(sentinel.read_bytes(), b"keep")
        self.assertFalse((outside / ("b" * 64 + ".npz")).exists())

    def test_cancelled_cleanup_keeps_cache_and_permission_failure_does_not_remove_other_files(self):
        from src.localization.localization_cache import maintain_cache

        stop = threading.Event()
        stop.set()
        path = self.overviews / ("a" * 64 + ".npz")
        path.write_bytes(b"cache")
        self.assertEqual(maintain_cache(stop, root=self.root), 0)
        self.assertTrue(path.exists())
        with (
            patch.object(Path, "unlink", side_effect=PermissionError),
            patch.object(composite_overview_cache, "MAX_BYTES", 0),
        ):
            maintain_cache(threading.Event(), root=self.root)
        self.assertTrue(path.exists())

    def test_invalid_keys_and_failed_publication_leave_no_partial_cache(self):
        pixels = np.zeros((10, 10, 3), np.uint8)
        composite_overview_cache.save_overview("../escape", pixels)
        self.assertIsNone(composite_overview_cache.load_overview("../escape", pixels.shape))
        with patch.object(np, "savez", side_effect=OSError("disk full")):
            composite_overview_cache.save_overview("a" * 64, pixels)
        self.assertEqual(list(self.overviews.iterdir()), [])

    def test_unremovable_old_entry_prevents_adding_cache_over_budget(self):
        old = self.overviews / ("a" * 64 + ".npz")
        old.write_bytes(b"x" * 4900)
        unlink = Path.unlink

        def remove(path, *args, **kwargs):
            if path == old:
                raise PermissionError("cache is in use")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", remove), patch.object(composite_overview_cache, "MAX_BYTES", 5000):
            composite_overview_cache.save_overview("b" * 64, np.zeros((10, 10, 3), np.uint8))
        self.assertEqual(list(self.overviews.iterdir()), [old])

    def test_unknown_zip_compression_is_a_cache_miss(self):
        key = "a" * 64
        pixels = np.zeros((10, 10, 3), np.uint8)
        composite_overview_cache.save_overview(key, pixels)
        path = self.overviews / (key + ".npz")
        data = bytearray(path.read_bytes())
        for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
            position = data.index(signature) + offset
            data[position : position + 2] = b"\x63\x00"
        path.write_bytes(data)
        self.assertIsNone(composite_overview_cache.load_overview(key, pixels.shape))

    def test_v3_loader_rejects_retired_versions_and_windows_stream_paths(self):
        from src.localization.localization_bundle import resolve_bundle_path

        for version in (1, 2, True):
            with self.subTest(version=version), self.assertRaises(ValueError):
                LocalizationTileBundle(self.root, {"schema_version": version})
        with self.assertRaises(ValueError):
            resolve_bundle_path(self.root, "maps/file:stream")
