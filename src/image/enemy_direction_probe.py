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

_GRADIENT_HALF_ARC_DEG = 3.5
_GRADIENT_INNER_SCALES = (0.92, 0.94, 0.96)
_GRADIENT_OUTER_SCALES = (1.03, 1.05, 1.07, 1.09, 1.11)
_GRADIENT_MIN_SAMPLE_RED = 3.0
_GRADIENT_MIN_SUPPORT_SAMPLES = 3
_GRADIENT_MIN_EVIDENCE = 6.0

_MARKER_INNER_MAX_RED = 5.0
_MARKER_INNER_MAX_RATIO = 0.12
_MARKER_MIN_RIDGE_RED = 6.0
_MARKER_MIN_RING_RED = 40.0
_MARKER_MIN_ARC_SUPPORT = 2.0 / 3.0
_MARKER_OUTER_RIDGE_SCALES = (1.04, 1.06, 1.08)
_MARKER_FAR_SCALES = (0.90, 1.13, 1.15)
_MARKER_PROFILE_SCALES = tuple(0.94 + 0.005 * index for index in range(25))
# The fitted annulus occupies 0.98..1.02 within the 0.94..1.06 profile.
_MARKER_PROFILE_RING_BINS = (8, 17)
_MARKER_MIN_EDGE_RISE_RATIO = 0.25
_MARKER_TAIL_MAX_PEAK_RATIO = 0.95
_MARKER_MIN_TAIL_LEVELS = 3


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


def _weighted_red_chroma(pixels: np.ndarray) -> np.ndarray:
    blue = pixels[..., 0].astype(np.int16)
    green = pixels[..., 1].astype(np.int16)
    red = pixels[..., 2].astype(np.int16)
    chroma = np.maximum(red - np.maximum(green, blue), 0).astype(np.float32)
    return chroma * (red.astype(np.float32) / 255.0)


def _red_profile(frame: np.ndarray, sample: _EllipseSample) -> np.ndarray:
    pixels = frame[sample.ys, sample.xs]
    weighted = _weighted_red_chroma(pixels)
    return np.max(weighted, axis=0)


def _candidate_radial_red(
    frame: np.ndarray,
    sample: _EllipseSample,
    markers: tuple[EnemyDirectionMarker, ...],
) -> np.ndarray:
    """Sample all candidate strips together; return candidate/radius/angle red."""
    height, width = frame.shape[:2]
    cx = width / 2.0
    cy = height / 2.0
    half_bins = max(1, round(_GRADIENT_HALF_ARC_DEG / _DIRECTION_BIN_DEG))
    offsets = np.arange(-half_bins, half_bins + 1, dtype=np.float32) * _DIRECTION_BIN_DEG
    centers = np.asarray([marker.parameter_angle_deg for marker in markers], dtype=np.float32)
    parameters = np.deg2rad((centers[:, None] + offsets[None, :]) % 360.0)
    scales = np.concatenate(
        (
            np.linspace(*_ELLIPSE_ANNULUS_SCALE, _ELLIPSE_RADIAL_SAMPLES, dtype=np.float32),
            np.asarray(
                _GRADIENT_INNER_SCALES
                + _GRADIENT_OUTER_SCALES
                + _MARKER_OUTER_RIDGE_SCALES
                + _MARKER_FAR_SCALES
                + _MARKER_PROFILE_SCALES,
                dtype=np.float32,
            ),
        )
    )
    radial = scales[None, :, None]
    xs = np.rint(cx + radial * sample.semi_axis_x * np.cos(parameters)[:, None, :]).astype(np.int32)
    ys = np.rint(cy + radial * sample.semi_axis_y * np.sin(parameters)[:, None, :]).astype(np.int32)
    np.clip(xs, 0, max(0, width - 1), out=xs)
    np.clip(ys, 0, max(0, height - 1), out=ys)
    return _weighted_red_chroma(frame[ys, xs])


def _outward_gradient_evidence(
    inner: np.ndarray,
    outer: np.ndarray,
) -> np.ndarray:
    """Return each candidate's one-sided tail evidence from shared radial samples."""
    baseline = np.mean(inner, axis=(1, 2))
    adjusted = np.maximum(np.mean(outer, axis=2) - baseline[:, None], 0.0)
    support = np.count_nonzero(adjusted >= _GRADIENT_MIN_SAMPLE_RED, axis=1)
    far_support = np.any(adjusted[:, -2:] >= _GRADIENT_MIN_SAMPLE_RED, axis=1)
    evidence = np.mean(adjusted[:, :3], axis=1)
    return np.where(
        (support >= _GRADIENT_MIN_SUPPORT_SAMPLES) & far_support & (evidence >= _GRADIENT_MIN_EVIDENCE),
        evidence,
        0.0,
    )


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


