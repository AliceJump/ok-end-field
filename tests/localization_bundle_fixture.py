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
        self.root = Path(temporary.name)
        self.store = CalibratedMapStore(self.root / "maps")
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
                "screen_widths": [1920, 2560],
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
        tiles = []
        for y in range(0, height, 64):
            for x in range(0, width, 64):
                record = self.write_image(f"{directory}/{x}-{y}.webp", pixels[y : y + 64, x : x + 64])
                roi = [0, 0, record["width"], record["height"]]
                if pixels.shape[2] == 4:
                    left, top, width_roi, height_roi = cv2.boundingRect(pixels[y : y + 64, x : x + 64, 3].copy())
                    roi = [left, top, left + width_roi, top + height_roi]
                tiles.append({**record, "column": x // 64, "row": y // 64, "storage": "full", "roi": roi})

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
