"""Load the configured v3 bundle without excluding broken competitors."""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from src.localization.localization_bundle import (
    TILED_KIND,
    TILED_SCHEMA_VERSION,
    LocalizationTileBundle,
    resolve_bundle_path,
)
from src.localization.minimap_map_locator import MapCalibration, MinimapMapLocator


@dataclass(frozen=True)
class _MapFiles:
    image: Path
    mapping: Path
    size: tuple[int, int]


class CalibratedMapStore:
    def __init__(self, root, *, require_prepared=True):
        self.root = Path(root).expanduser().resolve()
        self.require_prepared = bool(require_prepared)
        self._prepared_lock = threading.Lock()
        self._prepared_key = None
        self._prepared_bundle = None

    def _read_manifest(self):
        path = self.root.parent / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("kind") != TILED_KIND
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != TILED_SCHEMA_VERSION
            or not isinstance(manifest.get("base_maps"), list)
            or resolve_bundle_path(path.parent, manifest.get("maps_root")) != self.root
        ):
            raise ValueError("invalid_visual_map_manifest")
        return manifest

    def _prepared_for(self, manifest_path):
        from src.localization.prepared_localization import (
            PREPARED_MISSING_MESSAGE,
            PreparedLocalizationBundle,
            find_prepared_root,
        )

        root = find_prepared_root(manifest_path)
        if root is None:
            if self.require_prepared:
                raise ValueError(PREPARED_MISSING_MESSAGE)
            return None
        try:
            marker = (
                (root / "READY").stat().st_mtime_ns,
                (root / "READY").stat().st_size,
                (root.parent.parent / "dataset.json").stat().st_mtime_ns,
                (root.parent.parent / "prepared-package.json").stat().st_mtime_ns,
                manifest_path.stat().st_mtime_ns,
                manifest_path.stat().st_size,
            )
        except OSError as exc:
            if self.require_prepared:
                raise ValueError(PREPARED_MISSING_MESSAGE) from exc
            return None
        with self._prepared_lock:
            if marker != self._prepared_key:
                try:
                    self._prepared_bundle = PreparedLocalizationBundle(root, manifest_path)
                except (OSError, ValueError) as exc:
                    if self.require_prepared:
                        raise ValueError(f"{PREPARED_MISSING_MESSAGE}（{exc}）") from exc
                    return None
                self._prepared_key = marker
            return self._prepared_bundle

    def _map_files(self, manifest):
        files = {}
        for entry in manifest["base_maps"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("zoom"), str):
                raise ValueError("Invalid visual base map entry")
            if entry["zoom"] != "4":
                continue
            map_id = entry.get("map_id")
            if not isinstance(map_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", map_id) or map_id in files:
                raise ValueError("Invalid or duplicate visual map ID")
            image = resolve_bundle_path(self.root.parent, entry.get("image"))
            mapping = resolve_bundle_path(self.root.parent, entry.get("mapping"))
            if not image.is_relative_to(self.root) or not mapping.is_relative_to(self.root):
                raise ValueError("Visual base map is outside maps_root")
            size = (entry.get("width"), entry.get("height"))
            if any(type(dimension) is not int or dimension <= 0 for dimension in size):
                raise ValueError("Invalid visual base map dimensions")
            files[map_id] = _MapFiles(image, mapping, size)
        return files

    def available_maps(self):
        return sorted(self._map_files(self._read_manifest()))

    def decode_memory_bytes(self, map_id):
        return 64 * 1024**2

    def load(self, map_id="", *, background=False):
        try:
            manifest = self._read_manifest()
            files = self._map_files(manifest)
            available = sorted(files)
            if map_id and map_id not in files:
                raise ValueError("invalid_visual_map_id")
            if not available:
                raise ValueError("visual_map_assets_missing")
            if background or (not map_id and len(available) > 1):
                from src.localization.localization_workers import LocalizationWorkers
                from src.localization.map_set_locator import MapSetLocator

                return map_id, MapSetLocator(
                    self,
                    [map_id] if map_id else available,
                    workers=LocalizationWorkers() if background else None,
                    require_all_maps=True,
                    async_search=background,
                )
            return self._load_resources(map_id or available[0], files, manifest)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("invalid_visual_map_metadata") from exc

    def _load_resources(self, map_id, files, manifest):
        from src.localization.layered_minimap_locator import LayeredMinimapLocator

        paths = files[map_id]
        manifest_path = self.root.parent / "manifest.json"
        bundle = LocalizationTileBundle(self.root.parent, manifest)
        prepared = self._prepared_for(manifest_path)
        calibration = MapCalibration.from_mapping(bundle.read_json(paths.mapping), name=paths.mapping.stem)
        if (calibration.image_width, calibration.image_height) != paths.size:
            raise ValueError("Visual base map calibration size does not match manifest")
        layer_root = resolve_bundle_path(self.root.parent, manifest.get("layers_root"))
        catalog = bundle.read_json(resolve_bundle_path(self.root.parent, manifest.get("layer_catalog")))
        if (
            not isinstance(catalog, dict)
            or type(catalog.get("schema_version")) is not int
            or catalog["schema_version"] != 1
            or not isinstance(catalog.get("layers"), list)
        ):
            raise ValueError("invalid_visual_layer_catalog")
        base = MinimapMapLocator(bundle.open_base(paths.image, map_id, paths.size, paths.mapping), calibration)
        try:
            locator = LayeredMinimapLocator(
                base,
                map_id,
                layer_root,
                catalog,
                bundle=bundle,
                prepared=prepared,
                allow_unprepared=not self.require_prepared,
            )
        except BaseException:
            base.close()
            raise
        return map_id, locator
