"""Prepare exact global overviews and lossless WebP tiles before navigation."""

import hashlib
import json
import os
import shutil
import time
import uuid
from concurrent.futures import CancelledError
from pathlib import Path

import cv2

from src.localization.layered_minimap_locator import layer_calibration
from src.localization.localization_bundle import TILED_KIND, resolve_bundle_path
from src.localization.localization_workers import _DECODE_BUDGET, decode_memory_bytes
from src.localization.minimap_map_locator import COARSE_FACTOR, minimap_map_scale
from src.localization.tiled_map_image import CACHE_ROOT, TiledMapImage, descriptor_key, source_descriptor

DEFAULT_WIDTHS = (1280, 1600, 1920, 2560, 3440, 3840)


def _check_cancelled(stop):
    if stop is not None and stop.is_set():
        raise CancelledError()


def _write_image(root, relative, image, *, stop=None):
    _check_cancelled(stop)
    success, encoded = cv2.imencode(".webp", image, [cv2.IMWRITE_WEBP_QUALITY, 101])
    if not success:
        raise ValueError("WebP encoding failed")
    data = encoded.tobytes()
    _check_cancelled(stop)
    (root / relative).write_bytes(data)
    return {"path": relative, "shape": list(image.shape[:2]), "sha256": hashlib.sha256(data).hexdigest()}


def prepare_image(
    path,
    size,
    scales,
    *,
    resize=None,
    proposal_size=None,
    cache_root=CACHE_ROOT,
    tile_size=512,
    force=False,
    verify=False,
    stop=None,
):
    _check_cancelled(stop)
    descriptor = source_descriptor(path, size, resize=resize, proposal_size=proposal_size)
    root = Path(cache_root).resolve() / descriptor_key(descriptor)
    scales = tuple(sorted({float(value) for value in scales}))
    if not scales or any(not 0 < value <= 1 for value in scales) or not 32 <= tile_size <= 2048:
        raise ValueError("invalid overview scale or tile size")
    existing = TiledMapImage.open_prepared(
        path, size, resize=resize, proposal_size=proposal_size, cache_root=cache_root
    )
    if existing is not None and verify:
        try:
            existing.verify_resources(stop)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            # If repair fails (e.g. disk full), subsequent runtime loads must
            # fall back to the original, not adopt this known-broken index.
            TiledMapImage.reject_prepared(existing.root)
            existing.close()
            existing = None
    if existing is not None:
        if (
            not force
            and existing.tile_size == tile_size
            and all(value.hex() in existing.metadata["overviews"] for value in scales)
        ):
            return {"path": str(path), "reused": True, "tiles": len(existing.metadata["tiles"])}
        # Adding a screen width must not invalidate previously prepared widths.
        scales = tuple(sorted(set(scales) | {float.fromhex(key) for key in existing.metadata["overviews"]}))
        existing.close()
    output_pixels = round(size[0] * resize[0]) * round(size[1] * resize[1]) if resize else size[0] * size[1]
    memory = decode_memory_bytes(*size, output_pixels)
    while not _DECODE_BUDGET.acquire(memory):
        _check_cancelled(stop)
        time.sleep(0.05)
    generation = "generation-" + uuid.uuid4().hex
    published = False
    try:
        result = _build_image(path, size, scales, resize, proposal_size, root, generation, descriptor, tile_size, stop)
        published = True
        return result
    finally:
        _DECODE_BUDGET.release(memory)
        # Only this invocation's unpublished UUID directory belongs to us.
        # Published generations may still be in use by localization readers.
        directory = root / generation
        if not published and directory.resolve().parent == root and not directory.is_symlink():
            shutil.rmtree(directory, ignore_errors=True)


