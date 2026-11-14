"""Remove only disposable localization artifacts, without following links."""

import logging
import re
import stat
import threading
from contextlib import suppress
from pathlib import Path

CACHE_ROOT = Path("cache/localization")
_HASH = re.compile(r"[0-9a-f]{64}")
_GENERATION = re.compile(r"generation-[0-9a-f]{32}")
_TILE_FILE = re.compile(r"(?:index\.json|proposal\.webp|overview-\d+\.webp|tile-\d+-\d+\.webp)")


def safe_directory(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_dir():
        return None
    return path


def regular_file(path, root):
    return (
        path.parent == root and path.resolve() == path and stat.S_ISREG(path.lstat().st_mode) and not path.is_junction()
    )


def _remove_file(path, root):
    try:
        if safe_directory(root) is not None and regular_file(path, root):
            size = path.stat().st_size
            path.unlink()
            return size
    except OSError:
        pass
    return 0


def _remove_tiles(root, stop):
    removed = 0
    for descriptor in root.iterdir():
        if stop.is_set():
            break
        if not _HASH.fullmatch(descriptor.name) or safe_directory(descriptor) is None:
            continue
        for entry in descriptor.iterdir():
            if stop.is_set():
                return removed
            if entry.name == "index.json":
                removed += _remove_file(entry, descriptor)
            elif _GENERATION.fullmatch(entry.name) and safe_directory(entry) is not None:
                for resource in entry.iterdir():
                    if stop.is_set():
                        return removed
                    if _TILE_FILE.fullmatch(resource.name):
                        removed += _remove_file(resource, entry)
                if safe_directory(entry) is not None:
                    with suppress(OSError):
                        entry.rmdir()
        with suppress(OSError):
            descriptor.rmdir()
    return removed


def maintain_cache(stop, *, root=CACHE_ROOT):
    """Prune derived arrays and retire the unused PNG preparation caches."""
    from src.localization.composite_overview_cache import prune_cache

    removed = 0
    try:
        root = safe_directory(root)
        if root is None or stop.is_set():
            return removed
        prune_cache(root / "composite-overviews")
        layers = safe_directory(root / "layer-index")
        if layers is not None:
            for path in layers.iterdir():
                if stop.is_set():
                    return removed
                if re.fullmatch(r"[0-9a-f]{64}\.npz", path.name):
                    removed += _remove_file(path, layers)
        tiles = safe_directory(root / "tiled-images")
        if tiles is not None and not stop.is_set():
            removed += _remove_tiles(tiles, stop)
    except OSError:
        logging.getLogger(__name__).warning("Localization cache cleanup could not finish", exc_info=True)
    return removed


def start_cache_maintenance(stop):
    thread = threading.Thread(target=maintain_cache, args=(stop,), name="LocalizationCacheCleanup", daemon=True)
    try:
        thread.start()
    except RuntimeError:
        logging.getLogger(__name__).warning("Localization cache cleanup could not start", exc_info=True)
    return thread
