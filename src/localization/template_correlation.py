"""Exact template search with bounded native correlation scratch memory."""

import math

import cv2
import numpy as np


class _ColorZnccKernel:
    """Binary-mask ZNCC: share image transforms, keep one bounded tile alive.

    For each channel, the numerator is corr(I, M*(T-mean(T))). The image
    variance is corr(sum(I**2), M) - sum(corr(Ic, M)**2)/sum(M).
    Binary M**2 == M eliminates the repeated weighted-mask correlations.
    Double precision prevents cancellation on low-variance terrain.
    """

    def __init__(self, template, masks, *, size=None):
        self.th, self.tw = template.shape[:2]
        self.size = size or max(256, 1 << (2 * max(self.th, self.tw) - 1).bit_length())
        self._plane = np.empty((self.size, self.size), np.float64)
        self.groups = []
        for mask in masks:
            selected = np.ones((self.th, self.tw), bool) if mask is None else mask != 0
            count = int(selected.sum())
            centered = template.astype(np.float64)
            if count:
                centered -= centered[selected].mean(axis=0)
            centered[~selected] = 0
            norm = float(np.sqrt(np.sum(centered * centered)))
            self.groups.append(
                (
                    mask is None,
                    count,
                    norm,
                    None if mask is None else self.fft(selected),
                    tuple(self.fft(centered[:, :, c]) for c in range(3)),
                )
            )
        # Scratch belongs to this correlation call, never to a frame/map cache.
        # Reuse it across blocks and channels without retaining image spectra.
        self._spectrum, self._product, self._inverse, self._squares = (np.empty_like(self._plane) for _ in range(4))
        self._numerators = [np.empty_like(self._plane) for _ in self.groups]
        shape = (self.size - self.th + 1, self.size - self.tw + 1)
        self._moments = [np.empty(shape, np.float64) for _ in self.groups]
        self._combined, self._variance, self._scores, self._moment = (np.empty(shape, np.float64) for _ in range(4))
        self._valid = np.empty(shape, bool)

    def fft(self, pixels):
        self._plane.fill(0)
        self._plane[: pixels.shape[0], : pixels.shape[1]] = pixels
        return cv2.dft(self._plane)

    def inverse(self, spectrum, shape):
        cv2.idft(spectrum, dst=self._inverse, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE)
        return self._inverse[: shape[0], : shape[1]]

    def box_sum(self, pixels, shape):
        return cv2.boxFilter(
            pixels,
            cv2.CV_64F,
            (self.tw, self.th),
            dst=self._inverse,
            normalize=False,
            anchor=(0, 0),
            borderType=cv2.BORDER_CONSTANT,
        )[: shape[0], : shape[1]]

    def match(self, image):
        shape = (image.shape[0] - self.th + 1, image.shape[1] - self.tw + 1)
        region = np.s_[: shape[0], : shape[1]]
        mean_squares = [moments[region] for moments in self._moments]
        for moments in mean_squares:
            moments.fill(0)
        self._squares.fill(0)
        moment = self._moment[region]
        for channel in range(3):
            self._plane.fill(0)
            self._plane[: image.shape[0], : image.shape[1]] = image[:, :, channel]
            np.multiply(self._plane, self._plane, out=self._product)
            self._squares += self._product
            spectrum = cv2.dft(self._plane, dst=self._spectrum)
            for index, (full, count, norm, mask_spectrum, templates) in enumerate(self.groups):
                if not count or norm < 1e-12:
                    continue
                if channel == 0:
                    cv2.mulSpectrums(spectrum, templates[channel], 0, c=self._numerators[index], conjB=True)
                else:
                    cv2.mulSpectrums(spectrum, templates[channel], 0, c=self._product, conjB=True)
                    self._numerators[index] += self._product
                sums = (
                    self.box_sum(self._plane, shape)
                    if full
                    else self.inverse(cv2.mulSpectrums(spectrum, mask_spectrum, 0, c=self._product, conjB=True), shape)
                )
                if not full:
                    # Binary masks over uint8 pixels have integer moments.
                    # Recover them before subtracting nearly equal variances.
                    np.rint(sums, out=sums)
                np.multiply(sums, sums, out=moment)
                moment /= count
                mean_squares[index] += moment
        square_spectrum = (
            cv2.dft(self._squares, dst=self._spectrum) if any(not group[0] for group in self.groups) else None
        )
        combined = self._combined[region]
        combined.fill(0)
        variance, scores, valid = self._variance[region], self._scores[region], self._valid[region]
        for index, (full, count, norm, mask_spectrum, _) in enumerate(self.groups):
            if not count or norm < 1e-12:
                # OpenCV 4.12: constant unmasked templates score 1; masked
                # zero norms are nonfinite and the caller maps them to -1.
                combined += 1.0 if full else -1.0
                continue
            second = (
                self.box_sum(self._squares, shape)
                if full
                else self.inverse(
                    cv2.mulSpectrums(square_spectrum, mask_spectrum, 0, c=self._product, conjB=True), shape
                )
            )
            if not full:
                np.rint(second, out=second)
            np.subtract(second, mean_squares[index], out=variance)
            # uint8 pixels and a binary mask have a smallest positive variance
            # >= (count-1)/count. This epsilon only removes FFT roundoff at zero.
            np.greater(variance, 1e-6, out=valid)
            np.maximum(variance, 0, out=variance)
            np.sqrt(variance, out=variance)
            variance *= norm
            numerator = self.inverse(self._numerators[index], shape)
            scores.fill(0.0 if full else -1.0)
            np.divide(numerator, variance, out=scores, where=valid)
            np.clip(scores, -1, 1, out=scores)
            combined += scores
        combined /= len(self.groups)
        return combined.astype(np.float32)


