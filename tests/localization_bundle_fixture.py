import hashlib
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np

from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.minimap_map_locator import COARSE_FACTOR, minimap_map_scale


class LocalizationBundleFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package_root = Path(temporary.name) / "navigation"
        self.root = self.package_root / "localization"
        self.root.mkdir(parents=True)
        # Most low-level fixtures exercise the historical on-demand decoder. The
        # production map store is covered separately with its strict default.
        self.store = CalibratedMapStore(self.root / "maps", require_prepared=False)
        self.files = {}
        self.pixels = np.random.default_rng(240).integers(20, 235, (129, 131, 4), dtype=np.uint8)
        self.pixels[:, :, 3] = 255
        self.pixels[62:67, :, 3] = 0
        self.pixels[:, 61:65, 3] = 64
        map_id = getattr(self, "bundle_map_id", "main")
        self.base = {
            "map_id": map_id,
            "zoom": "4",
            "width": 131,
            "height": 129,
            "image": f"maps/{map_id}/4/tiles.json",
            "mapping": f"maps/{map_id}/4/mapping.json",
        }
        self.floor = {
            "id": "floor",
            "map_name": map_id,
            "level_id": "level",
            "map_category": "layered",
            "path": "floor/tiles.json",
            "filename": "tiles.json",
            "width": 131,
            "height": 129,
        }
        self.manifest = {
            "schema_version": 3,
            "tile_layout": "zero-padded-roi",
            "kind": "okef-tiled-localization",
            "maps_root": "maps",
            "layers_root": "layers",
            "layer_catalog": "layers/catalog.json",
            "base_maps": [self.base],
            "coverage": {"layer_count": 1, "independent_layer_count": 0, "missing_logic_ids": []},
            "raster_convention": {
                "origin": "top-left",
                "pixel_coordinates": "integer-centers",
                "columns": "right",
                "rows": "down",
                "index_base": 0,
            },
            "search": {
                "screen_widths": [1280, 1920, 2560],
                "scale_constant": 1706.5,
                "match_pixels_per_meter": 4,
                "floor_rank_pixels_per_meter": 0.5,
                "coarse_factor": 0.5,
                "interpolation": "OpenCV INTER_AREA",
                "image_layout": "zero-padded-roi",
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
            {"id": map_id, "map_id": map_id, "zoom": "4", "mapping": self.base["mapping"]},
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

        def color(image):
            rgb = image[:, :, :3].copy()
            if image.shape[2] == 4:
                rgb[image[:, :, 3] == 0] = 0
            return rgb

        tiles = []
        for y in range(0, height, 64):
            for x in range(0, width, 64):
                tile = pixels[y : y + 64, x : x + 64]
                record = self.write_image(f"{directory}/{x}-{y}.webp", tile)
                bgr = color(tile)
                left, top, width_roi, height_roi = cv2.boundingRect(bgr[:, :, 0] | bgr[:, :, 1] | bgr[:, :, 2])
                roi = [left, top, left + width_roi, top + height_roi]
                tiles.append({**record, "column": x // 64, "row": y // 64, "storage": "full", "roi": roi})

        match = cv2.resize(color(pixels), None, fx=scale[0], fy=scale[1], interpolation=cv2.INTER_AREA)
        rank = None if rank_size is None else color(cv2.resize(pixels, rank_size, interpolation=cv2.INTER_AREA))
        coarse = []
        for screen_width in self.manifest["search"]["screen_widths"]:
            factor = minimap_map_scale(screen_width) * COARSE_FACTOR
            reduced = cv2.resize(match, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            coarse.append(
                {
                    **self.write_search(f"{directory}/{screen_width}.webp", reduced),
                    "screen_width": screen_width,
                    "scale": factor,
                    "scale_hex": factor.hex(),
                }
            )
        index = {
            "schema_version": 3,
            "kind": "okef-image-tiles",
            "complete": True,
            "tile_size": 64,
            "width": width,
            "height": height,
            **identity,
            "tiles": tiles,
            "coarse": coarse,
            "match_raster": {"width": match.shape[1], "height": match.shape[0], "source_scale": list(scale)},
            "floor_rank": None if rank is None else self.write_search(f"{directory}/rank.webp", rank),
        }
        self.write_json(name, index)
        return index, match, rank

    def write_floor(self, scale):
        map_id = self.base["map_id"]
        bounds = {"west": 0, "east": 131 * scale[0] / 4, "south": 0, "north": 129 * scale[1] / 4}
        self.floor["bounds"] = bounds
        # Match the producer's operation order even for non-terminating ratios.
        scale = (4 * (bounds["east"] / 131), 4 * (bounds["north"] / 129))
        rank_size = (round(bounds["east"] * 0.5), round(bounds["north"] * 0.5))
        identity = {
            "id": "floor",
            "map_id": map_id,
            "zoom": "4",
            "bounds": bounds,
            "level_id": "level",
            "map_category": "layered",
        }
        self.floor_index, self.floor_match, self.floor_rank = self.write_index(
            "layers/floor/tiles.json", self.pixels, identity, scale, rank_size
        )
        self.write_json("layers/catalog.json", {"schema_version": 1, "layers": [self.floor]})
        self.write_indexes()

    def write_manifest(self):
        (self.root / "manifest.json").write_text(
            json.dumps({**self.manifest, "files": list(self.files.values())}), encoding="utf-8"
        )

    def write_indexes(self):
        self.write_json(self.base["image"], self.base_index)
        self.write_json("layers/floor/tiles.json", self.floor_index)
        self.write_manifest()

    def write_search(self, name, pixels):
        record = self.write_image(name, pixels)
        return {**record, "roi": [0, 0, record["width"], record["height"]], "fill": [0, 0, 0]}

    def write_prepared(self, *, corrupt_rank=False):
        """Write a minimal prepared package around the fixture's one floor."""
        root = self.root
        prepared = root / "prepared"
        prepared.mkdir()
        dataset = {
            "schema_version": 1,
            "kind": "okef-nav-dataset",
            "release_id": "fixture-release",
        }
        dataset_path = self.package_root / "dataset.json"
        dataset_data = json.dumps(dataset).encode()
        dataset_path.write_bytes(dataset_data)

        def write(name, data):
            path = prepared / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        def encode_webp(pixels):
            ok, data = cv2.imencode(".webp", pixels, [cv2.IMWRITE_WEBP_QUALITY, 90])
            self.assertTrue(ok)
            return data.tobytes()

        full_height = self.floor["height"] + 640
        full_width = self.floor["width"] + 640
        prepared_scale = 1280 / (4 * 1706.5) * 0.5
        region_height = round(full_height * prepared_scale)
        region_width = round(full_width * prepared_scale)
        atlas = np.zeros((320, 320, 3), np.uint8)
        atlas[128 : 128 + region_height, 128 : 128 + region_width] = cv2.resize(
            self.pixels[: max(region_height * 2, 1), : max(region_width * 2, 1), :3],
            (region_width, region_height),
            interpolation=cv2.INTER_AREA,
        )
        decoded = cv2.imdecode(np.frombuffer(encode_webp(atlas), np.uint8), cv2.IMREAD_COLOR)
        floor_mask = np.ones((region_height, region_width), bool)
        floor_mask[:4, :4] = False
        floor_mask_bytes = np.packbits(floor_mask.reshape(-1), bitorder="big").tobytes()
        atlas_mask = np.zeros((320, 320), bool)
        atlas_mask[128 : 128 + region_height, 128 : 128 + region_width] = floor_mask
        atlas_mask_bytes = np.packbits(atlas_mask.reshape(-1), bitorder="big").tobytes()
        floor_mask_record = write("masks/main/floor.bin", floor_mask_bytes)
        rank_shape = (
            max(1, round((self.floor["bounds"]["north"] - self.floor["bounds"]["south"]) * 0.5)),
            max(1, round((self.floor["bounds"]["east"] - self.floor["bounds"]["west"]) * 0.5)),
        )
        rank_record = self.floor_index["floor_rank"]
        rank_path = rank_record["path"]
        self.assertEqual([rank_record["height"], rank_record["width"]], list(rank_shape))
        if corrupt_rank:
            (root / rank_path).write_bytes(b"corrupt rank")
        atlas_record = write("atlases/main-1280.webp", encode_webp(atlas))
        atlas_mask_record = write("atlases/main-1280-mask.bin", atlas_mask_bytes)
        region = {"x": 128, "y": 128, "width": region_width, "height": region_height}
        floor = {
            "tier_id": self.floor["id"],
            "map_id": self.base["map_id"],
            "level_id": self.floor["level_id"],
            "map_category": self.floor["map_category"],
            "bounds": self.floor["bounds"],
            "atlas_region": region,
            "rank_path": f"localization/{rank_path}",
            "rank_size": rank_record["size"],
            "rank_sha256": rank_record["sha256"],
            "rank_shape": list(rank_shape),
            "rank_roi": [0, 0, rank_shape[1], rank_shape[0]],
            "mask_path": floor_mask_record["path"],
            "mask_shape": list(floor_mask.shape),
            "mask_bytes": len(floor_mask_bytes),
            "mask_sha256": floor_mask_record["sha256"],
            "mask_bit_order": "big",
            "mask_row_major": True,
            "coverage_pixels": int(floor_mask.sum()),
        }
        floor_index = {
            "schema_version": 1,
            "input_width": 1280,
            "maps": {self.base["map_id"]: [floor]},
        }
        floor_index_data = json.dumps(floor_index).encode()
        floor_index_record = write("floor-index.json", floor_index_data)
        records = sorted(
            (
                atlas_record,
                atlas_mask_record,
                floor_mask_record,
                floor_index_record,
            ),
            key=lambda record: record["path"],
        )
        manifest = {
            "kind": "okef-prepared-localization",
            "schema_version": 1,
            "complete": True,
            "source": {
                "release_id": dataset["release_id"],
                "localization_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
                "input_width": 1280,
                "coarse_factor": 0.5,
                "composite_dim": 0.3,
                "composite_padding": 320,
                "webp_quality": 90,
            },
            "coverage": {
                "layered_floors": 1,
                "maps": {self.base["map_id"]: 1},
                "missing_logic_ids": [],
                "mask_encoding": "row-major-msb-first-packbits-zero-tail",
            },
            "atlases": {
                self.base["map_id"]: {
                    "path": atlas_record["path"],
                    "mask_path": atlas_mask_record["path"],
                    "shape": list(atlas.shape[:2]),
                    "padding": 128,
                    "mask_bytes": len(atlas_mask_bytes),
                    "mask_bit_order": "big",
                    "mask_row_major": True,
                    "decoded_sha256": hashlib.sha256(decoded.tobytes()).hexdigest(),
                    "regions": {self.floor["id"]: region},
                }
            },
            "floors": [floor],
            "files": records,
        }
        manifest_data = json.dumps(manifest).encode()
        (prepared / "manifest.json").write_bytes(manifest_data)
        (prepared / "validation.json").write_text(
            json.dumps({"layered_floors": 1}),
            encoding="utf-8",
        )
        (prepared / "READY").write_text(
            "okef-prepared-localization 1 " + hashlib.sha256(manifest_data).hexdigest() + "\n",
            encoding="ascii",
        )
        package = {
            "kind": "okef-prepared-nav-package",
            "schema_version": 1,
            "complete": True,
            "release_id": dataset["release_id"],
            "dataset_sha256": hashlib.sha256(dataset_data).hexdigest(),
            "localization_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
            "prepared_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        }
        (self.package_root / "prepared-package.json").write_text(json.dumps(package), encoding="utf-8")
        return prepared, manifest, floor
