"""Read calibrated regions directly from the publisher's native tile grid."""

from fractions import Fraction

import cv2
import numpy as np

from src.localization.tiled_map_image import TiledMapImage


class UnpreparedTileOverview(ValueError):
    """The package has no overview at the exact scale needed for global search."""


def _axis_taps(start, stop, scale, length, area):
    """Source indices and weights for an ROI in the full resize's coordinates."""
    output = np.arange(start, stop, dtype=np.float64)
    left = output / scale
    first = np.floor(left).astype(np.intp)
    if not area:
        # OpenCV uses its two-tap area mode when either axis is enlarged.
        fraction = output + 1 - (first + 1) * scale
        fraction = np.maximum(0, fraction - np.floor(np.maximum(0, fraction)))
        return [(first, 1 - fraction), (np.minimum(first + 1, length - 1), fraction)]
    right = np.minimum((output + 1) / scale, length)
    taps = []
    for offset in range(int(np.max(np.ceil(right) - first))):
        index = first + offset
        weight = np.maximum(0, np.minimum(right, index + 1) - np.maximum(left, index)) / (right - left)
        taps.append((np.minimum(index, length - 1), weight))
    return taps


class NativeTileImage(TiledMapImage):
    def __init__(self, root, metadata, *, scale, match_size, alpha, cache_bytes=8 * 1024**2):
        super().__init__(root, metadata, cache_bytes=cache_bytes)
        self._scale_xy = scale
        periods = tuple(Fraction(factor).limit_denominator(128) for factor in scale)
        self._periods = (
            periods if all(float(period) == factor for period, factor in zip(periods, scale, strict=True)) else None
        )
        self._match_shape = (match_size[1], match_size[0], 3)
        self._alpha = alpha

    def _read_tile(self, record):
        return self._read(record, mask_alpha=self._alpha)

    def resource_records(self):
        yield from self.metadata["tiles"].values()
        yield from self.metadata["overviews"].values()
        if self.metadata["proposal"] is not None:
            yield self.metadata["proposal"]

    def overview(self, scale):
        record = self.metadata["overviews"].get(float(scale).hex())
        if record is None:
            raise UnpreparedTileOverview(f"Localization tile package has no global search image for scale {scale!r}")
        return self._read(record)

    def __getitem__(self, slices):
        if self._scale_xy == (1.0, 1.0):
            return super().__getitem__(slices)
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("tiled image requires contiguous regions")
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        if y0 >= y1 or x0 >= x1:
            return np.empty((max(0, y1 - y0), max(0, x1 - x0), 3), np.uint8)
        fx, fy = self._scale_xy
        h, w, _ = self._native_shape
        if self._periods is not None:
            # An integral number of source periods keeps the local OpenCV
            # resize on the full raster's pixel phase. Use the true source
            # edge for the final partial period.
            px, py = self._periods
            ox, oy = x0 // px.numerator, y0 // py.numerator
            left, top = ox * px.denominator, oy * py.denominator
            right = min(w, ((x1 + px.numerator - 1) // px.numerator) * px.denominator)
            bottom = min(h, ((y1 + py.numerator - 1) // py.numerator) * py.denominator)
            x, y = x0 - ox * px.numerator, y0 - oy * py.numerator
            out_right, out_bottom = x + x1 - x0, y + y1 - y0
            # Half-to-even dimension rounding can remove the last pixel from
            # a cropped resize even when the full image includes it.
            if round((right - left) * fx) >= out_right and round((bottom - top) * fy) >= out_bottom:
                source = self._read_region(top, bottom, left, right)
                normalized = cv2.resize(source, None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
                return normalized[y:out_bottom, x:out_right]
        area = fx <= 1 and fy <= 1
        xs = _axis_taps(x0, x1, fx, w, area)
        ys = _axis_taps(y0, y1, fy, h, area)
        left, top = xs[0][0][0], ys[0][0][0]
        right = max(indices[-1] for indices, _ in xs) + 1
        bottom = max(indices[-1] for indices, _ in ys) + 1
        source = self._read_region(top, bottom, left, right)
        horizontal = np.zeros((bottom - top, x1 - x0, 3), np.float32)
        for indices, weights in xs:
            horizontal += source[:, indices - left] * weights.astype(np.float32)[None, :, None]
        output = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
        for indices, weights in ys:
            output += horizontal[indices - top] * weights.astype(np.float32)[:, None, None]
        return np.rint(output, out=output).astype(np.uint8)

    @property
    def shape(self):
        return self._match_shape
