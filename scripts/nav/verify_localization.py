"""Verify the manifest, hashes, images and geometry of a visual localization bundle."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.localization.layered_minimap_locator import layer_calibration
from src.localization.localization_bundle import (
    TILED_KIND,
    TILED_SCHEMA_VERSION,
    LocalizationTileBundle,
    resolve_bundle_path,
)
from src.localization.minimap_map_locator import MapCalibration
from src.localization.prepared_localization import PreparedLocalizationBundle


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(root: Path, *, require_prepared: bool = True):
    root = root.resolve()
    manifest = _read_json(root / "manifest.json")
    _require(manifest["kind"] == TILED_KIND, "Unexpected localization bundle kind")
    _require(
        type(manifest["schema_version"]) is int and manifest["schema_version"] == TILED_SCHEMA_VERSION,
        "Unsupported localization manifest version",
    )
    bundle = LocalizationTileBundle(root, manifest)
    checked_payloads = {}
    files = set()
    total_bytes = 0
    for entry in manifest["files"]:
        name = entry["path"]
        path = resolve_bundle_path(root, name)
        _require(path not in files, f"Duplicate localization resource: {name}")
        with path.open("rb") as stream:
            pointer = stream.read(42).startswith(b"version https://git-lfs.github.com/spec/v1")
            _require(not pointer, f"Git LFS pointer remains: {name}")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        _require(path.stat().st_size == entry["size"], f"Size mismatch: {name}")
        _require(digest == entry["sha256"], f"SHA-256 mismatch: {name}")
        total_bytes += entry["size"]
        files.add(path)

    maps_root = resolve_bundle_path(root, manifest["maps_root"])
    _require(maps_root.parent == root, "Localization maps_root must be a direct child of the bundle root")
    layers_root = resolve_bundle_path(root, manifest["layers_root"])
    catalog_path = resolve_bundle_path(root, manifest["layer_catalog"])
    base_maps = manifest.get("base_maps")
    _require(isinstance(base_maps, list), "Localization base map list is invalid")
    for entry in base_maps:
        _require(
            isinstance(entry, dict) and isinstance(entry.get("zoom"), str),
            f"Invalid visual base map entry: {entry!r}",
        )
    _require(
        any(entry["zoom"] == "4" for entry in base_maps),
        "Localization bundle has no runtime zoom-4 base map",
    )
    referenced = {catalog_path}
    map_keys = set()
    for entry in base_maps:
        _require(
            isinstance(entry.get("map_id"), str) and re.fullmatch(r"[A-Za-z0-9_-]+", entry["map_id"]) is not None,
            f"Invalid visual map ID: {entry.get('map_id')!r}",
        )
        key = (entry["map_id"], entry["zoom"])
        _require(key not in map_keys, f"Duplicate base map: {key}")
        map_keys.add(key)
        image = resolve_bundle_path(root, entry["image"])
        mapping = resolve_bundle_path(root, entry["mapping"])
        _require(
            image.is_relative_to(maps_root) and mapping.is_relative_to(maps_root),
            f"Base map resource is outside maps_root: {key}",
        )
        size = (entry["width"], entry["height"])
        tiled = bundle.open_base(image, entry["map_id"], size, mapping, zoom=entry["zoom"])
        referenced.update(resolve_bundle_path(root, record["path"]) for record in tiled.resource_records())
        tiled.verify_sparse_payloads(checked_payloads)
        try:
            calibration = MapCalibration.from_mapping_file(mapping)
        except (TypeError, ValueError, OverflowError, np.linalg.LinAlgError) as exc:
            raise ValueError(f"Invalid mapping calibration: {mapping}") from exc
        _require(size == (calibration.image_width, calibration.image_height), f"Mapping image_size mismatch: {mapping}")
        forward = np.vstack((calibration.matrix, [0, 0, 1]))
        inverse = np.vstack((calibration.inverse_matrix, [0, 0, 1]))
        _require(
            np.allclose(forward @ inverse, np.eye(3), atol=1e-7, rtol=0)
            and np.allclose(inverse @ forward, np.eye(3), atol=1e-7, rtol=0),
            f"Mapping matrices are not inverses: {mapping}",
        )
        referenced.update((image, mapping))

    catalog = _read_json(catalog_path)
    _require(
        type(catalog["schema_version"]) is int and catalog["schema_version"] == 1, "Unsupported layer catalog version"
    )
    tiers = set()
    independent = 0
    for entry in catalog["layers"]:
        tier = entry["id"]
        _require(tier not in tiers, f"Duplicate layer: {tier}")
        tiers.add(tier)
        image = resolve_bundle_path(layers_root, entry["path"])
        _require(image.name == entry["filename"], f"Layer filename disagrees with path: {tier}")
        size = (entry["width"], entry["height"])
        tiled = bundle.open_floor(layers_root, entry)
        referenced.update(resolve_bundle_path(root, record["path"]) for record in tiled.resource_records())
        tiled.verify_sparse_payloads(checked_payloads)
        layer_calibration(entry["bounds"], *size)
        category = entry["map_category"]
        _require(category in {"layered", "independent"}, f"Unknown map category: {tier}")
        independent += category == "independent"
        referenced.add(image)
    coverage = manifest["coverage"]
    missing = coverage["missing_logic_ids"]
    _require(len(tiers) == coverage["layer_count"], "Layer count mismatch")
    _require(independent == coverage["independent_layer_count"], "Independent layer count mismatch")
    _require(catalog.get("missing_logic_ids", []) == missing, "Missing layer declarations disagree")
    _require(not tiers.intersection(missing), "Missing layer also declared present")
    difference = referenced.symmetric_difference(files)
    _require(not difference, f"Manifest and catalog references differ: {sorted(difference)}")
    unlisted = (
        {path for path in root.rglob("*") if path.is_file() and not path.is_relative_to(root / "prepared")}
        - files
        - {root / "manifest.json"}
    )
    _require(not unlisted, f"Unlisted localization files: {sorted(unlisted)}")
    prepared = PreparedLocalizationBundle.load(root / "manifest.json")
    if prepared is None and require_prepared:
        from src.localization.prepared_localization import PREPARED_MISSING_MESSAGE

        raise ValueError(PREPARED_MISSING_MESSAGE)
    if prepared is not None:
        prepared.validate_atlas_pixels()
    return {
        "base_maps": len(map_keys),
        "layers": len(tiers),
        "files": len(files),
        "bytes": total_bytes,
        "missing_logic_ids": missing,
        "prepared": None if prepared is None else prepared.summary(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("assets/nav-grid-data/navigation/localization"))
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, np.linalg.LinAlgError, cv2.error) as exc:
        parser.exit(1, f"Invalid localization bundle: {exc}\n")


if __name__ == "__main__":
    main()
