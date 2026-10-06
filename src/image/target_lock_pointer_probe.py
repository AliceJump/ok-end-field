"""Fast detector for the directional pointer shown while holding middle mouse."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_DIRECTION_BINS = 720
_DIRECTION_BIN_DEG = 360.0 / _DIRECTION_BINS
_DIRECTION_SMOOTH_HALF_WINDOW = 5
_POINTER_CENTER_Y_1080 = 476.25
_POINTER_DOT_RADIUS_1080 = 5.0
_POINTER_DARK_RING_1080 = (7.5, 15.0)
_POINTER_INNER_RADII_1080 = (10.0, 17.5)
_POINTER_OUTER_RADII_1080 = (20.0, 38.75)
_POINTER_INNER_SAMPLES = 10
_POINTER_OUTER_SAMPLES = 20
_POINTER_INNER_WEIGHT = 0.8
_POINTER_MIN_DOT_WHITE = 70.0
_POINTER_MIN_CENTER_CONTRAST = 35.0
_POINTER_MIN_DIRECTION_SCORE = 60.0


@dataclass(frozen=True)
class TargetLockPointerObservation:
    """Direction encoded by the center target-lock pointer.

    ``angle_deg`` uses the same screen-space convention as ``enemy_direction_probe``:
    0° points right, +90° points down, -90° points up, and ±180° points left.
    """

    angle_deg: float
    score: float
    center_contrast: float


@dataclass(frozen=True)
class _PointerSample:
    dot_ys: np.ndarray
    dot_xs: np.ndarray
    ring_ys: np.ndarray
    ring_xs: np.ndarray
    outer_ys: np.ndarray
    outer_xs: np.ndarray
    inner_ys: np.ndarray
    inner_xs: np.ndarray


def _pointer_center(width: int, height: int) -> tuple[float, float]:
    scale = height / 1080.0
    return width / 2.0, _POINTER_CENTER_Y_1080 * scale


def _ring_indices(
    width: int,
    height: int,
    center_x: float,
    center_y: float,
    min_radius: float,
    max_radius: float,
) -> tuple[np.ndarray, np.ndarray]:
    padding = int(np.ceil(max_radius)) + 1
    left = max(0, int(np.floor(center_x)) - padding)
    right = min(width, int(np.ceil(center_x)) + padding + 1)
    top = max(0, int(np.floor(center_y)) - padding)
    bottom = min(height, int(np.ceil(center_y)) + padding + 1)

    ys, xs = np.mgrid[top:bottom, left:right]
    radii = np.hypot(xs - center_x, ys - center_y)
    mask = (radii >= min_radius) & (radii <= max_radius)
    return ys[mask].astype(np.int32), xs[mask].astype(np.int32)


@lru_cache(maxsize=12)
def _pointer_sample(width: int, height: int) -> _PointerSample:
    center_x, center_y = _pointer_center(width, height)
    scale = height / 1080.0

    dot_ys, dot_xs = _ring_indices(
        width,
        height,
        center_x,
        center_y,
        0.0,
        _POINTER_DOT_RADIUS_1080 * scale,
    )
    ring_ys, ring_xs = _ring_indices(
        width,
        height,
        center_x,
        center_y,
        _POINTER_DARK_RING_1080[0] * scale,
        _POINTER_DARK_RING_1080[1] * scale,
    )

    angles = np.arange(_DIRECTION_BINS, dtype=np.float32) * (2.0 * np.pi / _DIRECTION_BINS)
    cos_angle = np.cos(angles)[None, :]
    sin_angle = np.sin(angles)[None, :]

    def radial_samples(radius_range: tuple[float, float], count: int) -> tuple[np.ndarray, np.ndarray]:
        radial = np.linspace(
            radius_range[0] * scale,
            radius_range[1] * scale,
            count,
            dtype=np.float32,
        )[:, None]
        xs = np.rint(center_x + radial * cos_angle).astype(np.int32)
        ys = np.rint(center_y + radial * sin_angle).astype(np.int32)
        np.clip(xs, 0, max(0, width - 1), out=xs)
        np.clip(ys, 0, max(0, height - 1), out=ys)
        return ys, xs

    outer_ys, outer_xs = radial_samples(_POINTER_OUTER_RADII_1080, _POINTER_OUTER_SAMPLES)
    inner_ys, inner_xs = radial_samples(_POINTER_INNER_RADII_1080, _POINTER_INNER_SAMPLES)
    return _PointerSample(
        dot_ys=dot_ys,
        dot_xs=dot_xs,
        ring_ys=ring_ys,
        ring_xs=ring_xs,
        outer_ys=outer_ys,
        outer_xs=outer_xs,
        inner_ys=inner_ys,
        inner_xs=inner_xs,
    )


def _neutral_white_response(pixels: np.ndarray) -> np.ndarray:
    """Prefer neutral white HUD pixels over bright colored scene content."""
    values = pixels[..., :3].astype(np.int16)
    blue = values[..., 0]
    green = values[..., 1]
    red = values[..., 2]
    low = np.minimum(np.minimum(blue, green), red).astype(np.float32)
    high = np.maximum(np.maximum(blue, green), red).astype(np.float32)
    return np.maximum(low - 0.5 * (high - low), 0.0)


def _circular_smooth(profile: np.ndarray) -> np.ndarray:
    half = _DIRECTION_SMOOTH_HALF_WINDOW
    padded = np.concatenate((profile[-half:], profile, profile[:half]))
    kernel = np.full(2 * half + 1, 1.0 / (2 * half + 1), dtype=np.float32)
    return np.convolve(padded, kernel, mode="valid")


def _refine_peak_angle(profile: np.ndarray, best_index: int) -> float:
    previous_value = float(profile[(best_index - 1) % _DIRECTION_BINS])
    best_value = float(profile[best_index])
    next_value = float(profile[(best_index + 1) % _DIRECTION_BINS])
    denominator = previous_value - 2.0 * best_value + next_value
    if abs(denominator) < 1e-6:
        offset = 0.0
    else:
        offset = 0.5 * (previous_value - next_value) / denominator
        offset = float(np.clip(offset, -0.5, 0.5))

    angle = (best_index + offset) * _DIRECTION_BIN_DEG
    if angle > 180.0:
        angle -= 360.0
    return float(angle)


def probe_target_lock_pointer(frame: np.ndarray | None) -> TargetLockPointerObservation | None:
    """Return the direction indicated by the center target-lock pointer.

    The pointer is a fixed HUD element made of a bright center dot inside a dark
    circular plate plus a neutral-white wedge on the plate's outer edge. The
    center-dot gate rejects frames where the pointer is absent. Direction scoring
    compares the outer white wedge against the same radial direction inside the
    dark plate, which suppresses broad white enemy outlines that cross the HUD.
    """
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3 or frame.shape[2] < 3:
        return None

    height, width = frame.shape[:2]
    sample = _pointer_sample(width, height)
    if sample.dot_ys.size == 0 or sample.ring_ys.size == 0:
        return None

    dot_white = float(np.mean(_neutral_white_response(frame[sample.dot_ys, sample.dot_xs])))
    ring_white = float(np.mean(_neutral_white_response(frame[sample.ring_ys, sample.ring_xs])))
    center_contrast = dot_white - ring_white
    if dot_white < _POINTER_MIN_DOT_WHITE or center_contrast < _POINTER_MIN_CENTER_CONTRAST:
        return None

    outer_profile = np.mean(
        _neutral_white_response(frame[sample.outer_ys, sample.outer_xs]),
        axis=0,
    )
    inner_profile = np.mean(
        _neutral_white_response(frame[sample.inner_ys, sample.inner_xs]),
        axis=0,
    )
    profile = _circular_smooth(outer_profile - _POINTER_INNER_WEIGHT * inner_profile)
    best_index = int(np.argmax(profile))
    score = float(profile[best_index])
    if score < _POINTER_MIN_DIRECTION_SCORE:
        return None

    return TargetLockPointerObservation(
        angle_deg=_refine_peak_angle(profile, best_index),
        score=score,
        center_contrast=center_contrast,
    )
