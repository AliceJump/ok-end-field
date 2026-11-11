"""Resolve explicitly configured, deployable calibrated map resources."""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from src.localization.localization_workers import decode_memory_bytes
from src.localization.minimap_map_locator import MapCalibration, MinimapMapLocator
from src.localization.tiled_map_image import TiledMapImage


def resolve_bundle_path(root: Path, name: str) -> Path:
    """Resolve a manifest path confined to its bundle."""
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError(f"Invalid localization resource path: {name!r}")
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise ValueError(f"Invalid localization resource path: {name!r}")
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Localization resource escapes bundle: {name}")
    return path


@dataclass(frozen=True)
class _MapFiles:
    image: Path
    mapping: Path
    size: tuple[int, int] | None = None


class CalibratedMapStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def _read_manifest(self):
        path = self.root.parent / "manifest.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if isinstance(manifest, dict) and manifest.get("kind") == "okef-visual-localization":
            if resolve_bundle_path(path.parent, manifest.get("maps_root")) != self.root:
                return None  # The sibling manifest describes a different configured map directory.
        elif self.root.name != "maps":
            return None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("kind") != "okef-visual-localization"
            or not isinstance(manifest.get("base_maps"), list)
        ):
            raise ValueError(f"Invalid visual localization manifest: {path}")
        return manifest

    def _map_files(self, manifest):
        if manifest is None:
            files = {}
            if not self.root.is_dir():
                return files
            for path in self.root.iterdir():
                if not path.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", path.name):
                    continue
                stem = path / "4" / f"{path.name}_4"
                for suffix in (".png", ".webp"):
                    image = stem.with_suffix(suffix)
                    if image.is_file():
                        files[path.name] = _MapFiles(image, stem.with_name(stem.name + "_mapping.json"))
                        break
            return files

        files = {}
        for entry in manifest["base_maps"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("zoom"), str):
                raise ValueError("Invalid visual base map entry")
            if entry["zoom"] != "4":
                continue
            map_id = entry.get("map_id")
            if not isinstance(map_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", map_id) or map_id in files:
                raise ValueError(f"Invalid or duplicate visual map ID: {map_id!r}")
            image = resolve_bundle_path(self.root.parent, entry.get("image"))
            mapping = resolve_bundle_path(self.root.parent, entry.get("mapping"))
            if not image.is_relative_to(self.root) or not mapping.is_relative_to(self.root):
                raise ValueError(f"Visual base map is outside maps_root: {map_id}")
            size = (entry.get("width"), entry.get("height"))
            if any(type(dimension) is not int or dimension <= 0 for dimension in size):
                raise ValueError(f"Invalid visual base map dimensions: {map_id}")
            files[map_id] = _MapFiles(image, mapping, size)
        return files

    @staticmethod
    def _available_maps(files):
        available = []
        for map_id, paths in files.items():
            if not paths.image.is_file():
                continue
            try:
                calibration = MapCalibration.from_mapping_file(paths.mapping)
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                continue
            if paths.size is None or paths.size == (calibration.image_width, calibration.image_height):
                available.append(map_id)
        return sorted(available)

    def available_maps(self):
        return self._available_maps(self._map_files(self._read_manifest()))

    def decode_memory_bytes(self, map_id):
        try:
            paths = self._map_files(self._read_manifest())[map_id]
            size = paths.size
            if size is None:
                calibration = MapCalibration.from_mapping_file(paths.mapping)
                size = (calibration.image_width, calibration.image_height)
            if TiledMapImage.open_prepared(paths.image, size) is not None:
                return 64 * 1024**2
            return decode_memory_bytes(*size)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            # The actual load reports invalid resources and applies backoff.
            return 1024**3

    def load(self, map_id="", *, background=False):
        try:
            manifest = self._read_manifest()
            files = self._map_files(manifest)
            if background:
                from src.localization.localization_workers import LocalizationWorkers
                from src.localization.map_set_locator import MapSetLocator

                available = self._available_maps(files)
                if map_id and map_id not in available:
                    raise ValueError("invalid_visual_map_id")
                if not available:
                    raise ValueError("visual_map_assets_missing")
                return map_id, MapSetLocator(self, [map_id] if map_id else available, workers=LocalizationWorkers())
            return self._load_resources(map_id, files, manifest)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("invalid_visual_map_metadata") from exc

    def _load_resources(self, map_id, files, manifest):
        if not map_id:
            available = self._available_maps(files)
            if not available:
                raise ValueError("visual_map_assets_missing")
            if len(available) > 1:
                from src.localization.map_set_locator import MapSetLocator

                return "", MapSetLocator(self, available)
            map_id = available[0]
        if map_id not in files:
            raise ValueError("invalid_visual_map_id")
        paths = files[map_id]
        locator = MinimapMapLocator.from_files(paths.image, paths.mapping)
        height, width = locator.map_image.shape[:2]
        if paths.size is not None and (width, height) != paths.size:
            raise ValueError(f"Visual base map size does not match manifest: {map_id}")
        if manifest is None:
            layer_root = self.root.parent / "layers"
            catalog_path = layer_root / "catalog.json"
        else:
            layer_root = resolve_bundle_path(self.root.parent, manifest.get("layers_root"))
            catalog_path = resolve_bundle_path(self.root.parent, manifest.get("layer_catalog"))
        if manifest is not None or catalog_path.is_file():
            from src.localization.layered_minimap_locator import LayeredMinimapLocator

            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            if (
                not isinstance(catalog, dict)
                or catalog.get("schema_version") != 1
                or not isinstance(catalog.get("layers"), list)
            ):
                raise ValueError("invalid_visual_layer_catalog")
            locator = LayeredMinimapLocator(locator, map_id, layer_root, catalog)
        return map_id, locator