def _build_image(path, size, scales, resize, proposal_size, root, generation, descriptor, tile_size, stop):
    _check_cancelled(stop)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR if resize is None else cv2.IMREAD_UNCHANGED)
    if image is None or image.shape[:2] != (size[1], size[0]):
        raise ValueError(f"source dimensions do not match: {path}")
    (root / generation).mkdir(parents=True, exist_ok=False)
    metadata = {"descriptor": descriptor, "tile_size": tile_size, "tiles": {}, "overviews": {}}
    if proposal_size is not None:
        # Match the previous floor index exactly, including alpha handling
        # before normalizing the original floor to four pixels per meter.
        proposal = cv2.resize(image, proposal_size, interpolation=cv2.INTER_AREA)
        if proposal.ndim == 3 and proposal.shape[2] == 4:
            alpha = proposal[:, :, 3]
            proposal = proposal[:, :, :3].copy()
            proposal[alpha == 0] = 0
        elif proposal.ndim == 2:
            proposal = cv2.cvtColor(proposal, cv2.COLOR_GRAY2BGR)
        metadata["proposal"] = _write_image(root, f"{generation}/proposal.webp", proposal, stop=stop)
        del proposal
    if image.ndim == 3 and image.shape[2] == 4:
        color = image[:, :, :3].copy()
        color[image[:, :, 3] == 0] = 0
        image = color
        del color
    elif image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if resize is not None:
        image = cv2.resize(image, None, fx=resize[0], fy=resize[1], interpolation=cv2.INTER_AREA)
    metadata["shape"] = list(image.shape[:2])
    for i, scale in enumerate(scales):
        _check_cancelled(stop)
        overview = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        metadata["overviews"][scale.hex()] = _write_image(root, f"{generation}/overview-{i}.webp", overview, stop=stop)
        del overview
    for y in range(0, image.shape[0], tile_size):
        for x in range(0, image.shape[1], tile_size):
            metadata["tiles"][f"{x // tile_size},{y // tile_size}"] = _write_image(
                root,
                f"{generation}/tile-{x // tile_size}-{y // tile_size}.webp",
                image[y : y + tile_size, x : x + tile_size],
                stop=stop,
            )
    if descriptor != source_descriptor(path, size, resize=resize, proposal_size=proposal_size):
        raise ValueError("source changed while preparing tiles")
    # Readers retain their immutable generation. A partial build never replaces
    # the visible index, and an interrupted rebuild cannot corrupt live readers.
    staging = root / generation / "index.json"
    staging.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    _check_cancelled(stop)
    os.replace(staging, root / "index.json")
    TiledMapImage.allow_prepared(root)
    return {"path": str(path), "reused": False, "tiles": len(metadata["tiles"])}


def prepare_bundle(root, widths=DEFAULT_WIDTHS, *, cache_root=CACHE_ROOT, force=False, verify=False, stop=None):
    _check_cancelled(stop)
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("kind") == TILED_KIND:
        return
    scales = [minimap_map_scale(width) * COARSE_FACTOR for width in widths]
    for entry in manifest["base_maps"]:
        if entry["zoom"] == "4":
            yield prepare_image(
                resolve_bundle_path(root, entry["image"]),
                (entry["width"], entry["height"]),
                scales,
                cache_root=cache_root,
                force=force,
                verify=verify,
                stop=stop,
            )
    catalog = json.loads(resolve_bundle_path(root, manifest["layer_catalog"]).read_text(encoding="utf-8"))
    layers_root = resolve_bundle_path(root, manifest["layers_root"])
    for entry in catalog["layers"]:
        if entry["map_category"] != "layered":
            continue
        _, resize = layer_calibration(entry["bounds"], entry["width"], entry["height"])
        b = entry["bounds"]
        proposal_size = (max(1, round((b["east"] - b["west"]) * 0.5)), max(1, round((b["north"] - b["south"]) * 0.5)))
        yield prepare_image(
            resolve_bundle_path(layers_root, entry["path"]),
            (entry["width"], entry["height"]),
            scales,
            resize=resize,
            proposal_size=proposal_size,
            cache_root=cache_root,
            force=force,
            verify=verify,
            stop=stop,
        )
