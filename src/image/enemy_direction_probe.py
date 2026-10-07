"""Fast elliptical enemy-direction marker probe for combat camera recovery."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_DIRECTION_BINS = 720
_DIRECTION_BIN_DEG = 360.0 / _DIRECTION_BINS
_DIRECTION_SMOOTH_HALF_WINDOW = 2
_DIRECTION_MIN_RED = 18.0
_DIRECTION_MIN_ARC_DEG = 5.0
_DIRECTION_MAX_ARC_DEG = 20.0
_MARKER_ARC_WIDTH_DEG = 8.0
_SINGLE_MARKER_RUN_DEG = 13.5
_DOUBLE_MARKER_MIN_RUN_DEG = 15.0
_ELLIPSE_AXES_1080 = (349.0, 245.0)
_ELLIPSE_ANNULUS_SCALE = (0.98, 1.02)
_ELLIPSE_RADIAL_SAMPLES = 9


@dataclass(frozen=True)
class EnemyDirectionMarker:
    """One fixed-width red direction marker recovered from the ellipse annulus."""

    angle_deg: float
    parameter_angle_deg: float
    score: float
    arc_width_deg: float = _MARKER_ARC_WIDTH_DEG


@dataclass(frozen=True)
class EnemyDirectionObservation:
    """Strongest direction plus all marker arcs detected in the same frame."""

    angle_deg: float
    score: float
    markers: tuple[EnemyDirectionMarker, ...] = ()


@dataclass(frozen=True)
class _EllipseSample:
    ys: np.ndarray
    xs: np.ndarray
    semi_axis_x: float
    semi_axis_y: float


def _ellipse_axes(width: int, height: int) -> tuple[float, float]:
    del width
    scale = height / 1080.0
    return _ELLIPSE_AXES_1080[0] * scale, _ELLIPSE_AXES_1080[1] * scale


def _parameter_to_direction_deg(parameter_angle_deg: float, semi_axis_x: float, semi_axis_y: float) -> float:
    radians = np.deg2rad(parameter_angle_deg)
    dx = semi_axis_x * np.cos(radians)
    dy = semi_axis_y * np.sin(radians)
    angle = float(np.degrees(np.arctan2(dy, dx)))
    if angle > 180.0:
        angle -= 360.0
    return angle


def _direction_to_parameter_deg(angle_deg: float, semi_axis_x: float, semi_axis_y: float) -> float:
    radians = np.deg2rad(angle_deg)
    parameter = float(
        np.degrees(
            np.arctan2(
                semi_axis_x * np.sin(radians),
                semi_axis_y * np.cos(radians),
            )
        )
    )
    return parameter % 360.0


@lru_cache(maxsize=12)
def _ellipse_sample(width: int, height: int) -> _EllipseSample:
    cx = width / 2.0
    cy = height / 2.0
    semi_axis_x, semi_axis_y = _ellipse_axes(width, height)
    angles = np.arange(_DIRECTION_BINS, dtype=np.float32) * (2.0 * np.pi / _DIRECTION_BINS)
    radial = np.linspace(
        _ELLIPSE_ANNULUS_SCALE[0],
        _ELLIPSE_ANNULUS_SCALE[1],
        _ELLIPSE_RADIAL_SAMPLES,
        dtype=np.float32,
    )[:, None]
    cos_angle = np.cos(angles)[None, :]
    sin_angle = np.sin(angles)[None, :]
    xs = np.rint(cx + radial * semi_axis_x * cos_angle).astype(np.int32)
    ys = np.rint(cy + radial * semi_axis_y * sin_angle).astype(np.int32)
    np.clip(xs, 0, max(0, width - 1), out=xs)
    np.clip(ys, 0, max(0, height - 1), out=ys)
    return _EllipseSample(ys=ys, xs=xs, semi_axis_x=semi_axis_x, semi_axis_y=semi_axis_y)


def _red_profile(frame: np.ndarray, sample: _EllipseSample) -> np.ndarray:
    pixels = frame[sample.ys, sample.xs]
    blue = pixels[..., 0].astype(np.int16)
    green = pixels[..., 1].astype(np.int16)
    red = pixels[..., 2].astype(np.int16)
    chroma = np.maximum(red - np.maximum(green, blue), 0).astype(np.float32)
    weighted = chroma * (red.astype(np.float32) / 255.0)
    return np.max(weighted, axis=0)


def _circular_smooth(profile: np.ndarray) -> np.ndarray:
    half = _DIRECTION_SMOOTH_HALF_WINDOW
    padded = np.concatenate((profile[-half:], profile, profile[:half]))
    kernel = np.full(2 * half + 1, 1.0 / (2 * half + 1), dtype=np.float32)
    return np.convolve(padded, kernel, mode="same")[half:-half]


def _circular_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    count = int(mask.size)
    if count == 0 or not bool(np.any(mask)):
        return []
    if bool(np.all(mask)):
        return [(0, count)]

    first_false = int(np.flatnonzero(~mask)[0])
    origin = (first_false + 1) % count
    runs: list[tuple[int, int]] = []
    offset = 0
    while offset < count:
        index = (origin + offset) % count
        if not bool(mask[index]):
            offset += 1
            continue
        start = offset
        while offset < count and bool(mask[(origin + offset) % count]):
            offset += 1
        runs.append(((origin + start) % count, offset - start))
    return runs


def _window_score(profile: np.ndarray, center_parameter_deg: float) -> float:
    half_bins = max(1, round((_MARKER_ARC_WIDTH_DEG / _DIRECTION_BIN_DEG) / 2.0))
    center_bin = round((center_parameter_deg % 360.0) / _DIRECTION_BIN_DEG) % _DIRECTION_BINS
    offsets = np.arange(-half_bins, half_bins + 1, dtype=np.int32)
    indices = (center_bin + offsets) % _DIRECTION_BINS
    return float(np.mean(profile[indices]))


def _run_center_parameter(profile: np.ndarray, start: int, length: int) -> float:
    indices = (start + np.arange(length, dtype=np.int32)) % _DIRECTION_BINS
    parameters = indices.astype(np.float32) * _DIRECTION_BIN_DEG
    radians = np.deg2rad(parameters)
    weights = np.maximum(profile[indices], 1e-6)
    vector = np.sum(weights * np.exp(1j * radians))
    if abs(vector) < 1e-6:
        return float((parameters[0] + (length - 1) * _DIRECTION_BIN_DEG / 2.0) % 360.0)
    return float(np.degrees(np.angle(vector)) % 360.0)


def _marker_candidates(profile: np.ndarray, sample: _EllipseSample) -> tuple[EnemyDirectionMarker, ...]:
    active = profile >= _DIRECTION_MIN_RED
    runs = []
    for start, length in _circular_runs(active):
        span_deg = length * _DIRECTION_BIN_DEG
        if _DIRECTION_MIN_ARC_DEG <= span_deg <= _DIRECTION_MAX_ARC_DEG:
            runs.append((start, length, span_deg))
    if not runs:
        return ()

    markers: list[EnemyDirectionMarker] = []
    for start, length, span_deg in runs:
        start_parameter = start * _DIRECTION_BIN_DEG
        if span_deg >= _DOUBLE_MARKER_MIN_RUN_DEG:
            # Anti-aliasing plus five-point smoothing expands one fixed eight-degree
            # primitive to roughly 12.5-14.5 degrees. Use the midpoint of that
            # observed footprint to recover overlapping primitive centers instead
            # of anchoring them to the widened run edges with the semantic 8° width.
            half_single_run = _SINGLE_MARKER_RUN_DEG / 2.0
            first = (start_parameter + half_single_run) % 360.0
            second = (start_parameter + span_deg - half_single_run) % 360.0
            parameters = (first, second)
        else:
            parameters = (_run_center_parameter(profile, start, length),)

        for parameter in parameters:
            angle = _parameter_to_direction_deg(parameter, sample.semi_axis_x, sample.semi_axis_y)
            markers.append(
                EnemyDirectionMarker(
                    angle_deg=angle,
                    parameter_angle_deg=float(parameter),
                    score=_window_score(profile, parameter),
                )
            )

    markers.sort(key=lambda marker: marker.parameter_angle_deg)
    return tuple(markers)


def probe_enemy_direction_fast(frame: np.ndarray | None) -> EnemyDirectionObservation | None:
    """Return the strongest red off-screen marker from a thin fitted ellipse annulus.

    The hot path samples only nine radial points for each of 720 ellipse angles.
    Marker arcs are modeled as a fixed eight-degree primitive. A run that reaches
    the fixed merged-marker threshold is interpreted as two overlapping
    primitives, so clipped short runs cannot change the split behavior.
    """
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3 or frame.shape[2] < 3:
        return None

    height, width = frame.shape[:2]
    sample = _ellipse_sample(width, height)
    profile = _circular_smooth(_red_profile(frame, sample))
    markers = _marker_candidates(profile, sample)
    if not markers:
        return None

    best = max(markers, key=lambda marker: marker.score)
    return EnemyDirectionObservation(angle_deg=best.angle_deg, score=best.score, markers=markers)