def _verified_markers(
    frame: np.ndarray,
    sample: _EllipseSample,
    markers: tuple[EnemyDirectionMarker, ...],
) -> tuple[tuple[EnemyDirectionMarker, ...], EnemyDirectionMarker | None]:
    """Require a fitted bright arc before using tail evidence or ring-only fallback.

    A real marker has a bright edge at the ellipse, a quiet inward side, and a
    dimmer outward tail. Red scenery can have an outward gradient without that
    localized edge. Test the ridge across most of the arc rather than letting a
    few bright pixels validate an unrelated effect. Require the bright edge to
    rise sharply and continue into several weaker outward red levels. Weak far
    tails may use ring score, but uniform red bars cannot re-enter via fallback.
    """
    red = _candidate_radial_red(frame, sample, markers)
    ring_end = _ELLIPSE_RADIAL_SAMPLES
    inner_end = ring_end + len(_GRADIENT_INNER_SCALES)
    outer_end = inner_end + len(_GRADIENT_OUTER_SCALES)
    ridge_end = outer_end + len(_MARKER_OUTER_RIDGE_SCALES)
    ring = np.max(red[:, :ring_end], axis=1)
    inner = red[:, ring_end:inner_end]
    outer = red[:, inner_end:outer_end]
    inner_peak = np.max(inner, axis=1)
    outer_peak = np.max(red[:, outer_end:ridge_end], axis=1)
    ring_mean = np.mean(ring, axis=1)
    quiet = np.mean(inner_peak, axis=1) <= np.maximum(
        _MARKER_INNER_MAX_RED,
        ring_mean * _MARKER_INNER_MAX_RATIO,
    )
    ridge = ring - np.maximum(inner_peak, outer_peak)
    profile_start = ridge_end + len(_MARKER_FAR_SCALES)
    localized = ring >= np.max(red[:, ring_end:profile_start], axis=1)
    profile = red[:, profile_start:]
    # Mean across the angular strip before measuring the rise: unrelated edges
    # crossing different radii at each angle must not act like one fitted arc.
    mean_profile = np.mean(profile, axis=2)
    start_bin, end_bin = _MARKER_PROFILE_RING_BINS
    sharp = np.max(np.diff(mean_profile[:, :end_bin], axis=1), axis=1) >= (ring_mean * _MARKER_MIN_EDGE_RISE_RATIO)
    # Anchor to the outermost equal peak, so a thin antialiased bright plateau
    # does not consume the first few tail samples. Multiple intermediate levels
    # separate a fading translucent tail from a flat bar ending at background.
    peak_region = profile[:, start_bin:end_bin]
    peak = np.max(peak_region, axis=1)
    peak_index = end_bin - 1 - np.argmax(peak_region[:, ::-1], axis=1)
    tail_index = peak_index[:, None, :] + np.arange(1, 6)[None, :, None]
    tails = np.take_along_axis(profile, tail_index, axis=1)
    levels = np.count_nonzero(
        (tails >= _GRADIENT_MIN_SAMPLE_RED) & (tails < peak[:, None, :] * _MARKER_TAIL_MAX_PEAK_RATIO),
        axis=1,
    )
    required = int(np.ceil(ring.shape[1] * _MARKER_MIN_ARC_SUPPORT))
    shape = (
        quiet
        & sharp
        & (ring_mean >= _MARKER_MIN_RING_RED)
        & (np.count_nonzero((ridge >= _MARKER_MIN_RIDGE_RED) & localized, axis=1) >= required)
        & (np.count_nonzero(levels >= _MARKER_MIN_TAIL_LEVELS, axis=1) >= required)
    )
    if not np.any(shape):
        return (), None

    evidence = _outward_gradient_evidence(inner, outer)
    verified = shape & (evidence > 0.0)
    keep = verified if np.any(verified) else shape
    filtered = tuple(marker for marker, accepted in zip(markers, keep, strict=True) if accepted)
    if not np.any(verified):
        return filtered, None
    best_index = max(np.flatnonzero(verified), key=lambda i: (evidence[i], markers[i].score))
    best = markers[best_index]
    return filtered, best


def probe_enemy_direction_fast(frame: np.ndarray | None) -> EnemyDirectionObservation | None:
    """Return the strongest red off-screen marker from a fitted ellipse region.

    The hot path first samples only nine radial points for each of 720 ellipse
    angles. Candidate strips are sampled together to verify a localized bright
    edge and quiet inward side before looking for the outward translucent tail.
    A sharp edge followed by weaker outward red levels excludes uniform red
    bars. The strongest far-tail-supported marker wins when one exists; weak far
    tails fall back to ring score only after the near-tail shape is verified.

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

    markers, gradient_best = _verified_markers(frame, sample, markers)
    if not markers:
        return None
    best = gradient_best or max(markers, key=lambda marker: marker.score)
    return EnemyDirectionObservation(angle_deg=best.angle_deg, score=best.score, markers=markers)
