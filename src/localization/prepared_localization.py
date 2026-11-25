"""Read-only loader for the publisher's prepared localization bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import cv2
import numpy as np

from src.core.NavConfig import DEFAULT_SCALE_CONSTANT
from src.localization.localization_bundle import (
    LocalizationTileBundle,
    resolve_bundle_path,
    validate_bundle_relative_path,
)
from src.localization.minimap_map_locator import (
    COARSE_FACTOR,
    MAP_PIXELS_PER_METER,
    MapCalibration,
)
from src.localization.tiled_map_image import TiledMapImage

PREPARED_DIRNAME = "prepared"
PREPARED_KIND = "okef-prepared-localization"
PREPARED_VERSION = 1
PACKAGE_MARKER = "prepared-package.json"
PACKAGE_KIND = "okef-prepared-nav-package"
PACKAGE_VERSION = 1
PREPARED_MISSING_MESSAGE = (
    "prepared_localization_missing: 导航定位资源不完整；请重新下载并完整解压 nav-grid-data 的 navigation/ 目录"
)
INPUT_WIDTH = 1280
COMPOSITE_DIM = 0.3
COMPOSITE_PADDING = 320
WEBP_QUALITY = 90
MASK_ENCODING = "row-major-msb-first-packbits-zero-tail"
RANK_PIXELS_PER_METER = 0.5
PREPARED_SCALE = INPUT_WIDTH / (MAP_PIXELS_PER_METER * DEFAULT_SCALE_CONSTANT) * COARSE_FACTOR
MAX_ATLAS_CACHE_BYTES = 128 * 1024**2


def _sha256(data) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_json_bytes(path: Path):
    """Parse and digest the same bytes so a rewrite between reads cannot mix versions."""
    data = path.read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _finite_pair_shape(value):
    if not isinstance(value, list) or len(value) != 2 or any(not _positive_int(item) for item in value):
        return None
    return int(value[0]), int(value[1])


def resolve_prepared_path(root: Path, name) -> Path:
    """Resolve a prepared-bundle path while rejecting escapes and aliases."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError(f"Invalid prepared localization path: {name!r}")
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise ValueError(f"Invalid prepared localization path: {name!r}")
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Prepared localization path escapes bundle: {name}")
    return path


def find_prepared_root(localization_manifest: Path) -> Path | None:
    """Find the prepared runtime resources inside the localization bundle."""
    manifest = Path(localization_manifest).resolve()
    candidate = manifest.parent / PREPARED_DIRNAME
    return candidate if candidate.is_dir() else None


def _decode_image(path):
    """Decode an image from bytes so non-ASCII Windows paths still work.

    cv2.imread() passes the path to a narrow-char fopen on Windows and can fail
    on paths containing CJK characters even though Python can read the file.
    """
    return cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)


def floor_rank_shape(entry: dict) -> tuple[int, int]:
    bounds = entry["bounds"]
    return (
        max(1, round((float(bounds["north"]) - float(bounds["south"])) * RANK_PIXELS_PER_METER)),
        max(1, round((float(bounds["east"]) - float(bounds["west"])) * RANK_PIXELS_PER_METER)),
    )


def _floor_matrix(entry: dict) -> np.ndarray:
    bounds = entry["bounds"]
    west, east = float(bounds["west"]), float(bounds["east"])
    south, north = float(bounds["south"]), float(bounds["north"])
    width, height = int(entry["width"]), int(entry["height"])
    sx, sy = (east - west) / width, (north - south) / height
    return np.array(
        [
            [MAP_PIXELS_PER_METER**-1, 0, west + 0.125 - sx / 2],
            [0, -(MAP_PIXELS_PER_METER**-1), north - 0.125 + sy / 2],
        ],
        dtype=np.float64,
    )


def _prepared_calibration(entry: dict) -> MapCalibration:
    matrix = _floor_matrix(entry)
    matrix[:, 2] -= matrix[:, :2] @ np.array([COMPOSITE_PADDING, COMPOSITE_PADDING], dtype=np.float64)
    inverse = np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2]
    return MapCalibration(matrix, inverse)


