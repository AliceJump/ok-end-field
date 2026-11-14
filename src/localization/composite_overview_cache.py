"""Bounded derived overviews, keyed by immutable pixels and render geometry."""

import hashlib
import json
import os
import re
import threading
import time
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

import numpy as np

from src.localization.derived_array_cache import load_arrays
from src.localization.localization_cache import regular_file, safe_directory

CACHE_ROOT = Path("cache/localization/composite-overviews")
MAX_BYTES = 256 * 1024**2
MAX_ENTRIES = 128
MAX_AGE_S = 30 * 86400
TEMP_AGE_S = 86400
_LOCK = threading.Lock()


def _refresh_file_stamps(paths, stamps):
    """Batch directory metadata within this request, refreshing every later call.

    Windows directory enumeration supplies size/mtime without one file lookup
    per tile. DirEntry.stat still follows links, matching os.stat semantics.
    """
    directories = {}
    for path in paths:
        if path not in stamps:
            directory, name = os.path.split(path)
            directories.setdefault(directory, {})[name] = path
    for directory, pending in directories.items():
        if len(pending) >= 4:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = pending.pop(entry.name, None)
                    if path is not None:
                        stat = entry.stat()
                        stamps[path] = (stat.st_size, stat.st_mtime_ns)
                        if not pending:
                            break
        # Also preserve the missing-file error rather than accepting an
        # incomplete identity when a referenced tile disappeared.
        for path in pending.values():
            stat = os.stat(path)
            stamps[path] = (stat.st_size, stat.st_mtime_ns)


def image_identity(image, *, file_stamps=None):
    records = getattr(image, "resource_records", None)
    if not callable(records):
        return None
    names = sorted({record["path"] for record in records()})
    # Share only this overview request's file metadata across its two images.
    # Every later request refreshes stamps, so changed/missing source objects
    # cannot silently reuse a stale persisted overview.
    file_stamps = {} if file_stamps is None else file_stamps
    root = os.fspath(image.root)
    paths = [os.path.join(root, name) for name in names]
    _refresh_file_stamps(paths, file_stamps)
    files = []
    for name, path in zip(names, paths, strict=True):
        files.append((name, *file_stamps[path]))
    return {
        "metadata": image.metadata,
        "shape": image.shape,
        "scale": image._scale_xy,
        "alpha": image._alpha,
        "files": files,
    }


def cache_key(descriptor):
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()


def load_overview(key, shape):
    try:
        root = safe_directory(CACHE_ROOT)
        if root is None or not re.fullmatch(r"[0-9a-f]{64}", key):
            return None
        path = root / f"{key}.npz"
        if not regular_file(path, root):
            return None
        saved = load_arrays(path, {"pixels": (shape, np.uint8), "sha256": ((), "U64")})
        pixels = saved["pixels"]
        if hashlib.sha256(pixels.tobytes()).hexdigest() != saved["sha256"].item():
            return None
        pixels.setflags(write=False)
        with suppress(OSError):
            if time.time() - path.stat().st_mtime > 3600 and regular_file(path, root):
                os.utime(path)
        return pixels
    except (
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        EOFError,
        MemoryError,
        RuntimeError,
    ):
        return None


def save_overview(key, pixels):
    """Cache failure cannot remove a competitor or fail valid localization."""
    temporary = None
    if pixels.nbytes + 4096 > MAX_BYTES or not re.fullmatch(r"[0-9a-f]{64}", key):
        return
    try:
        root = safe_directory(CACHE_ROOT, create=True)
        if root is None:
            return
        temporary = root / f".{key}.{uuid.uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            np.savez(stream, pixels=pixels, sha256=hashlib.sha256(pixels.tobytes()).hexdigest())
        with _LOCK:
            if safe_directory(root) is None:
                return
            target = root / f"{key}.npz"
            temporary.replace(target)
            _prune(root, keep=target)
    except OSError:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                if safe_directory(temporary.parent) is not None and regular_file(temporary, temporary.parent):
                    temporary.unlink()


def _prune(root, *, keep=None):
    entries = []
    now = time.time()
    for path in root.iterdir():
        try:
            if not regular_file(path, root):
                continue
            stamp = path.stat()
            if re.fullmatch(r"\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp", path.name):
                if now - stamp.st_mtime > TEMP_AGE_S:
                    path.unlink()
            elif re.fullmatch(r"[0-9a-f]{64}\.npz", path.name):
                if path != keep and now - stamp.st_mtime > MAX_AGE_S:
                    try:
                        path.unlink()
                        continue
                    except OSError:
                        pass
                entries.append((stamp.st_mtime_ns, stamp.st_size, path))
        except OSError:
            continue
    total, count = sum(size for _, size, _ in entries), len(entries)
    for _, size, path in sorted(entries):
        if total <= MAX_BYTES and count <= MAX_ENTRIES:
            break
        if path != keep:
            try:
                if safe_directory(root) is not None and regular_file(path, root):
                    path.unlink()
                    total -= size
                    count -= 1
            except OSError:
                continue
    if keep is not None and (total > MAX_BYTES or count > MAX_ENTRIES):
        with suppress(OSError):
            if safe_directory(root) is not None and regular_file(keep, root):
                keep.unlink()


def prune_cache(root=None):
    try:
        root = safe_directory(CACHE_ROOT if root is None else root)
        if root is not None:
            with _LOCK:
                _prune(root)
    except OSError:
        pass