def correlate_color_zncc(image, template, *, masks=(None,), allowed=None, output_bounds=None, compact=False):
    """Color competition with shared bounded FFT scratch.

    No image spectra survive the call. Every legal center and its complete
    template halo is evaluated; sparse zero windows retain sanitized scores.
    """
    if template.dtype != np.uint8 or image.shape[2:] != (3,) or template.shape[2:] != (3,):
        raise ValueError("color ZNCC requires uint8 BGR pixels")
    if isinstance(image, np.ndarray) and image.dtype != np.uint8:
        raise ValueError("color ZNCC requires uint8 BGR pixels")
    if any(mask is not None and (mask.dtype != np.uint8 or mask.shape != template.shape[:2]) for mask in masks):
        raise ValueError("color ZNCC requires uint8 binary masks")
    if not masks:
        raise ValueError("color ZNCC requires a scoring mask")
    th, tw = template.shape[:2]
    sh, sw = image.shape[0] - th + 1, image.shape[1] - tw + 1
    if sh <= 0 or sw <= 0:
        raise ValueError("template is larger than its search image")
    if allowed is not None and allowed.shape != (sh, sw):
        raise ValueError("candidate mask shape differs from score grid")
    if max(th, tw) > 256:
        # Unusual capture scales retain the bounded OpenCV path rather than
        # allocating an unbounded set of large template spectra.
        combined = np.zeros((sh, sw), np.float32)
        for mask in masks:
            part = correlate_bounded(image, template, mask=mask, allowed=allowed, output_bounds=output_bounds)
            np.nan_to_num(part, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
            combined += part / len(masks)
        return combined
    left, top, right, bottom = output_bounds or (0, 0, sw, sh)
    size = None
    if compact:
        if allowed is not None:
            active = allowed[top:bottom, left:right]
            rows, cols = np.flatnonzero(active.any(axis=1)), np.flatnonzero(active.any(axis=0))
            if rows.size:
                left, top, right, bottom = left + cols[0], top + rows[0], left + cols[-1] + 1, top + rows[-1] + 1
            else:
                right, bottom = left, top
        # A tiny refinement window need not pad a 143-pixel template to 512.
        # Choose bounded smooth FFT sizes by work, including template setup.
        width, height = max(1, right - left), max(1, bottom - top)
        candidates = {256, 512, cv2.getOptimalDFTSize(min(512, max(width + tw - 1, height + th - 1)))}
        size = min(
            (value for value in candidates if value >= max(th, tw)),
            key=lambda value: (
                (math.ceil(width / (value - tw + 1)) * math.ceil(height / (value - th + 1)) + 1)
                * value**2
                * math.log2(value)
            ),
        )
    kernel = _ColorZnccKernel(template, masks, size=size)
    zero = sum((1.0 if norm < 1e-12 else 0.0) if full else -1.0 for full, _, norm, _, _ in kernel.groups)
    scores = np.full((sh, sw), zero / len(masks), np.float32)
    bw, bh = kernel.size - tw + 1, kernel.size - th + 1
    for y in range(top, bottom, bh):
        for x in range(left, right, bw):
            bx, by, ex, ey = x, y, min(x + bw, right), min(y + bh, bottom)
            if allowed is not None:
                active = allowed[by:ey, bx:ex]
                rows, cols = np.flatnonzero(active.any(axis=1)), np.flatnonzero(active.any(axis=0))
                if not rows.size:
                    continue
                bx, by, ex, ey = x + cols[0], y + rows[0], x + cols[-1] + 1, y + rows[-1] + 1
            scores[by:ey, bx:ex] = kernel.match(image[by : ey + th - 1, bx : ex + tw - 1])
    return scores


def correlate_bounded(
    image, template, *, mask=None, block_size=512, output_bounds=None, allowed=None, method=cv2.TM_CCOEFF_NORMED
):
    """Evaluate every legal window, including windows crossing block edges.

    Blocks partition output coordinates, not input pixels. Their template-size
    halos preserve the original pixel phase and do not prune any candidate.
    """
    th, tw = template.shape[:2]
    sh, sw = image.shape[0] - th + 1, image.shape[1] - tw + 1
    if sh <= 0 or sw <= 0:
        raise ValueError("template is larger than its search image")
    if output_bounds is None:
        left, top, right_limit, bottom_limit = 0, 0, sw, sh
        scores = np.empty((sh, sw), np.float32)
    else:
        # Sparse rasters know which windows can intersect stored pixels. All
        # other windows are constant zero; retain OpenCV's score convention.
        left, top, right_limit, bottom_limit = output_bounds
        scores = np.full((sh, sw), 0.0 if mask is None else np.nan, np.float32)
    if allowed is not None:
        if allowed.shape != (sh, sw):
            raise ValueError("candidate mask shape differs from score grid")
        scores.fill(np.nan)
    for y in range(top, bottom_limit, block_size):
        bottom = min(bottom_limit, y + block_size)
        for x in range(left, right_limit, block_size):
            right = min(right_limit, x + block_size)
            bx, by, ex, ey = x, y, right, bottom
            if allowed is not None:
                active = allowed[y:bottom, x:right]
                rows, cols = np.flatnonzero(active.any(axis=1)), np.flatnonzero(active.any(axis=0))
                if not rows.size:
                    continue
                bx, by, ex, ey = x + cols[0], y + rows[0], x + cols[-1] + 1, y + rows[-1] + 1
            # Crop OUTPUT centers only. Every retained window still includes
            # all terrain pixels, including the dim background outside alpha.
            region = image[by : ey + th - 1, bx : ex + tw - 1]
            scores[by:ey, bx:ex] = cv2.matchTemplate(region, template, method, mask=mask)
    return scores
