"""Prepared global overviews and bounded, lossless WebP region reads."""

import hashlib
import json
import logging
import math
import threading
from collections import OrderedDict
from concurrent.futures import CancelledError
from pathlib import Path
from typing import ClassVar

import cv2
import numpy as np

from src.localization.localization_workers import _DECODE_BUDGET, decode_memory_bytes, decode_reservation

CACHE_ROOT = Path("cache/localization/tiled-images")
FORMAT_VERSION = 1


def source_descriptor(path, size, *, resize=None, proposal_size=None):
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "version": FORMAT_VERSION,
        "source_sha256": digest,
        "source_size": list(size),
        "resize": list(resize) if resize is not None else None,
        "proposal_size": list(proposal_size) if proposal_size is not None else None,
    }


def descriptor_key(descriptor):
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()


def floor_options(entry):
    b = entry["bounds"]
    resize = (4 * ((b["east"] - b["west"]) / entry["width"]), 4 * ((b["north"] - b["south"]) / entry["height"]))
    proposal = (max(1, round((b["east"] - b["west"]) * 0.5)), max(1, round((b["north"] - b["south"]) * 0.5)))
    return {"resize": resize, "proposal_size": proposal}


class TiledMapImage:
    _rejected_roots: ClassVar[set[Path]] = set()
    _rejection_lock = threading.Lock()

    @classmethod
    def reject_prepared(cls, root):
        with cls._rejection_lock:
            cls._rejected_roots.add(Path(root).resolve())

    @classmethod
    def allow_prepared(cls, root):
        with cls._rejection_lock:
            cls._rejected_roots.discard(Path(root).resolve())

    def __init__(self, root, metadata, *, source=None, cache_bytes=8 * 1024**2):
        self.root = Path(root).resolve()
        self.metadata = metadata
        self.source = source
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

    @classmethod
    def open_prepared(cls, *args, **kwargs):
        try:
            return cls.find(*args, **kwargs)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            # An unavailable derived index must not remove a map from global
            # competition. The caller can still use the original image.
            return None

    @classmethod
    def find(cls, path, size, *, resize=None, proposal_size=None, cache_root=CACHE_ROOT):
        if not Path(cache_root).is_dir():
            return None
        descriptor = source_descriptor(path, size, resize=resize, proposal_size=proposal_size)
        root = Path(cache_root) / descriptor_key(descriptor)
        with cls._rejection_lock:
            if root.resolve() in cls._rejected_roots:
                return None
        try:
            metadata = json.loads((root / "index.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(metadata, dict):
            raise ValueError("invalid tiled image index")
        if metadata.get("descriptor") != descriptor:
            raise ValueError("tiled image does not match source")
        expected = [size[1], size[0]]
        if resize is not None:
            expected = [round(size[1] * resize[1]), round(size[0] * resize[0])]
        if metadata.get("shape") != expected:
            raise ValueError("tiled image dimensions do not match source")
        return cls(root, metadata, source=Path(path))

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

    def verify_resources(self, stop=None):
        """Check derived files without decoding images during startup."""
        records = [*self.metadata["tiles"].values(), *self.metadata["overviews"].values()]
        if self.metadata["descriptor"]["proposal_size"] is not None:
            records.append(self.metadata["proposal"])
        for record in records:
            if stop is not None and stop.is_set():
                raise CancelledError()
            with self._resource_path(record).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != record["sha256"]:
                raise ValueError("corrupt tiled image resource")

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

    def overview_memory_bytes(self, scale):
        output_pixels = round(self.shape[0] * scale) * round(self.shape[1] * scale)
        descriptor = self.metadata.get("descriptor")
        if descriptor is not None and float(scale).hex() not in self.metadata["overviews"]:
            return decode_memory_bytes(*descriptor["source_size"], self.shape[0] * self.shape[1] + output_pixels)
        return 64 * 1024**2 + output_pixels * 4

    def _check_open(self):
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")

    def overview(self, scale):
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        record = self.metadata["overviews"].get(float(scale).hex())
        if record is None:
            # Preserve exact global candidate generation for uncommon screen
            # widths too. This compatibility path temporarily costs full-image
            # memory; preparing that width removes the fallback on next startup.
            if self.source is None:
                raise ValueError(f"global overview scale {scale!r} is not prepared")
            logging.getLogger(__name__).warning("Unprepared overview scale %s; prepare this screen width", scale)
            descriptor = self.metadata["descriptor"]
            if (
                source_descriptor(
                    self.source,
                    descriptor["source_size"],
                    resize=descriptor["resize"],
                    proposal_size=descriptor["proposal_size"],
                )
                != descriptor
            ):
                raise ValueError("source changed after tiled image opened")
            size = self.overview_memory_bytes(scale)
            with decode_reservation(size, budget=_DECODE_BUDGET, check_open=self._check_open):
                return self._fallback_overview(scale, descriptor)
        image = self._read(record)
        expected = (round(self.shape[0] * scale), round(self.shape[1] * scale))
        if image.shape[:2] != expected:
            raise ValueError("global overview dimensions do not match scale")
        return image

    def coarse_overview(self, scale):
        return self.overview(scale)

    def _fallback_overview(self, scale, descriptor):
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        resize = descriptor["resize"]
        image = cv2.imread(str(self.source), cv2.IMREAD_COLOR if resize is None else cv2.IMREAD_UNCHANGED)
        if image is None or image.shape[:2] != tuple(reversed(descriptor["source_size"])):
            raise ValueError("invalid original image for overview fallback")
        if image.ndim == 3 and image.shape[2] == 4:
            color = image[:, :, :3].copy()
            color[image[:, :, 3] == 0] = 0
            image = color
            del color
        elif image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if resize is not None:
            image = cv2.resize(image, None, fx=resize[0], fy=resize[1], interpolation=cv2.INTER_AREA)
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        image.setflags(write=False)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        return image

    def proposal(self):
        record = self.metadata.get("proposal")
        if record is None:
            raise ValueError("floor proposal image is missing")
        return self._read(record)

    def _read_tile(self, record):
        return self._read(record)

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

    def _tile_cache_key(self, coordinates, record):
        return coordinates

    def _tile(self, x, y):
        coordinates = f"{x},{y}"
        record = self.metadata["tiles"][coordinates]
        key = self._tile_cache_key(coordinates, record)
        cached = self._cached_pixels(key)
        if cached is not None:
            return cached
        pixels = self._read_tile(record)
        expected = (
            min(self.tile_size, self._native_shape[0] - y * self.tile_size),
            min(self.tile_size, self._native_shape[1] - x * self.tile_size),
        )
        if pixels.shape[:2] != expected:
            raise ValueError("tile dimensions do not match grid")
        return self._remember_pixels(key, pixels)

    def __getitem__(self, slices):
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("tiled image requires contiguous regions")
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        return self._read_region(y0, y1, x0, x1)

    def _read_region(self, y0, y1, x0, x1):
        result = np.empty((max(0, y1 - y0), max(0, x1 - x0), self.shape[2]), np.uint8)
        if result.size == 0:
            return result
        for ty in range(y0 // self.tile_size, (y1 - 1) // self.tile_size + 1):
            for tx in range(x0 // self.tile_size, (x1 - 1) // self.tile_size + 1):
                tile = self._tile(tx, ty)
                ox, oy = tx * self.tile_size, ty * self.tile_size
                left, top = max(x0, ox), max(y0, oy)
                right, bottom = min(x1, ox + tile.shape[1]), min(y1, oy + tile.shape[0])
                result[top - y0 : bottom - y0, left - x0 : right - x0] = tile[
                    top - oy : bottom - oy, left - ox : right - ox
                ]
        return result
