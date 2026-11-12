"""Current-floor pixels over a dimmed, world-aligned base map.

The floor's alpha constrains candidate centers, independently of the screenshot.
Background pixels remain matching evidence outside that footprint.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict

import cv2
import numpy as np

from src.localization.composite_overview_cache import cache_key, image_identity, load_overview, save_overview
from src.localization.minimap_map_locator import MapCalibration
from src.localization.native_tile_image import _axis_taps
from src.localization.tiled_map_image import TiledMapImage


def _blend_regions(foreground, alpha, background, dim):
    """Keep float intermediates below one MiB, with identical pixel rounding."""
    result = np.empty_like(foreground)
    height, width = foreground.shape[:2]
    for y in range(0, height, 128):
        for x in range(0, width, 256):
            region = np.s_[y : y + 128, x : x + 256]
            weight = alpha[region].astype(np.float32) / 255
            mixed = weight * foreground[region]
            mixed += (1 - weight) * dim * background[region]
            np.rint(mixed, out=mixed)
            result[region] = mixed
    return result


class CompositeFloorImage(TiledMapImage):
    def __init__(self, foreground, calibration, base, base_calibration, *, dim=0.3, padding=320):
        if not 0 <= dim <= 1:
            raise ValueError("invalid background dimming")
        self.foreground = foreground
        self.alpha = foreground.alpha_image()
        self.base = base  # Borrowed; closing a floor must not close the base map.
        self.dim = float(dim)
        self.padding = int(padding)
        self._shape = (foreground.shape[0] + 2 * padding, foreground.shape[1] + 2 * padding, 3)
        matrix = calibration.matrix.copy()
        matrix[:, 2] -= matrix[:, :2] @ np.array([padding, padding])
        self.calibration = MapCalibration(matrix, np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2])
        self._base_transform = base_calibration.inverse_matrix @ np.vstack((matrix, [0, 0, 1]))
        self._cache = OrderedDict()
        self._bytes = 0
        self._limit = 8 * 1024**2
        self._lock = threading.Lock()
        self._closed = False
        self._coverage_cache = OrderedDict()

    @property
    def shape(self):
        return self._shape

    @property
    def nbytes(self):
        return super().nbytes + self.alpha.nbytes + sum(mask.nbytes for mask in self._coverage_cache.values())

    def close(self):
        super().close()
        self.alpha.close()
        self._coverage_cache.clear()

    def _floor_region(self, source, y0, y1, x0, x1):
        result = np.zeros((y1 - y0, x1 - x0, source.shape[2]), np.uint8)
        p = self.padding
        left, top = max(x0, p), max(y0, p)
        right, bottom = min(x1, p + source.shape[1]), min(y1, p + source.shape[0])
        if left < right and top < bottom:
            result[top - y0 : bottom - y0, left - x0 : right - x0] = source[top - p : bottom - p, left - p : right - p]
        return result

    def _base_region(self, y0, y1, x0, x1):
        transform = self._base_transform.copy()
        transform[:, 2] += transform[:, :2] @ np.array([x0, y0])
        corners = transform @ np.array(
            [[0, x1 - x0 - 1, 0, x1 - x0 - 1], [0, 0, y1 - y0 - 1, y1 - y0 - 1], [1, 1, 1, 1]]
        )
        left, top = np.maximum(0, np.floor(corners.min(axis=1)).astype(int) - 2)
        right, bottom = np.minimum(
            [self.base.shape[1], self.base.shape[0]], np.ceil(corners.max(axis=1)).astype(int) + 3
        )
        if left >= right or top >= bottom:
            return np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        pixels = self.base[top:bottom, left:right]
        transform[:, 2] -= [left, top]
        return cv2.warpAffine(
            pixels,
            transform,
            (x1 - x0, y1 - y0),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        )

    def __getitem__(self, slices):
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("composite image requires contiguous regions")
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        key = (y0, y1, x0, x1)
        cached = self._cached_pixels(key)
        if cached is not None:
            return cached
        if y1 <= y0 or x1 <= x0:
            return np.empty((max(0, y1 - y0), max(0, x1 - x0), 3), np.uint8)
        foreground = self._floor_region(self.foreground, y0, y1, x0, x1)
        alpha = self._floor_region(self.alpha, y0, y1, x0, x1)
        background = self._base_region(y0, y1, x0, x1)
        result = _blend_regions(foreground, alpha, background, self.dim)
        result.setflags(write=False)
        return self._remember_pixels(key, result)

    def coarse_overview(self, scale):
        """Resize in bounded blocks, preserving the full raster's pixel phase."""
        h, w = self.shape[:2]
        oh, ow = round(h * scale), round(w * scale)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        file_stamps = {}
        foreground = image_identity(self.foreground, file_stamps=file_stamps)
        base = image_identity(self.base, file_stamps=file_stamps)
        key = None
        if foreground is not None and base is not None:
            key = cache_key(
                {
                    "version": 1,
                    "opencv": cv2.__version__,
                    "numpy": np.__version__,
                    "foreground": foreground,
                    "base": base,
                    "scale": scale,
                    "shape": self.shape,
                    "padding": self.padding,
                    "dim": self.dim,
                    "transform": self._base_transform.tolist(),
                }
            )
            cached = load_overview(key, (oh, ow, 3))
            if cached is not None:
                return cached
        output = np.empty((oh, ow, 3), np.uint8)
        for y0 in range(0, oh, 128):
            y1 = min(oh, y0 + 128)
            ys = _axis_taps(y0, y1, scale, h, scale <= 1)
            for x0 in range(0, ow, 128):
                x1 = min(ow, x0 + 128)
                xs = _axis_taps(x0, x1, scale, w, scale <= 1)
                left, top = xs[0][0][0], ys[0][0][0]
                right = max(indices[-1] for indices, _ in xs) + 1
                bottom = max(indices[-1] for indices, _ in ys) + 1
                source = self[top:bottom, left:right]
                horizontal = np.zeros((bottom - top, x1 - x0, 3), np.float32)
                for indices, weights in xs:
                    horizontal += source[:, indices - left] * weights.astype(np.float32)[None, :, None]
                block = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
                for indices, weights in ys:
                    block += horizontal[indices - top] * weights.astype(np.float32)[:, None, None]
                output[y0:y1, x0:x1] = np.rint(block).astype(np.uint8)
        output.setflags(write=False)
        if key is not None:
            save_overview(key, output)
        return output

    def candidate_mask(self, xs, ys):
        """A half-meter edge allowance covers calibration/raster rounding only."""
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        xs, ys = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
        key = (xs.tobytes(), ys.tobytes()) if len(xs) > 1 and len(ys) > 1 else None
        if key is not None and key in self._coverage_cache:
            self._coverage_cache.move_to_end(key)
            return self._coverage_cache[key]
        output = np.zeros((len(ys), len(xs)), bool)
        for row in range(0, len(ys), 128):
            yy = np.asarray(ys[row : row + 128])
            for col in range(0, len(xs), 128):
                xx = np.asarray(xs[col : col + 128])
                x0, y0 = math.floor(xx.min()) - 3, math.floor(yy.min()) - 3
                x1, y1 = math.ceil(xx.max()) + 4, math.ceil(yy.max()) + 4
                alpha = self._floor_region(self.alpha, y0, y1, x0, x1)[:, :, 0]
                support = cv2.dilate((alpha > 0).astype(np.uint8), np.ones((5, 5), np.uint8))
                ix, iy = np.rint(xx - x0).astype(int), np.rint(yy - y0).astype(int)
                output[row : row + len(yy), col : col + len(xx)] = support[np.ix_(iy, ix)] > 0
        output.setflags(write=False)
        if key is not None and output.nbytes <= 256 * 1024:
            self._coverage_cache[key] = output
            while len(self._coverage_cache) > 4:
                self._coverage_cache.popitem(last=False)
        return output
