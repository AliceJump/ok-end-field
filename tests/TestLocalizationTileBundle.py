import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.minimap_map_locator import COARSE_FACTOR, minimap_map_scale
from src.localization.tile_preparation import prepare_bundle


class TestLocalizationTileBundle(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = CalibratedMapStore(self.root / "maps")
        self.files = {}
        self.pixels = np.random.default_rng(240).integers(20, 235, (129, 131, 4), dtype=np.uint8)
        self.pixels[:, :, 3] = 255
        self.pixels[62:67, :, 3] = 0
        self.pixels[:, 61:65, 3] = 64
        self.base = {
            "map_id": "main",
            "zoom": "4",
            "width": 131,
            "height": 129,
            "image": "maps/main/4/tiles.json",
            "mapping": "maps/main/4/mapping.json",
        }
        self.floor = {
            "id": "floor",
            "map_name": "main",
            "level_id": "level",
            "map_category": "layered",
            "path": "floor/tiles.json",
            "width": 131,
            "height": 129,
        }
        self.manifest = {
            "schema_version": 1,
            "kind": "okef-tiled-localization",
            "maps_root": "maps",
            "layers_root": "layers",
            "layer_catalog": "layers/catalog.json",
            "base_maps": [self.base],
            "raster_convention": {
                "origin": "top-left",
                "pixel_coordinates": "integer-centers",
                "columns": "right",
                "rows": "down",
                "index_base": 0,
            },
            "search": {
                "screen_widths": [1920, 2560],
                "scale_constant": 1706.5,
                "match_pixels_per_meter": 4,
                "floor_rank_pixels_per_meter": 0.5,
                "coarse_factor": 0.5,
                "interpolation": "OpenCV INTER_AREA",
            },
        }
        self.write_json(
            self.base["mapping"],
            {
                "image_size": {"width": 131, "height": 129},
                "matrix": [[0.25, 0, -16], [0, -0.25, 64]],
            },
        )
        self.base_index, self.base_match, _ = self.write_index(
            self.base["image"],
            self.pixels[:, :, :3],
            {"id": "main", "map_id": "main", "zoom": "4", "mapping": self.base["mapping"]},
        )
        self.write_floor((0.8, 0.8))

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        record = {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.files[name] = record
        return record

    def write_json(self, name, data):
        return self.write(name, json.dumps(data).encode())

    def write_image(self, name, image):
        ok, data = cv2.imencode(".webp", image, [cv2.IMWRITE_WEBP_QUALITY, 101])
        self.assertTrue(ok)
        return {**self.write(name, data.tobytes()), "width": image.shape[1], "height": image.shape[0]}

    def write_index(self, name, pixels, identity, scale=(1.0, 1.0), rank_size=None):
        height, width = pixels.shape[:2]
        directory = name.removesuffix("/tiles.json")
        tiles = []
        for y in range(0, height, 64):
            for x in range(0, width, 64):
                record = self.write_image(f"{directory}/{x}-{y}.webp", pixels[y : y + 64, x : x + 64])
                tiles.append({**record, "column": x // 64, "row": y // 64})

        def color(image):
            rgb = image[:, :, :3].copy()
            if image.shape[2] == 4:
                rgb[image[:, :, 3] == 0] = 0
            return rgb

        match = cv2.resize(color(pixels), None, fx=scale[0], fy=scale[1], interpolation=cv2.INTER_AREA)
        rank = None if rank_size is None else color(cv2.resize(pixels, rank_size, interpolation=cv2.INTER_AREA))
        coarse = []
        for screen_width in self.manifest["search"]["screen_widths"]:
            factor = minimap_map_scale(screen_width) * COARSE_FACTOR
            reduced = cv2.resize(match, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            coarse.append(
                {
                    **self.write_image(f"{directory}/{screen_width}.webp", reduced),
                    "screen_width": screen_width,
                    "scale": factor,
                    "scale_hex": factor.hex(),
                }
            )
        index = {
            "schema_version": 1,
            "kind": "okef-image-tiles",
            "complete": True,
            "tile_size": 64,
            "width": width,
            "height": height,
            **identity,
            "tiles": tiles,
            "coarse": coarse,
            "match_raster": {"width": match.shape[1], "height": match.shape[0], "source_scale": list(scale)},
            "floor_rank": None if rank is None else self.write_image(f"{directory}/rank.webp", rank),
        }
        self.write_json(name, index)
        return index, match, rank

    def write_floor(self, scale):
        bounds = {"west": 0, "east": 131 * scale[0] / 4, "south": 0, "north": 129 * scale[1] / 4}
        self.floor["bounds"] = bounds
        # Match the producer's operation order even for non-terminating ratios.
        scale = (4 * (bounds["east"] / 131), 4 * (bounds["north"] / 129))
        rank_size = (round(bounds["east"] * 0.5), round(bounds["north"] * 0.5))
        identity = {
            "id": "floor",
            "map_id": "main",
            "zoom": "4",
            "bounds": bounds,
            "level_id": "level",
            "map_category": "layered",
        }
        self.floor_index, self.floor_match, self.floor_rank = self.write_index(
            "layers/floor/tiles.json", self.pixels, identity, scale, rank_size
        )
        self.write_json("layers/catalog.json", {"schema_version": 1, "layers": [self.floor]})
        self.write_manifest()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(
            json.dumps({**self.manifest, "files": list(self.files.values())}), encoding="utf-8"
        )

    def test_standalone_search_resources_skip_old_images_and_caches(self):
        with (
            patch("src.localization.tiled_map_image.TiledMapImage.open_prepared", side_effect=AssertionError),
            patch("src.localization.layer_search_index.np.load", side_effect=AssertionError),
            patch("src.localization.tile_preparation.prepare_image", side_effect=AssertionError),
            patch("cv2.imread", side_effect=AssertionError),
        ):
            self.assertEqual(list(prepare_bundle(self.root)), [])
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
