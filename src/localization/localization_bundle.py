"""Validate and open the publisher's standalone localization raster package."""

import hashlib
import json
import re
from pathlib import PurePosixPath, PureWindowsPath

from src.localization.minimap_map_locator import COARSE_FACTOR, DEFAULT_SCALE_CONSTANT, minimap_map_scale
from src.localization.native_tile_image import NativeTileImage
from src.localization.tiled_map_image import floor_options
from src.localization.visual_frame import VISUAL_FRAME_WIDTH

TILED_KIND = "okef-tiled-localization"
TILED_SCHEMA_VERSION = 3
RASTER_CONVENTION = {
    "origin": "top-left",
    "pixel_coordinates": "integer-centers",
    "columns": "right",
    "rows": "down",
    "index_base": 0,
}


def resolve_bundle_path(root, name):
    """Resolve a manifest path confined to its bundle."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError(f"Invalid localization resource path: {name!r}")
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise ValueError(f"Invalid localization resource path: {name!r}")
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Localization resource escapes bundle: {name}")
    return path


class LocalizationTileBundle:
    def __init__(self, root, manifest):
        self.root = root
        self.version = manifest.get("schema_version")
        if type(self.version) is not int or self.version != TILED_SCHEMA_VERSION:
            raise ValueError("Unsupported localization tile bundle version")
        search = manifest.get("search")
        if manifest.get("raster_convention") != RASTER_CONVENTION or not isinstance(search, dict):
            raise ValueError("Unsupported localization tile coordinates")
        # These are serialized format identifiers, not measured floats. Compare
        # the exact contract; tolerances could accept an incompatible raster.
        expected_search = {
            "scale_constant": DEFAULT_SCALE_CONSTANT,
            "match_pixels_per_meter": 4,
            "floor_rank_pixels_per_meter": 0.5,
            "coarse_factor": COARSE_FACTOR,
            "interpolation": "OpenCV INTER_AREA",
        }
        if {key: search.get(key) for key in expected_search} != expected_search or (
            search.get("image_layout") != "zero-padded-roi"
        ):
            raise ValueError("Unsupported localization tile matching scales")
        if manifest.get("tile_layout") != "zero-padded-roi":
            raise ValueError("Unsupported native localization tile layout")
        widths = search.get("screen_widths")
        if not isinstance(widths, list) or not widths or any(type(w) is not int or w <= 0 for w in widths):
            raise ValueError("Invalid localization tile screenshot widths")
        self.scales = {w: minimap_map_scale(w) * COARSE_FACTOR for w in widths}
        if len(self.scales) != len(widths):
            raise ValueError("Duplicate localization tile screenshot width")
        if VISUAL_FRAME_WIDTH not in widths:
            raise ValueError("Missing normalized localization screenshot width")
        self.files = {}
        records = manifest.get("files")
        if not isinstance(records, list):
            raise ValueError("Missing localization tile resource list")
        for entry in records:
            if not isinstance(entry, dict):
                raise ValueError("Invalid localization tile resource record")
            name, size, digest = entry.get("path"), entry.get("size"), entry.get("sha256")
            if (
                not isinstance(name, str)
                or name in self.files
                or type(size) is not int
                or size <= 0
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError(f"Invalid or duplicate localization tile resource: {name!r}")
            self.files[name] = (size, digest)

    def read_json(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Localization tile index escapes resource root")
        name = path.relative_to(self.root).as_posix()
        record = self.files.get(name)
        if record is None:
            raise ValueError(f"Unlisted localization tile resource: {name}")
        size, digest = record
        with path.open("rb") as stream:
            data = stream.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Corrupt localization tile resource: {name}")
        return json.loads(data.decode("utf-8"))

    def _image_record(self, record, width, height):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("Invalid localization image resource")
        name = record["path"]
        if (
            self.files.get(name) != (record.get("size"), record.get("sha256"))
            or any(type(record.get(axis)) is not int or record[axis] <= 0 for axis in ("width", "height"))
            or (record.get("width"), record.get("height")) != (width, height)
        ):
            raise ValueError(f"Localization image record disagrees with manifest or geometry: {name}")
        return {"path": name, "size": record["size"], "sha256": record["sha256"], "shape": [height, width]}

    @staticmethod
    def _read_roi(record, width, height):
        roi = record.get("roi")
        if not isinstance(roi, list) or len(roi) != 4 or any(type(n) is not int for n in roi):
            raise ValueError("Invalid localization raster ROI")
        left, top, right, bottom = roi
        if roi != [0, 0, 0, 0] and not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("Localization ROI exceeds its logical raster")
        return (left, top, right, bottom)

    @staticmethod
    def _empty_payload(record):
        return (
            {"path", "size", "sha256"}.issubset(record)
            and record["path"] is None
            and type(record["size"]) is int
            and record["size"] == 0
            and record["sha256"] is None
        )

    def _crop_payload(self, record, roi):
        left, top, right, bottom = roi
        width, height = right - left, bottom - top
        return self._image_record({**record, "width": width, "height": height}, width, height)

    def _search_record(self, record, width, height):
        if (
            not isinstance(record, dict)
            or any(type(record.get(axis)) is not int or record[axis] <= 0 for axis in ("width", "height"))
            or (record.get("width"), record.get("height")) != (width, height)
            or record.get("fill") != [0, 0, 0]
        ):
            raise ValueError("Invalid zero-padded localization search image")
        roi = self._read_roi(record, width, height)
        if roi == (0, 0, 0, 0):
            if not self._empty_payload(record):
                raise ValueError("Invalid empty localization search image")
            crop = None
        else:
            crop = self._crop_payload(record, roi)
        return {"shape": [height, width], "roi": roi, "crop": crop}

    def _native_record(self, record, width, height):
        if any(type(record.get(axis)) is not int for axis in ("width", "height")) or (
            record.get("width"),
            record.get("height"),
        ) != (width, height):
            raise ValueError("Invalid logical native tile size")
        roi = self._read_roi(record, width, height)
        storage = record.get("storage")
        if storage == "zero":
            if roi != (0, 0, 0, 0) or not self._empty_payload(record):
                raise ValueError("Invalid explicitly zero native tile")
            return None
        if roi == (0, 0, 0, 0) and storage != "full":
            raise ValueError("Nonzero native tile must declare a nonempty ROI")
        if storage == "roi":
            payload, origin = self._crop_payload(record, roi), roi[:2]
            payload["require_bgr"] = True
        elif storage == "full":
            payload, origin = self._image_record(record, width, height), (0, 0)
        else:
            raise ValueError("Unknown native localization tile storage")
        return {"roi": roi, "payload": payload, "origin": origin}

    def _read_index(self, path, identity, size):
        index = self.read_json(path)
        expected = {
            "schema_version": self.version,
            "kind": "okef-image-tiles",
            "complete": True,
            "width": size[0],
            "height": size[1],
            **identity,
        }
        if (
            not isinstance(index, dict)
            or index.get("complete") is not True
            or any(type(index.get(key)) is not int for key in ("schema_version", "width", "height"))
            or any(index.get(key) != value for key, value in expected.items())
        ):
            raise ValueError(f"Invalid localization image tile index: {path}")
        return index

    @staticmethod
    def _floor_identity(entry):
        return {
            "id": entry["id"],
            "map_id": entry["map_name"],
            "zoom": "4",
            "bounds": entry["bounds"],
            "level_id": entry.get("level_id"),
            "map_category": entry["map_category"],
        }

    def open_base(self, path, map_id, size, mapping):
        identity = {"id": map_id, "map_id": map_id, "zoom": "4", "mapping": mapping.relative_to(self.root).as_posix()}
        index = self._read_index(path, identity, size)
        return self._open_image(index, size, (1.0, 1.0), proposal_size=None)

    def open_floor(self, root, entry):
        size = (entry["width"], entry["height"])
        index = self._read_index(resolve_bundle_path(root, entry["path"]), self._floor_identity(entry), size)
        options = floor_options(entry)
        return self._open_image(index, size, options["resize"], proposal_size=options["proposal_size"])

    def _open_image(self, index, size, scale, *, proposal_size):
        width, height = size
        edge = index.get("tile_size")
        if type(edge) is not int or not 32 <= edge <= 2048:
            raise ValueError("Invalid localization tile size")
        tiles = {}
        rows = index.get("tiles")
        if not isinstance(rows, list) or len(rows) != ((width + edge - 1) // edge) * ((height + edge - 1) // edge):
            raise ValueError("Incomplete localization tile coverage")
        for record in rows:
            if not isinstance(record, dict):
                raise ValueError("Invalid localization tile")
            x, y = record.get("column"), record.get("row")
            if type(x) is not int or type(y) is not int or not (0 <= x * edge < width and 0 <= y * edge < height):
                raise ValueError("Invalid localization tile coordinates")
            key = f"{x},{y}"
            if key in tiles:
                raise ValueError("Duplicate localization tile coordinates")
            tiles[key] = self._native_record(record, min(edge, width - x * edge), min(edge, height - y * edge))
        match_width, match_height = round(width * scale[0]), round(height * scale[1])
        if index.get("match_raster") != {"width": match_width, "height": match_height, "source_scale": list(scale)}:
            raise ValueError("Localization tile match raster disagrees with calibration")
        overviews = {}
        coarse = index.get("coarse")
        if not isinstance(coarse, list):
            raise ValueError("Missing global localization search images")
        for record in coarse:
            screen_width = record.get("screen_width") if isinstance(record, dict) else None
            factor = self.scales.get(screen_width) if type(screen_width) is int else None
            if (
                factor is None
                or record.get("scale") != factor
                or record.get("scale_hex") != factor.hex()
                or factor.hex() in overviews
            ):
                raise ValueError("Invalid global localization search scale")
            overviews[factor.hex()] = self._search_record(
                record, round(match_width * factor), round(match_height * factor)
            )
        if len(overviews) != len(self.scales):
            raise ValueError("Incomplete global localization search images")
        proposal = index.get("floor_rank")
        if proposal_size is not None:
            proposal = self._search_record(proposal, *proposal_size)
        elif proposal is not None:
            raise ValueError("Base map must not have a floor ranking image")
        return NativeTileImage(
            self.root,
            {
                "shape": [height, width],
                "tile_size": edge,
                "tiles": tiles,
                "overviews": overviews,
                "proposal": proposal,
            },
            scale=scale,
            match_size=(match_width, match_height),
            alpha=proposal_size is not None,
        )