class PreparedAtlasFloorImage(TiledMapImage):
    """A full-size floor view backed by one coarse atlas region."""

    def __init__(
        self,
        atlas: np.ndarray,
        region: dict,
        mask: np.ndarray,
        calibration: MapCalibration,
        *,
        full_shape: tuple[int, int],
        on_close=None,
    ):
        self._atlas = atlas
        self._region = dict(region)
        self._mask = mask
        self.calibration = calibration
        self._coarse_shape = (region["height"], region["width"], 3)
        self._shape = (full_shape[0], full_shape[1], 3)
        self._closed = False
        self._lock = threading.Lock()
        self._on_close = on_close

    @property
    def shape(self):
        return self._shape

    @property
    def nbytes(self):
        return self._coarse_shape[0] * self._coarse_shape[1] * self._coarse_shape[2] + self._mask.nbytes

    @property
    def resident_bytes(self):
        return self.nbytes

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._atlas = None
            callback, self._on_close = self._on_close, None
        if callback is not None:
            callback()

    def overview_memory_bytes(self, scale):
        return round(self._shape[0] * scale) * round(self._shape[1] * scale) * 4

    def coarse_overview(self, scale):
        if not math.isclose(float(scale), PREPARED_SCALE, rel_tol=0, abs_tol=1e-15):
            raise ValueError(f"prepared atlas has no coarse overview at scale {scale!r}")
        with self._lock:
            atlas = self._atlas
            if self._closed or atlas is None:
                raise ValueError("prepared atlas image closed")
        region = self._region
        result = np.array(
            atlas[
                region["y"] : region["y"] + region["height"],
                region["x"] : region["x"] + region["width"],
            ],
            copy=True,
            order="C",
        )
        result.setflags(write=False)
        return result

    def __getitem__(self, slices):
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("prepared atlas image requires contiguous regions")
        with self._lock:
            atlas = self._atlas
            if self._closed or atlas is None:
                raise ValueError("prepared atlas image closed")
        y0, y1, _ = rows.indices(self._shape[0])
        x0, x1, _ = columns.indices(self._shape[1])
        if y0 >= y1 or x0 >= x1:
            return np.empty((max(0, y1 - y0), max(0, x1 - x0), 3), np.uint8)
        region = self._region
        left = max(0, math.floor(x0 * PREPARED_SCALE) - 1)
        top = max(0, math.floor(y0 * PREPARED_SCALE) - 1)
        right = min(region["width"], math.ceil(x1 * PREPARED_SCALE) + 1)
        bottom = min(region["height"], math.ceil(y1 * PREPARED_SCALE) + 1)
        crop = np.ascontiguousarray(
            atlas[
                region["y"] + top : region["y"] + bottom,
                region["x"] + left : region["x"] + right,
            ]
        )
        if crop.size == 0:
            return np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        atlas_x = (np.arange(x0, x1, dtype=np.float32) + 0.5) * PREPARED_SCALE - 0.5 - left
        atlas_y = (np.arange(y0, y1, dtype=np.float32) + 0.5) * PREPARED_SCALE - 0.5 - top
        map_x, map_y = np.meshgrid(atlas_x, atlas_y)
        result = cv2.remap(
            crop,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        result.setflags(write=False)
        return result

    def candidate_mask(self, xs, ys):
        with self._lock:
            if self._closed:
                raise ValueError("prepared atlas image closed")
            mask = self._mask
        xx, yy = np.meshgrid(
            np.rint((np.asarray(xs, dtype=np.float64) + 0.5) * PREPARED_SCALE - 0.5).astype(np.int64),
            np.rint((np.asarray(ys, dtype=np.float64) + 0.5) * PREPARED_SCALE - 0.5).astype(np.int64),
        )
        height, width = mask.shape
        valid = (xx >= 0) & (xx < width) & (yy >= 0) & (yy < height)
        result = np.zeros(xx.shape, dtype=bool)
        if np.any(valid):
            rows, columns = np.nonzero(valid)
            result[rows, columns] = mask[yy[rows, columns], xx[rows, columns]]
        result.setflags(write=False)
        return result


@dataclass(frozen=True)
class PreparedFloor:
    entry: dict
    calibration: MapCalibration
    record: dict


class PreparedLocalizationBundle:
    """Validated prepared atlases, masks, and source-bound floor ranking."""

    def __init__(self, root, localization_manifest):
        self.root = Path(root).resolve()
        self.localization_manifest_path = Path(localization_manifest).resolve()
        expected = self.localization_manifest_path.parent / PREPARED_DIRNAME
        _require(self.root == expected, "Prepared directory must belong to the requested localization package")
        self.manifest, self.manifest_sha256 = _read_json_bytes(self.root / "manifest.json")
        _require(isinstance(self.manifest, dict), "Prepared localization manifest is invalid")
        self._atlas_lock = threading.Lock()
        self._atlas_cache = OrderedDict()
        self._atlas_cache_bytes = 0
        self._atlas_paths = {}
        self._atlas_bytes = {}
        self._atlas_active = {}
        self._rank_failures = set()
        self._records = self._validate_file_records()
        self._floors = {}
        self._floor_records = {}
        self._atlases = {}
        self._validate_identity_and_source()
        self._validate_readiness()
        atlases = self._validate_atlas_layout()
        self._validate_floor_metadata(atlases)
        self._validate_payloads()
        self._register_atlas_payloads()
        self._validate_masks_and_floors()

    @classmethod
    def load(cls, localization_manifest):
        manifest = Path(localization_manifest).resolve()
        root = find_prepared_root(manifest)
        return None if root is None else cls(root, manifest)

    def summary(self):
        return {
            "layered_floors": len(self._floor_records),
            "maps": self.manifest["coverage"]["maps"],
            "files": len(self._records),
            "bytes": sum(record["size"] for record in self._records.values()),
            "webp_quality": self.manifest["source"]["webp_quality"],
        }

    def floor_entries(self, map_id):
        return [copy.deepcopy(self._floors[key].entry) for key in sorted(self._floors) if key[0] == str(map_id)]

    def tier_coverage_complete(self, map_id):
        prefix = f"scene_{map_id}_"
        return not any(str(item).startswith(prefix) for item in self.manifest["coverage"]["missing_logic_ids"])

    def open_floor(self, entry):
        tier_id, map_id = str(entry["tier_id"]), str(entry["map_id"])
        floor = self._floors[(map_id, tier_id)]
        atlas = self._acquire_atlas(map_id)
        try:
            region = floor.record["atlas_region"]
            mask = self._floor_mask(floor)
            full_shape = (
                int(floor.entry["height"]) + 2 * COMPOSITE_PADDING,
                int(floor.entry["width"]) + 2 * COMPOSITE_PADDING,
            )
            return PreparedAtlasFloorImage(
                atlas,
                region,
                mask,
                floor.calibration,
                full_shape=full_shape,
                on_close=lambda: self._release_atlas(map_id),
            )
        except BaseException:
            self._release_atlas(map_id)
            raise

    def rank_image(self, entry):
        tier_id = str(entry["tier_id"])
        if tier_id in self._rank_failures:
            return None
        floor = self._floors[(str(entry["map_id"]), tier_id)]
        record = floor.record
        path_name = record.get("rank_path")
        height, width = floor_rank_shape(record)
        left, top, right, bottom = record["rank_roi"]
        if path_name is None:
            if (left, top, right, bottom) != (0, 0, 0, 0):
                self._rank_failures.add(tier_id)
                return None
            image = np.zeros((height, width, 3), np.uint8)
            image.setflags(write=False)
            return image
        if not path_name.startswith("localization/"):
            self._rank_failures.add(tier_id)
            return None
        source_name = path_name.removeprefix("localization/")
        file_record = self._localization_files.get(source_name)
        if file_record is None:
            self._rank_failures.add(tier_id)
            return None
        try:
            try:
                path = resolve_prepared_path(self.root.parent.parent, path_name)
            except RuntimeError as exc:
                raise ValueError("unresolvable rank path") from exc
            with path.open("rb") as stream:
                data = stream.read(file_record[0] + 1)
            if len(data) != file_record[0] or _sha256(data) != file_record[1]:
                raise ValueError("rank checksum mismatch")
            crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if crop is None or crop.shape != (bottom - top, right - left, 3):
                raise ValueError("rank image shape mismatch")
            image = np.zeros((height, width, 3), np.uint8)
            image[top:bottom, left:right] = crop
            image.setflags(write=False)
            return image
        except (OSError, ValueError, cv2.error):
            self._rank_failures.add(tier_id)
            return None

    def _validate_file_records(self):
        records = self.manifest.get("files")
        _require(isinstance(records, list), "Prepared localization file list is missing")
        output = {}
        for record in records:
            _require(isinstance(record, dict), "Invalid prepared localization file record")
            name, size, digest = record.get("path"), record.get("size"), record.get("sha256")
            _require(
                isinstance(name, str)
                and name not in output
                and _positive_int(size)
                and isinstance(digest, str)
                and len(digest) == 64,
                f"Invalid or duplicate prepared localization resource: {name!r}",
            )
            int(digest, 16)
            resolve_prepared_path(self.root, name)
            output[name] = record
        return output

    def _validate_identity_and_source(self):
        manifest = self.manifest
        _require(
            manifest.get("kind") == PREPARED_KIND
            and type(manifest.get("schema_version")) is int
            and manifest["schema_version"] == PREPARED_VERSION
            and manifest.get("complete") is True,
            "Unsupported prepared localization bundle",
        )
        source = manifest.get("source")
        _require(isinstance(source, dict), "Prepared localization source is missing")
        _require(
            source.get("input_width") == INPUT_WIDTH
            and source.get("coarse_factor") == COARSE_FACTOR
            and source.get("composite_dim") == COMPOSITE_DIM
            and source.get("composite_padding") == COMPOSITE_PADDING
            and source.get("webp_quality") == WEBP_QUALITY,
            "Prepared localization search settings do not match this client",
        )
        _require(
            source.get("localization_manifest_sha256") == _digest(self.localization_manifest_path),
            "Prepared localization source manifest checksum mismatch",
        )
        package_root = self.root.parent.parent
        dataset_path = package_root / "dataset.json"
        package_path = package_root / PACKAGE_MARKER
        _require(package_path.is_file(), PREPARED_MISSING_MESSAGE)
        package, _package_sha256 = _read_json_bytes(package_path)
        _require(
            isinstance(package, dict)
            and package.get("kind") == PACKAGE_KIND
            and type(package.get("schema_version")) is int
            and package.get("schema_version") == PACKAGE_VERSION
            and package.get("complete") is True,
            "Prepared navigation package identity is invalid",
        )
        dataset, dataset_sha256 = _read_json_bytes(dataset_path)
        release_id = dataset.get("release_id") if isinstance(dataset, dict) else None
        _require(
            isinstance(release_id, str) and release_id and source.get("release_id") == release_id,
            "Prepared localization release does not match dataset.json",
        )
        _require(
            package.get("release_id") == release_id
            and package.get("dataset_sha256") == dataset_sha256
            and package.get("localization_manifest_sha256") == _digest(self.localization_manifest_path)
            and package.get("prepared_manifest_sha256") == self.manifest_sha256,
            "Prepared navigation package checksums do not match payloads",
        )
        coverage = manifest.get("coverage")
        _require(
            isinstance(coverage, dict)
            and coverage.get("mask_encoding") == MASK_ENCODING
            and _positive_int(coverage.get("layered_floors"))
            and isinstance(coverage.get("maps"), dict)
            and isinstance(coverage.get("missing_logic_ids"), list),
            "Invalid prepared localization coverage metadata",
        )

    def _validate_readiness(self):
        actual = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()}
        expected = set(self._records) | {"manifest.json", "validation.json", "READY"}
        _require(not (actual - expected), f"Prepared localization has unlisted files: {sorted(actual - expected)[:8]}")
        _require(
            not (expected - actual),
            f"Prepared localization is missing files: {sorted(expected - actual)[:8]}. {PREPARED_MISSING_MESSAGE}",
        )
        ready = (self.root / "READY").read_text(encoding="ascii").strip().split()
        _require(
            ready == [PREPARED_KIND, str(PREPARED_VERSION), self.manifest_sha256],
            "Prepared localization READY marker mismatch",
        )
        validation = _read_json(self.root / "validation.json")
        _require(isinstance(validation, dict), "Prepared localization validation report is invalid")

    def _validate_payloads(self):
        available = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file()}
        for name, record in self._records.items():
            path = resolve_prepared_path(self.root, name)
            if name not in available:
                raise ValueError(f"Prepared localization resource is missing: {name}")
            # Hash in bounded chunks and stop as soon as the file exceeds the
            # declared size, so an oversized file cannot exhaust memory first.
            expected = record["size"]
            digest = hashlib.sha256()
            total = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1 << 20):
                    total += len(chunk)
                    if total > expected:
                        break
                    digest.update(chunk)
            if total != expected or digest.hexdigest() != record["sha256"]:
                raise ValueError(f"Prepared localization resource checksum mismatch: {name}")

    def _validate_atlas_layout(self):
        atlases = self.manifest.get("atlases")
        _require(isinstance(atlases, dict) and atlases, "Prepared localization atlases are missing")
        for map_id, atlas in atlases.items():
            _require(isinstance(map_id, str) and map_id, "Invalid prepared localization map ID")
            _require(isinstance(atlas, dict), f"Invalid prepared atlas: {map_id}")
            path_name = atlas.get("path")
            mask_name = atlas.get("mask_path")
            _require(
                isinstance(path_name, str)
                and path_name in self._records
                and isinstance(mask_name, str)
                and mask_name in self._records,
                f"Prepared atlas resource is not listed: {map_id}",
            )
            shape = _finite_pair_shape(atlas.get("shape"))
            _require(shape is not None, f"Invalid prepared atlas shape: {map_id}")
            _require(type(atlas.get("padding")) is int and atlas["padding"] >= 0, f"Invalid atlas padding: {map_id}")
            _require(atlas.get("mask_bit_order") == "big", f"Invalid atlas mask bit order: {map_id}")
            _require(atlas.get("mask_row_major") is True, f"Invalid atlas mask row order: {map_id}")
            regions = atlas.get("regions")
            _require(isinstance(regions, dict) and regions, f"Prepared atlas has no floor regions: {map_id}")
            rects = []
            for tier_id, region in regions.items():
                _require(
                    isinstance(tier_id, str)
                    and tier_id
                    and isinstance(region, dict)
                    and all(type(region.get(key)) is int for key in ("x", "y", "width", "height"))
                    and region["x"] >= 0
                    and region["y"] >= 0
                    and region["width"] > 0
                    and region["height"] > 0
                    and region["x"] + region["width"] <= shape[1]
                    and region["y"] + region["height"] <= shape[0],
                    f"Invalid prepared atlas region: {map_id}/{tier_id}",
                )
                rects.append(region)
            for index, left in enumerate(rects):
                for right in rects[index + 1 :]:
                    overlap = not (
                        left["x"] + left["width"] <= right["x"]
                        or right["x"] + right["width"] <= left["x"]
                        or left["y"] + left["height"] <= right["y"]
                        or right["y"] + right["height"] <= left["y"]
                    )
                    _require(not overlap, f"Prepared atlas regions overlap: {map_id}")

        return atlases

    def _validate_floor_metadata(self, atlases):
        floors = self.manifest.get("floors")
        _require(isinstance(floors, list), "Prepared localization floors are missing")
        raw_manifest = _read_json(self.localization_manifest_path)
        raw_bundle = LocalizationTileBundle(self.localization_manifest_path.parent, raw_manifest)
        self._localization_files = raw_bundle.files
        base_maps = raw_manifest.get("base_maps")
        _require(isinstance(base_maps, list), "Invalid source localization base map list")
        runtime_map_ids = {
            entry.get("map_id")
            for entry in base_maps
            if isinstance(entry, dict) and entry.get("zoom") == "4" and isinstance(entry.get("map_id"), str)
        }
        catalog_path = resolve_bundle_path(
            self.localization_manifest_path.parent,
            raw_manifest.get("layer_catalog"),
        )
        catalog = raw_bundle.read_json(catalog_path)
        _require(
            isinstance(catalog, dict)
            and type(catalog.get("schema_version")) is int
            and catalog.get("schema_version") == 1
            and isinstance(catalog.get("layers"), list),
            "Invalid source layer catalog",
        )
        catalog_entries = [entry for entry in catalog["layers"] if isinstance(entry, dict)]
        all_layer_ids = [entry.get("id") for entry in catalog_entries]
        _require(
            len(catalog_entries) == len(catalog["layers"])
            and all(isinstance(tier_id, str) and tier_id for tier_id in all_layer_ids)
            and len(all_layer_ids) == len(set(all_layer_ids)),
            "Source layer catalog contains duplicate or invalid floor IDs",
        )
        layered_entries = [
            entry for entry in catalog["layers"] if isinstance(entry, dict) and entry.get("map_category") == "layered"
        ]
        self._catalog = {entry["id"]: entry for entry in layered_entries}
        layers_root = resolve_bundle_path(self.localization_manifest_path.parent, raw_manifest.get("layers_root"))
        missing_list = self.manifest["coverage"]["missing_logic_ids"]
        source_missing = catalog.get("missing_logic_ids", [])
        manifest_coverage = raw_manifest.get("coverage")
        manifest_missing = (
            manifest_coverage.get("missing_logic_ids", []) if isinstance(manifest_coverage, dict) else None
        )
        _require(
            isinstance(missing_list, list)
            and all(isinstance(item, str) and item for item in missing_list)
            and len(missing_list) == len(set(missing_list))
            and isinstance(source_missing, list)
            and all(isinstance(item, str) and item for item in source_missing)
            and len(source_missing) == len(set(source_missing))
            and isinstance(manifest_missing, list)
            and all(isinstance(item, str) and item for item in manifest_missing)
            and len(manifest_missing) == len(set(manifest_missing))
            and set(source_missing) == set(missing_list) == set(manifest_missing),
            "Prepared missing-layer declarations disagree with source catalog",
        )
        _require(
            not set(source_missing).intersection(all_layer_ids),
            "Prepared missing-layer declaration also exists in the source layer catalog",
        )
        expected_ids = set(self._catalog) - set(source_missing)
        actual_ids = set()
        seen = set()
        for record in floors:
            _require(isinstance(record, dict), "Invalid prepared floor record")
            tier_id = record.get("tier_id")
            map_id = record.get("map_id")
            _require(
                isinstance(tier_id, str)
                and tier_id
                and tier_id not in seen
                and isinstance(map_id, str)
                and map_id in atlases
                and tier_id in atlases[map_id]["regions"],
                f"Invalid or duplicate prepared floor identity: {tier_id!r}",
            )
            _require(map_id in runtime_map_ids, f"Prepared floor has no runtime zoom-4 base map: {tier_id}")
            seen.add(tier_id)
            actual_ids.add(tier_id)
            source = self._catalog.get(tier_id)
            _require(source is not None, f"Prepared floor is absent from source catalog: {tier_id}")
            _require(
                source.get("map_name") == map_id
                and source.get("level_id") == record.get("level_id")
                and source.get("map_category") == record.get("map_category") == "layered"
                and source.get("bounds") == record.get("bounds")
                and record.get("atlas_region") == atlases[map_id]["regions"][tier_id],
                f"Prepared floor identity disagrees with source catalog: {tier_id}",
            )
            bounds = record.get("bounds")
            _require(isinstance(bounds, dict), f"Prepared floor bounds are missing: {tier_id}")
            values = [bounds.get(key) for key in ("west", "east", "south", "north")]
            try:
                finite = all(type(v) in (int, float) and math.isfinite(v) for v in values)
            except OverflowError:
                finite = False
            _require(
                finite
                and math.isfinite(float(bounds["east"]) - float(bounds["west"]))
                and math.isfinite(float(bounds["north"]) - float(bounds["south"]))
                and float(bounds["east"]) > float(bounds["west"])
                and float(bounds["north"]) > float(bounds["south"]),
                f"Invalid prepared floor bounds: {tier_id}",
            )
            region = record["atlas_region"]
            atlas_shape = atlases[map_id]["shape"]
            _require(
                isinstance(region, dict)
                and all(type(region.get(key)) is int for key in ("x", "y", "width", "height"))
                and region["x"] >= 0
                and region["y"] >= 0
                and region["width"] > 0
                and region["height"] > 0
                and region["x"] + region["width"] <= atlas_shape[1]
                and region["y"] + region["height"] <= atlas_shape[0],
                f"Invalid prepared floor atlas region: {tier_id}",
            )
            mask_shape = _finite_pair_shape(record.get("mask_shape"))
            _require(
                _positive_int(source.get("width")) and _positive_int(source.get("height")),
                f"Invalid source floor dimensions: {tier_id}",
            )
            full_shape = (
                int(source["height"]) + 2 * COMPOSITE_PADDING,
                int(source["width"]) + 2 * COMPOSITE_PADDING,
            )
            expected_coarse_shape = (
                round(full_shape[0] * PREPARED_SCALE),
                round(full_shape[1] * PREPARED_SCALE),
            )
            _require(
                mask_shape == expected_coarse_shape == (region["height"], region["width"])
                and isinstance(record.get("mask_path"), str)
                and record["mask_path"] in self._records
                and record.get("mask_bit_order") == "big"
                and record.get("mask_row_major") is True
                and isinstance(record.get("mask_sha256"), str)
                and len(record["mask_sha256"]) == 64
                and _positive_int(record.get("mask_bytes"))
                and type(record.get("coverage_pixels")) is int
                and record["coverage_pixels"] >= 0,
                f"Invalid prepared floor mask metadata: {tier_id}",
            )
            rank_shape = _finite_pair_shape(record.get("rank_shape"))
            rank_roi = record.get("rank_roi")
            rank_path = record.get("rank_path")
            source_index = raw_bundle.read_json(resolve_bundle_path(layers_root, source.get("path")))
            _require(
                isinstance(source_index, dict)
                and source_index.get("id") == tier_id
                and source_index.get("map_id") == map_id,
                f"Source floor tile index has the wrong identity: {tier_id}",
            )
            source_rank = source_index.get("floor_rank")
            if source_rank is None:
                expected_rank_path = None
                expected_rank_size = None
                expected_rank_sha256 = None
                expected_rank_roi = [0, 0, 0, 0]
            else:
                _require(
                    isinstance(source_rank, dict) and isinstance(source_rank.get("roi"), list),
                    f"Source floor ranking record is invalid: {tier_id}",
                )
                if source_rank["roi"] == [0, 0, 0, 0]:
                    _require(
                        source_rank.get("path") is None
                        and source_rank.get("size") == 0
                        and source_rank.get("sha256") is None,
                        f"Source floor ranking record is invalid: {tier_id}",
                    )
                    expected_rank_path = None
                    expected_rank_size = None
                    expected_rank_sha256 = None
                    expected_rank_roi = [0, 0, 0, 0]
                    source_rank = None
            if source_rank is not None:
                _require(
                    isinstance(source_rank, dict)
                    and isinstance(source_rank.get("path"), str)
                    and _positive_int(source_rank.get("size"))
                    and isinstance(source_rank.get("sha256"), str)
                    and isinstance(source_rank.get("roi"), list),
                    f"Source floor ranking record is invalid: {tier_id}",
                )
                source_rank_path = validate_bundle_relative_path(source_rank["path"]).as_posix()
                expected_rank_path = validate_bundle_relative_path(f"localization/{source_rank_path}").as_posix()
                expected_rank_size = source_rank["size"]
                expected_rank_sha256 = source_rank["sha256"]
                expected_rank_roi = source_rank["roi"]
            _require(
                rank_shape == floor_rank_shape(record)
                and isinstance(rank_roi, list)
                and len(rank_roi) == 4
                and all(type(value) is int for value in rank_roi)
                and (
                    tuple(rank_roi) == (0, 0, 0, 0)
                    or (
                        0 <= rank_roi[0] < rank_roi[2] <= rank_shape[1]
                        and 0 <= rank_roi[1] < rank_roi[3] <= rank_shape[0]
                    )
                )
                and rank_path == expected_rank_path
                and rank_roi == expected_rank_roi
                and record.get("rank_size") == expected_rank_size
                and record.get("rank_sha256") == expected_rank_sha256
                and (
                    expected_rank_path is None
                    or raw_bundle.files.get(source_rank_path) == (expected_rank_size, expected_rank_sha256)
                ),
                f"Invalid prepared floor rank metadata: {tier_id}",
            )
            entry = {
                **record,
                "id": tier_id,
                "width": source["width"],
                "height": source["height"],
            }
            calibration = _prepared_calibration(entry)
            self._floors[(map_id, tier_id)] = PreparedFloor(entry, calibration, record)
            self._floor_records[(map_id, tier_id)] = record
        _require(actual_ids == expected_ids, "Prepared floor coverage disagrees with source catalog")
        coverage_maps = self.manifest["coverage"]["maps"]
        actual_maps = {map_id: sum(1 for floor_map, _tier in self._floors if floor_map == map_id) for map_id in atlases}
        _require(
            coverage_maps == actual_maps and self.manifest["coverage"]["layered_floors"] == len(self._floor_records),
            "Prepared localization coverage count mismatch",
        )
        floor_index = self._bundle_json("floor-index.json")
        expected_index = {
            map_id: [record for record in floors if record["map_id"] == map_id] for map_id in sorted(atlases)
        }
        _require(
            isinstance(floor_index, dict)
            and floor_index.get("schema_version") == PREPARED_VERSION
            and floor_index.get("input_width") == INPUT_WIDTH
            and floor_index.get("maps") == expected_index,
            "floor-index.json disagrees with prepared manifest",
        )

    def _register_atlas_payloads(self):
        for map_id, atlas in self.manifest["atlases"].items():
            self._atlas_paths[map_id] = resolve_prepared_path(self.root, atlas["path"])
            self._atlas_bytes[map_id] = atlas["shape"][0] * atlas["shape"][1] * 3

    def _decode_atlas_pixels(self, map_id):
        atlas = self.manifest["atlases"][map_id]
        shape = tuple(atlas["shape"])
        path = self._atlas_paths[map_id]
        record = self._records[atlas["path"]]
        with path.open("rb") as stream:
            data = stream.read(record["size"] + 1)
        _require(
            len(data) == record["size"] and _sha256(data) == record["sha256"],
            f"Prepared atlas resource checksum mismatch: {map_id}",
        )
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        _require(
            image is not None and image.dtype == np.uint8 and image.shape == (*shape, 3),
            f"Prepared atlas cannot be decoded: {map_id}",
        )
        _require(
            _sha256(memoryview(image) if image.flags.c_contiguous else image.tobytes(order="C"))
            == atlas.get("decoded_sha256"),
            f"Prepared atlas decoded checksum mismatch: {map_id}",
        )
        return image

    def validate_atlas_pixels(self):
        """Decode every atlas for publisher-side verification, never runtime startup."""
        for map_id in self.manifest["atlases"]:
            self._decode_atlas_pixels(str(map_id))

    def _validate_masks_and_floors(self):
        for map_id, atlas in self.manifest["atlases"].items():
            atlas_mask = self._decode_mask(
                atlas["mask_path"],
                tuple(atlas["shape"]),
                atlas.get("mask_bytes"),
                None,
            )
            union = np.zeros(atlas_mask.shape, bool)
            for (floor_map, tier_id), floor in self._floors.items():
                if floor_map != map_id:
                    continue
                record, region = floor.record, floor.record["atlas_region"]
                mask = self._decode_mask(
                    record["mask_path"],
                    tuple(record["mask_shape"]),
                    record["mask_bytes"],
                    record["coverage_pixels"],
                    record["mask_sha256"],
                )
                expected = atlas_mask[
                    region["y"] : region["y"] + region["height"],
                    region["x"] : region["x"] + region["width"],
                ]
                _require(np.array_equal(mask, expected), f"Prepared floor mask disagrees with atlas: {tier_id}")
                union[
                    region["y"] : region["y"] + region["height"],
                    region["x"] : region["x"] + region["width"],
                ] = mask
            _require(np.array_equal(union, atlas_mask), f"Prepared atlas mask has unexplained coverage: {map_id}")

    def _decode_mask(self, name, shape, byte_count, coverage_pixels, digest=None):
        path = resolve_prepared_path(self.root, name)
        count = int(np.prod(shape))
        _require(
            _positive_int(byte_count) and byte_count == math.ceil(count / 8),
            f"Prepared mask metadata mismatch: {name}",
        )
        with path.open("rb") as source:
            data = source.read(byte_count + 1)
        _require(
            len(data) == byte_count
            and _sha256(data) == self._records[name]["sha256"]
            and (digest is None or self._records[name]["sha256"] == digest),
            f"Prepared mask metadata mismatch: {name}",
        )
        bits = np.unpackbits(np.frombuffer(data, np.uint8), bitorder="big")
        _require(not bits[count:].any(), f"Prepared mask has nonzero tail bits: {name}")
        mask = bits[:count].reshape(shape).astype(bool)
        if coverage_pixels is not None:
            _require(int(np.count_nonzero(mask)) == coverage_pixels, f"Prepared mask coverage count mismatch: {name}")
        mask.setflags(write=False)
        return mask

    def _floor_mask(self, floor: PreparedFloor):
        record = floor.record
        return self._decode_mask(
            record["mask_path"],
            tuple(record["mask_shape"]),
            record["mask_bytes"],
            record["coverage_pixels"],
            record["mask_sha256"],
        )

    @property
    def _atlas_resident_bytes(self):
        with self._atlas_lock:
            maps = set(self._atlas_cache) | {map_id for map_id, count in self._atlas_active.items() if count > 0}
            return sum(self._atlas_bytes[map_id] for map_id in maps)

    def _acquire_atlas(self, map_id):
        with self._atlas_lock:
            self._atlas_active[map_id] = self._atlas_active.get(map_id, 0) + 1
            try:
                return self._atlas_pixels_locked(map_id)
            except BaseException:
                count = self._atlas_active[map_id]
                if count <= 1:
                    self._atlas_active.pop(map_id, None)
                else:
                    self._atlas_active[map_id] = count - 1
                self._evict_atlas_cache_locked()
                raise

    def _release_atlas(self, map_id):
        with self._atlas_lock:
            count = self._atlas_active.get(map_id, 0)
            if count <= 1:
                self._atlas_active.pop(map_id, None)
            else:
                self._atlas_active[map_id] = count - 1
            self._evict_atlas_cache_locked()

    def _atlas_pixels_locked(self, map_id):
        image = self._atlas_cache.get(map_id)
        if image is None:
            try:
                image = self._decode_atlas_pixels(map_id)
            except (ValueError, OSError, cv2.error) as exc:
                raise ValueError(f"{PREPARED_MISSING_MESSAGE} ({exc})") from exc
            image.setflags(write=False)
            self._atlas_cache[map_id] = image
            self._atlas_cache_bytes += image.nbytes
        self._atlas_cache.move_to_end(map_id)
        self._evict_atlas_cache_locked()
        return image

    def _evict_atlas_cache_locked(self):
        while self._atlas_cache and (
            len(self._atlas_cache) > 4 or self._atlas_resident_bytes_locked() > MAX_ATLAS_CACHE_BYTES
        ):
            inactive = next(
                (map_id for map_id in self._atlas_cache if self._atlas_active.get(map_id, 0) <= 0),
                None,
            )
            if inactive is None:
                return
            old_image = self._atlas_cache.pop(inactive)
            self._atlas_cache_bytes -= old_image.nbytes

    def _atlas_resident_bytes_locked(self):
        maps = set(self._atlas_cache) | {map_id for map_id, count in self._atlas_active.items() if count > 0}
        return sum(self._atlas_bytes[map_id] for map_id in maps)

    def _bundle_json(self, name):
        _require(name in self._records, f"Prepared localization resource is missing: {name}")
        path = resolve_prepared_path(self.root, name)
        record = self._records[name]
        with path.open("rb") as stream:
            data = stream.read(record["size"] + 1)
        _require(
            len(data) == record["size"] and _sha256(data) == record["sha256"],
            f"Prepared localization JSON checksum mismatch: {name}",
        )
        return json.loads(data.decode("utf-8"))
