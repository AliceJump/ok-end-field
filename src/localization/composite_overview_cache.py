"""Bounded derived overviews, keyed by immutable pixels and render geometry."""

import hashlib
import json
import os
import re
import threading
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path

import numpy as np

CACHE_ROOT = Path("cache/localization/composite-overviews")
MAX_BYTES = 256 * 1024**2
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
    path = CACHE_ROOT / f"{key}.npz"
    try:
        # Validate decompressed sizes before numpy allocates any array.
        with zipfile.ZipFile(path) as archive:
            if sorted(archive.namelist()) != ["pixels.npy", "sha256.npy"]:
                return None
            if archive.getinfo("pixels.npy").file_size > int(np.prod(shape)) + 4096:
                return None
            if archive.getinfo("sha256.npy").file_size > 4096:
                return None
        with np.load(path, allow_pickle=False) as saved:
            pixels = saved["pixels"]
            if pixels.dtype != np.uint8 or pixels.shape != shape:
                return None
            if hashlib.sha256(pixels.tobytes()).hexdigest() != str(saved["sha256"].item()):
                return None
        pixels.setflags(write=False)
        return pixels
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, EOFError):
        return None


def save_overview(key, pixels):
    """Cache failure cannot remove a competitor or fail valid localization."""
    temporary = None
    if pixels.nbytes + 4096 > MAX_BYTES:
        return
    try:
        root = CACHE_ROOT.resolve()
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / f".{key}.{uuid.uuid4().hex}.tmp"
        with temporary.open("wb") as stream:
            np.savez(stream, pixels=pixels, sha256=hashlib.sha256(pixels.tobytes()).hexdigest())
        with _LOCK:
            target = root / f"{key}.npz"
            temporary.replace(target)
            entries = []
            for path in root.glob("*.npz"):
                if re.fullmatch(r"[0-9a-f]{64}\.npz", path.name) and path.resolve().parent == root:
                    stat = path.stat()
                    entries.append((stat.st_mtime_ns, stat.st_size, path))
            total = sum(size for _, size, _ in entries)
            for _, size, path in sorted(entries):
                if total <= MAX_BYTES:
                    break
                if path != target:
                    path.unlink()
                    total -= size
    except OSError:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
