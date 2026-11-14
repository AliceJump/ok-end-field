"""Prepared global overviews and bounded, lossless WebP region reads."""

import hashlib
import math
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np


def floor_options(entry):
    b = entry["bounds"]
    resize = (4 * ((b["east"] - b["west"]) / entry["width"]), 4 * ((b["north"] - b["south"]) / entry["height"]))
    proposal = (max(1, round((b["east"] - b["west"]) * 0.5)), max(1, round((b["north"] - b["south"]) * 0.5)))
    return {"resize": resize, "proposal_size": proposal}


class TiledMapImage:
    def __init__(self, root, metadata, *, cache_bytes=8 * 1024**2):
        self.root = Path(root).resolve()
        self.metadata = metadata
        height, width = metadata["shape"]
        tile_size = metadata["tile_size"]
        if any(type(value) is not int or value <= 0 for value in (height, width, tile_size)):
            raise ValueError("invalid tiled image geometry")
        if not 32 <= tile_size <= 2048:
            raise ValueError("invalid tile size")
        self._native_shape = (height, width, 3)
        self.tile_size = tile_size
        expected = {
            f"{x},{y}" for y in range(math.ceil(height / tile_size)) for x in range(math.ceil(width / tile_size))
        }
        if set(metadata["tiles"]) != expected:
            raise ValueError("incomplete tiled image grid")
        self._cache = OrderedDict()
        self._bytes = 0
        self._limit = max(0, int(cache_bytes))
        self._lock = threading.Lock()
        self._closed = False

    @property
    def shape(self):
        return self._native_shape

    @property
    def nbytes(self):
        with self._lock:
            return self._bytes

    def close(self):
        with self._lock:
            self._closed = True
            self._cache.clear()
            self._bytes = 0

    def _resource_path(self, record):
        name = record["path"]
        if not isinstance(name, str) or not name:
            raise ValueError("invalid tiled image path")
        path = (self.root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(self.root):
            raise ValueError("tiled image path escapes resource root")
        return path

    def _read(self, record, *, mask_alpha=False, require_bgr=False, alpha_only=False):
        path = self._resource_path(record)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        size = record.get("size")
        with path.open("rb") as stream:
            data = stream.read() if size is None else stream.read(size + 1)
        if (size is not None and len(data) != size) or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"corrupt tiled image resource: {record['path']}")
        try:
            flags = cv2.IMREAD_UNCHANGED if mask_alpha or require_bgr or alpha_only else cv2.IMREAD_COLOR
            pixels = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
        except cv2.error as exc:
            raise ValueError(f"invalid tiled image resource: {record['path']}") from exc
        if (
            pixels is None
            or list(pixels.shape[:2]) != record["shape"]
            or pixels.dtype != np.uint8
            or pixels.ndim != 3
            or pixels.shape[2] not in (3, 4)
            or (require_bgr and pixels.shape[2] != 3)
        ):
            raise ValueError(f"invalid tiled image resource: {record['path']}")
        if alpha_only:
            alpha = pixels[:, :, 3] if pixels.shape[2] == 4 else np.full(pixels.shape[:2], 255, np.uint8)
            pixels = alpha[:, :, None].copy()
        elif pixels.shape[2] == 4:
            color = pixels[:, :, :3].copy()
            color[pixels[:, :, 3] == 0] = 0
            pixels = color
        pixels.setflags(write=False)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        return pixels

    def _cached_pixels(self, key):
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
            pixels = self._cache.get(key)
            if pixels is not None:
                self._cache.move_to_end(key)
            return pixels

    def _remember_pixels(self, key, pixels):
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
            if pixels.nbytes > self._limit:
                return pixels
            previous = self._cache.pop(key, None)
            if previous is not None:
                self._bytes -= previous.nbytes
            self._cache[key] = pixels
            self._bytes += pixels.nbytes
            while self._bytes > self._limit:
                self._bytes -= self._cache.popitem(last=False)[1].nbytes
        return pixels

    def __getitem__(self, slices):
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("tiled image requires contiguous regions")
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        return self._read_region(y0, y1, x0, x1)

    def overview_memory_bytes(self, scale):
        return 64 * 1024**2 + round(self.shape[0] * scale) * round(self.shape[1] * scale) * 4
