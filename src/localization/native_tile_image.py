"""Read calibrated regions directly from the publisher's native tile grid."""

from dataclasses import dataclass
from fractions import Fraction

import cv2
import numpy as np

from src.localization.template_correlation import correlate_bounded
from src.localization.tiled_map_image import TiledMapImage


class UnpreparedTileOverview(ValueError):
    """The package has no overview at the exact scale needed for global search."""


@dataclass(frozen=True)
class ZeroPaddedSearchRaster:
    shape: tuple[int, int, int]
    roi: tuple[int, int, int, int]
    pixels: np.ndarray | None

    @property
    def nbytes(self):
        return 0 if self.pixels is None else self.pixels.nbytes

    def as_dense(self):
        height, width, _ = self.shape
        if self.pixels is not None and self.roi == (0, 0, width, height):
            return self.pixels
        image = np.zeros(self.shape, np.uint8)
        if self.pixels is not None:
            left, top, right, bottom = self.roi
            image[top:bottom, left:right] = self.pixels
        image.setflags(write=False)
        return image

    def match_scores(self, template, *, mask=None):
        height, width, _ = self.shape
        th, tw = template.shape[:2]
        # OpenCV treats constant templates specially even on uniform images.
        if not np.any(template != template[0, 0]):
            return correlate_bounded(self, template, mask=mask)
        score_h, score_w = height - th + 1, width - tw + 1
        empty_score = 0.0 if mask is None else np.nan
        if self.pixels is None:
            return np.full((score_h, score_w), empty_score, np.float32)

        left, top, right, bottom = self.roi
        # Include every legal window which can see a nonzero pixel. Match
        # from the same logical origin even if the ROI touches an image edge.
        x0, y0 = max(0, left - tw + 1), max(0, top - th + 1)
        x1, y1 = min(score_w, right), min(score_h, bottom)
        crop_w, crop_h = x1 + tw - 1 - x0, y1 + th - 1 - y0
        if crop_w * crop_h > 512**2:
            return correlate_bounded(self, template, mask=mask, output_bounds=(x0, y0, x1, y1))
        if 4 * crop_w * crop_h >= 3 * width * height:
            # Padding and restoring scores has little payoff near full size.
            return cv2.matchTemplate(self.as_dense(), template, cv2.TM_CCOEFF_NORMED, mask=mask)
        region = np.zeros((crop_h, crop_w, 3), np.uint8)
        region[top - y0 : bottom - y0, left - x0 : right - x0] = self.pixels
        scores = np.full((score_h, score_w), empty_score, np.float32)
        scores[y0:y1, x0:x1] = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        return scores

    def correlation_bounds(self, template_shape):
        """All output centers whose windows can intersect nonzero pixels."""
        if self.pixels is None:
            return (0, 0, 0, 0)
        th, tw = template_shape
        height, width, _ = self.shape
        left, top, right, bottom = self.roi
        return max(0, left - tw + 1), max(0, top - th + 1), min(width - tw + 1, right), min(height - th + 1, bottom)

    def __getitem__(self, slices):
        rows, columns = slices
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        result = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        if self.pixels is not None:
            left, top, right, bottom = self.roi
            ix0, iy0, ix1, iy1 = max(left, x0), max(top, y0), min(right, x1), min(bottom, y1)
            if ix0 < ix1 and iy0 < iy1:
                result[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = self.pixels[
                    iy0 - top : iy1 - top, ix0 - left : ix1 - left
                ]
        return result


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
        self._alpha_only = False

    def alpha_image(self):
        """Independent bounded view retaining native alpha and resize phase."""
        view = NativeTileImage(
            self.root,
            self.metadata,
            scale=self._scale_xy,
            match_size=(self.shape[1], self.shape[0]),
            alpha=self._alpha,
            cache_bytes=8 * 1024**2 // 3,
        )
        view._alpha_only = True
        view._match_shape = (*view._match_shape[:2], 1)
        return view

    def _read_tile(self, record):
        return self._read(
            record, mask_alpha=self._alpha, require_bgr=record.get("require_bgr", False), alpha_only=self._alpha_only
        )

    def _tile_cache_key(self, coordinates, record):
        # Equal content-addressed objects (often transparent) can occur at
        # several positions. Keep the expected shape in the cache identity.
        return ("object", record["path"], *record["shape"], record.get("require_bgr", False))

    def resource_records(self):
        for tile in self.metadata["tiles"].values():
            if tile is not None:
                yield tile.get("payload", tile)
        for record in (*self.metadata["overviews"].values(), self.metadata["proposal"]):
            if record is not None:
                resource = record.get("crop", record)
                if resource is not None:
                    yield resource

    def verify_sparse_payloads(self, checked):
        """Decode v3 assets offline and check the zero-padding contract."""
        for tile in self.metadata["tiles"].values():
            if tile is None:
                continue
            payload = tile["payload"]
            key = (payload["path"], *payload["shape"], self._alpha, payload.get("require_bgr", False))
            if key not in checked:
                pixels = self._read_tile(payload)
                nonzero = pixels[:, :, 0] | pixels[:, :, 1] | pixels[:, :, 2]
                x, y, width, height = cv2.boundingRect(nonzero)
                checked[key] = (x, y, x + width, y + height)
            left, top, right, bottom = checked[key]
            ox, oy = tile["origin"]
            if (left + ox, top + oy, right + ox, bottom + oy) != tile["roi"]:
                raise ValueError(f"Native tile ROI disagrees with matching pixels: {payload['path']}")
        for record in (*self.metadata["overviews"].values(), self.metadata["proposal"]):
            if record is None or record["crop"] is None:
                continue
            payload = record["crop"]
            key = (payload["path"], *payload["shape"])
            if key not in checked:
                self._read(payload)
                checked[key] = True

    def _search_raster(self, record):
        if "roi" not in record:
            return self._read(record)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        crop = None if record["crop"] is None else self._read(record["crop"])
        height, width = record["shape"]
        if crop is not None and record["roi"] == (0, 0, width, height):
            return crop
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        return ZeroPaddedSearchRaster((height, width, 3), record["roi"], crop)

    def _search_image(self, record):
        raster = self._search_raster(record)
        if isinstance(raster, ZeroPaddedSearchRaster):
            image = raster.as_dense()
            with self._lock:
                if self._closed:
                    raise ValueError("tiled image closed")
            return image
        return raster

    def _overview_record(self, scale):
        record = self.metadata["overviews"].get(float(scale).hex())
        if record is None:
            raise UnpreparedTileOverview(f"Localization tile package has no global search image for scale {scale!r}")
        return record

    def coarse_overview(self, scale):
        return self._search_raster(self._overview_record(scale))

    def overview(self, scale):
        return self._search_image(self._overview_record(scale))

    def proposal(self):
        record = self.metadata["proposal"]
        if record is None:
            raise ValueError("floor proposal image is missing")
        return self._search_image(record)

    def _read_region(self, y0, y1, x0, x1):
        if not self.metadata.get("sparse_native_tiles"):
            return super()._read_region(y0, y1, x0, x1)
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        result = np.zeros((max(0, y1 - y0), max(0, x1 - x0), self.shape[2]), np.uint8)
        if result.size == 0:
            return result
        edge = self.tile_size
        for ty in range(y0 // edge, (y1 - 1) // edge + 1):
            for tx in range(x0 // edge, (x1 - 1) // edge + 1):
                coordinates = f"{tx},{ty}"
                tile = self.metadata["tiles"][coordinates]
                if tile is None:
                    continue
                ox, oy = tx * edge, ty * edge
                left, top, right, bottom = tile["roi"]
                left, top = max(x0, ox + left), max(y0, oy + top)
                right, bottom = min(x1, ox + right), min(y1, oy + bottom)
                if left >= right or top >= bottom:
                    continue
                payload = tile["payload"]
                key = self._tile_cache_key(coordinates, payload)
                pixels = self._cached_pixels(key)
                if pixels is None:
                    pixels = self._remember_pixels(key, self._read_tile(payload))
                px, py = tile["origin"]
                px, py = ox + px, oy + py
                result[top - y0 : bottom - y0, left - x0 : right - x0] = pixels[
                    top - py : bottom - py, left - px : right - px
                ]
        with self._lock:
            if self._closed:
                raise ValueError("tiled image closed")
        return result

    def __getitem__(self, slices):
        if self._scale_xy == (1.0, 1.0):
            return super().__getitem__(slices)
        rows, columns = slices
        if rows.step not in (None, 1) or columns.step not in (None, 1):
            raise ValueError("tiled image requires contiguous regions")
        y0, y1, _ = rows.indices(self.shape[0])
        x0, x1, _ = columns.indices(self.shape[1])
        if y0 >= y1 or x0 >= x1:
            return np.empty((max(0, y1 - y0), max(0, x1 - x0), self.shape[2]), np.uint8)
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
                key = ("aligned", top, bottom, left, right)
                normalized = self._cached_pixels(key)
                if normalized is None:
                    source = self._read_region(top, bottom, left, right)
                    normalized = cv2.resize(source, None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
                    if normalized.ndim == 2:
                        normalized = normalized[:, :, None]
                    normalized.setflags(write=False)
                    self._remember_pixels(key, normalized)
                return normalized[y:out_bottom, x:out_right]
        key = ("region", y0, y1, x0, x1)
        cached = self._cached_pixels(key)
        if cached is not None:
            return cached
        area = fx <= 1 and fy <= 1
        xs = _axis_taps(x0, x1, fx, w, area)
        ys = _axis_taps(y0, y1, fy, h, area)
        left, top = xs[0][0][0], ys[0][0][0]
        right = max(indices[-1] for indices, _ in xs) + 1
        bottom = max(indices[-1] for indices, _ in ys) + 1
        source = self._read_region(top, bottom, left, right)
        horizontal = np.zeros((bottom - top, x1 - x0, self.shape[2]), np.float32)
        for indices, weights in xs:
            horizontal += source[:, indices - left] * weights.astype(np.float32)[None, :, None]
        output = np.zeros((y1 - y0, x1 - x0, self.shape[2]), np.float32)
        for indices, weights in ys:
            output += horizontal[indices - top] * weights.astype(np.float32)[:, None, None]
        normalized = np.rint(output, out=output).astype(np.uint8)
        normalized.setflags(write=False)
        return self._remember_pixels(key, normalized)

    @property
    def shape(self):
        return self._match_shape
