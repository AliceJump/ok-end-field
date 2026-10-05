"""Fast screen-edge enemy-direction marker probe for combat camera recovery."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_DIRECTION_BINS = 720
_DIRECTION_SAMPLE_STEP = 2
_DIRECTION_SMOOTH_HALF_WINDOW = 6
_DIRECTION_MIN_CONTRAST = 10.0

# The supplied 1920x1080 combat screenshots place the bright inner edge of the
# red off-screen enemy marker around r=344..361 px from screen center. Use that
# thin ring as signal and compare it against neighboring rings so broad red VFX
# spanning the scene is cancelled instead of mistaken for the HUD marker.
_TARGET_RING = (335 / 1080, 370 / 1080)
_INNER_REFERENCE_RING = (290 / 1080, 320 / 1080)
_OUTER_REFERENCE_RING = (390 / 1080, 420 / 1080)


@dataclass(frozen=True)
class EnemyDirectionObservation:
    """One strongest off-screen enemy direction marker observation."""

    angle_deg: float
    score: float


@dataclass(frozen=True)
class _RingSample:
    ys: np.ndarray
    xs: np.ndarray
    angle_bins: np.ndarray
    bin_counts: np.ndarray


def _build_ring_sample(width: int, height: int, ring: tuple[float, float]) -> _RingSample:
    cx = width / 2.0
    cy = height / 2.0
    r1 = ring[0] * height
    r2 = ring[1] * height
    margin = int(np.ceil(r2)) + _DIRECTION_SAMPLE_STEP

    x1 = max(0, int(np.floor(cx - margin)))
    x2 = min(width - 1, int(np.ceil(cx + margin)))
    y1 = max(0, int(np.floor(cy - margin)))
    y2 = min(height - 1, int(np.ceil(cy + margin)))

    xs = np.arange(x1, x2 + 1, _DIRECTION_SAMPLE_STEP, dtype=np.int32)
    ys = np.arange(y1, y2 + 1, _DIRECTION_SAMPLE_STEP, dtype=np.int32)
    xx, yy = np.meshgrid(xs, ys)
    dx = xx.astype(np.float32) - cx
    dy = yy.astype(np.float32) - cy
    radius = np.hypot(dx, dy)
    mask = (radius >= r1) & (radius <= r2)

    sample_x = xx[mask].astype(np.int32, copy=False)
    sample_y = yy[mask].astype(np.int32, copy=False)
    angle = (np.degrees(np.arctan2(dy[mask], dx[mask])) + 360.0) % 360.0
    angle_bins = np.floor(angle / 360.0 * _DIRECTION_BINS).astype(np.int16)
    angle_bins %= _DIRECTION_BINS
    counts = np.bincount(angle_bins, minlength=_DIRECTION_BINS).astype(np.float32)
    counts[counts == 0] = 1.0
    return _RingSample(sample_y, sample_x, angle_bins, counts)


@lru_cache(maxsize=12)
def _ring_samples(width: int, height: int) -> tuple[_RingSample, _RingSample, _RingSample]:
    return (
        _build_ring_sample(width, height, _TARGET_RING),
        _build_ring_sample(width, height, _INNER_REFERENCE_RING),
        _build_ring_sample(width, height, _OUTER_REFERENCE_RING),
    )


def _red_profile(frame: np.ndarray, sample: _RingSample) -> np.ndarray:
    pixels = frame[sample.ys, sample.xs]
    blue = pixels[:, 0].astype(np.int16)
    green = pixels[:, 1].astype(np.int16)
    red = pixels[:, 2].astype(np.int16)

    # Red chroma instead of raw R suppresses gray/white HUD while retaining the
    # translucent marker. Weight by brightness so dim reddish terrain contributes
    # less than the bright red inner edge.
    chroma = np.maximum(red - np.maximum(green, blue), 0).astype(np.float32)
    weights = chroma * (red.astype(np.float32) / 255.0)
    sums = np.bincount(sample.angle_bins, weights=weights, minlength=_DIRECTION_BINS)
    return sums / sample.bin_counts


def _circular_smooth(profile: np.ndarray) -> np.ndarray:
    half = _DIRECTION_SMOOTH_HALF_WINDOW
    padded = np.concatenate((profile[-half:], profile, profile[:half]))
    kernel = np.full(2 * half + 1, 1.0 / (2 * half + 1), dtype=np.float32)
    return np.convolve(padded, kernel, mode="same")[half:-half]


def probe_enemy_direction_fast(frame: np.ndarray | None) -> EnemyDirectionObservation | None:
    """Return the strongest red off-screen enemy direction marker, if present.

    The hot path samples about one quarter of the pixels from three narrow fixed
    rings and never performs a full-frame color conversion, contour search or
    OCR. Broad red combat effects are reduced by subtracting the neighboring
    inner/outer ring profiles from the marker ring profile.
    """
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3 or frame.shape[2] < 3:
        return None

    height, width = frame.shape[:2]
    target_sample, inner_sample, outer_sample = _ring_samples(width, height)

    target = _circular_smooth(_red_profile(frame, target_sample))
    inner = _circular_smooth(_red_profile(frame, inner_sample))
    outer = _circular_smooth(_red_profile(frame, outer_sample))
    contrast = target - 0.5 * (inner + outer)

    previous = np.roll(contrast, 1)
    following = np.roll(contrast, -1)
    peak_mask = (contrast >= _DIRECTION_MIN_CONTRAST) & (contrast > previous) & (contrast >= following)
    peak_indices = np.flatnonzero(peak_mask)
    if peak_indices.size == 0:
        return None

    best = int(peak_indices[np.argmax(contrast[peak_indices])])
    angle = best * 360.0 / _DIRECTION_BINS
    if angle > 180.0:
        angle -= 360.0
    return EnemyDirectionObservation(angle_deg=float(angle), score=float(contrast[best]))
