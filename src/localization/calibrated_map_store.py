"""Resolve explicitly configured, deployable calibrated map resources."""

import json
import re
from pathlib import Path

from src.localization.minimap_map_locator import MapCalibration, MinimapMapLocator


class CalibratedMapStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def available_maps(self):
        if not self.root.is_dir():
            return []
        available = []
        for path in self.root.iterdir():
            if not path.is_dir() or not re.fullmatch(r"[A-Za-z0-9_-]+", path.name):
                continue
            if not (path / "4" / f"{path.name}_4.png").is_file():
                continue
            try:
                MapCalibration.from_mapping_file(path / "4" / f"{path.name}_4_mapping.json")
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                continue
            available.append(path.name)
        return sorted(available)

    def load(self, map_id="", *, background=False):
        try:
            if background:
                from src.localization.localization_workers import LocalizationWorkers
                from src.localization.map_set_locator import MapSetLocator

                available = self.available_maps()
                if map_id and (not re.fullmatch(r"[A-Za-z0-9_-]+", map_id) or map_id not in available):
                    raise ValueError("invalid_visual_map_id")
                if not available:
                    raise ValueError("visual_map_assets_missing")
                return map_id, MapSetLocator(self, [map_id] if map_id else available, workers=LocalizationWorkers())
            return self._load_resources(map_id)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("invalid_visual_map_metadata") from exc

    def _load_resources(self, map_id):
        if not map_id:
            available = self.available_maps()
            if not available:
                raise ValueError("visual_map_assets_missing")
            if len(available) > 1:
                from src.localization.map_set_locator import MapSetLocator

                return "", MapSetLocator(self, available)
            map_id = available[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", map_id):
            raise ValueError("invalid_visual_map_id")
        directory = self.root / map_id / "4"
        locator = MinimapMapLocator.from_files(
            directory / f"{map_id}_4.png",
            directory / f"{map_id}_4_mapping.json",
        )
        layer_root = self.root.parent / "layers"
        catalog_path = layer_root / "catalog.json"
        if catalog_path.is_file():
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
