"""Absolute world localization from the minimap using a calibrated large map.

This module is deliberately independent from WS, packet capture and runtime task
lifecycle. It answers one question: given a game frame and a calibrated map image,
where is the minimap center on that map?

The global search is intentionally conservative. A single matching peak can be a
repeated wall or building, so callers should require a high NCC and a clear gap to
the next candidate, or use :meth:`MinimapMapLocator.locate_near` with the previous
world position as a bounded prior.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.core.NavConfig import DEFAULT_SCALE_CONSTANT
from src.localization.localization_workers import ordered_results
from src.localization.minimap_odometry import (
    DEFAULT_CENTER_RATIO,
    DEFAULT_R_OUTER_RATIO,
    annulus_mask,
    region_geometry,
)
from src.localization.native_tile_image import ZeroPaddedSearchRaster
from src.localization.template_correlation import correlate_bounded, correlate_color_zncc
from src.localization.tiled_map_image import TiledMapImage

MAP_PIXELS_PER_METER = 4.0
COARSE_FACTOR = 0.5
RING_INNER_RATIO = 0.10
RING_OUTER_RATIO = 0.85
DEFAULT_REFINE_RADIUS_MAP_PX = 14
DEFAULT_TOP_K = 8
DEFAULT_CANDIDATE_MERGE_DISTANCE_M = 4.0
DEFAULT_ROBUST_TRIM_RATIO = 0.30
DEFAULT_PROMOTE_HITS = 2
DEFAULT_PROMOTE_WINDOW_S = 1.0
DEFAULT_CONSISTENCY_DISTANCE_M = 5.0
DEFAULT_STALE_AFTER_S = 2.0
MIN_ACCEPT_NCC = 0.70
MIN_VALID_NCC = 0.55
MIN_ACCEPT_MARGIN = 0.15


@dataclass(frozen=True)
class MapCalibration:
    """Map-pixel to world-XZ affine calibration."""

    matrix: np.ndarray
    inverse_matrix: np.ndarray
    image_width: int = 0
    image_height: int = 0
    map_name: str = ""

    def __post_init__(self) -> None:
        matrix = np.asarray(self.matrix, dtype=np.float64)
        inverse = np.asarray(self.inverse_matrix, dtype=np.float64)
        if matrix.shape != (2, 3) or inverse.shape != (2, 3):
            raise ValueError("Map calibration matrices must both be 2x3")
        if not np.isfinite(matrix).all() or not np.isfinite(inverse).all():
            raise ValueError("Map calibration contains non-finite values")
        object.__setattr__(self, "matrix", matrix)
        object.__setattr__(self, "inverse_matrix", inverse)

    @classmethod
    def from_mapping_file(cls, path: str | Path) -> MapCalibration:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_mapping(data, name=Path(path).stem)

    @classmethod
    def from_mapping(cls, data, *, name: str) -> MapCalibration:
        if not isinstance(data, dict):
            raise ValueError("Map mapping must be an object")
        matrix = np.asarray(data["matrix"], dtype=np.float64)
        inverse = data.get("inverse_matrix")
        if inverse is None:
            homogeneous = np.vstack((matrix, np.array([0.0, 0.0, 1.0])))
            inverse = np.linalg.inv(homogeneous)[:2]
        size = data.get("image_size") or {}
        if not isinstance(size, dict):
            raise ValueError("Map image_size must be an object")
        return cls(
            matrix=matrix,
            inverse_matrix=np.asarray(inverse, dtype=np.float64),
            image_width=int(size.get("width") or 0),
            image_height=int(size.get("height") or 0),
            map_name=str(data.get("map_name") or name),
        )

    def map_to_world(self, map_x: float, map_y: float) -> tuple[float, float]:
        point = self.matrix @ np.array([float(map_x), float(map_y), 1.0], dtype=np.float64)
        return float(point[0]), float(point[1])

    def world_to_map(self, world_x: float, world_z: float) -> tuple[float, float]:
        point = self.inverse_matrix @ np.array([float(world_x), float(world_z), 1.0], dtype=np.float64)
        return float(point[0]), float(point[1])


@dataclass(frozen=True)
class MinimapGeometry:
    """Minimap center and visible outer radius in full-frame pixels."""

    center_x: float
    center_y: float
    outer_radius: float
    ring_inner_ratio: float = RING_INNER_RATIO
    ring_outer_ratio: float = RING_OUTER_RATIO

    def __post_init__(self) -> None:
        values = (
            self.center_x,
            self.center_y,
            self.outer_radius,
            self.ring_inner_ratio,
            self.ring_outer_ratio,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("Minimap geometry contains non-finite values")
        if self.outer_radius <= 0:
            raise ValueError("Minimap outer radius must be positive")
        if not 0 <= self.ring_inner_ratio < self.ring_outer_ratio <= 1:
            raise ValueError("Minimap ring ratios must satisfy 0 <= inner < outer <= 1")

    @classmethod
    def from_frame(
        cls,
        width: int,
        height: int,
        *,
        center_ratio: tuple[float, float] = DEFAULT_CENTER_RATIO,
        outer_radius_ratio: float = DEFAULT_R_OUTER_RATIO,
        ring_inner_ratio: float = RING_INNER_RATIO,
        ring_outer_ratio: float = RING_OUTER_RATIO,
    ) -> MinimapGeometry:
        """Build client-area geometry through the production minimap helper."""

        center_x, center_y, _inner, outer_radius = region_geometry(
            int(width),
            int(height),
            center_ratio=center_ratio,
            r_outer_ratio=outer_radius_ratio,
            r_inner_ratio=0.0,
        )
        return cls(
            center_x=center_x,
            center_y=center_y,
            outer_radius=outer_radius,
            ring_inner_ratio=ring_inner_ratio,
            ring_outer_ratio=ring_outer_ratio,
        )

    @property
    def match_inner_radius(self) -> float:
        return self.outer_radius * self.ring_inner_ratio

    @property
    def match_outer_radius(self) -> float:
        return self.outer_radius * self.ring_outer_ratio


@dataclass(frozen=True)
class MapCandidate:
    map_x: float
    map_y: float
    world_x: float
    world_z: float
    ncc: float
    coarse_score: float = 0.0
    full_ncc: float = 0.0
    inlier_ratio: float = 1.0
    tier_id: str | None = None
    map_id: str | None = None
    color: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_x": self.map_x,
            "map_y": self.map_y,
            "world_x": self.world_x,
            "world_z": self.world_z,
            "ncc": self.ncc,
            "coarse_score": self.coarse_score,
            "full_ncc": self.full_ncc,
            "inlier_ratio": self.inlier_ratio,
            "tier_id": self.tier_id,
            "map_id": self.map_id,
            "color": self.color,
        }


def strong_candidate(best, margin, min_ncc=None, min_full=None, min_margin=None):
    """Apply score thresholds in the candidate's matching space."""
    defaults = (0.45, 0.45, 0.10) if best.color else (0.85, 0.65, 0.08)
    thresholds = tuple(
        default if override is None else override
        for default, override in zip(defaults, (min_ncc, min_full, min_margin), strict=True)
    )
    return best.ncc >= thresholds[0] and best.full_ncc >= thresholds[1] and margin >= thresholds[2]


def candidate_margin(candidates) -> float:
    """Require separation from every alternative in either full or robust NCC.

    Trimming can make a displaced side lobe almost as strong as the true peak,
    even when the untrimmed terrain clearly separates them. Both scores use the
    same frame-derived mask; neither comparison selects pixels per candidate.
    """
    if len(candidates) < 2:
        return 1.0
    best = candidates[0]
    return max(best.ncc - max(c.ncc for c in candidates[1:]), best.full_ncc - max(c.full_ncc for c in candidates[1:]))


@dataclass(frozen=True)
class LocateResult:
    status: str
    reason: str
    best: MapCandidate
    candidates: tuple[MapCandidate, ...]
    elapsed_s: float
    scale: float
    used_prior: bool = False
    spatial_margin: float | None = None
    layer_margin: float | None = None

    @property
    def confidence(self) -> float:
        return self.best.ncc

    @property
    def margin(self) -> float:
        if self.spatial_margin is not None:
            return self.spatial_margin
        return candidate_margin(self.candidates)

    def to_dict(self, *, include_candidates: bool = True) -> dict[str, Any]:
        values: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "map_x": self.best.map_x,
            "map_y": self.best.map_y,
            "world_x": self.best.world_x,
            "world_z": self.best.world_z,
            "confidence": self.confidence,
            "margin": self.margin,
            "elapsed_s": self.elapsed_s,
            "scale": self.scale,
            "used_prior": self.used_prior,
            "layer_margin": self.layer_margin,
        }
        if include_candidates:
            values["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return values


@dataclass(frozen=True)
class TrackResult:
    """Temporal tracking state returned by :class:`MinimapTracker`."""

    status: str
    accepted: bool
    reason: str
    locate: LocateResult | None
    position: MapCandidate | None
    pending_hits: int
    used_prior: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "reason": self.reason,
            "pending_hits": self.pending_hits,
            "used_prior": self.used_prior,
            "position": None if self.position is None else self.position.to_dict(),
            "locate": None if self.locate is None else self.locate.to_dict(),
        }


@dataclass(frozen=True)
class _PreparedTemplate:
    flat: np.ndarray
    ring: np.ndarray
    mask: np.ndarray
    mask8: np.ndarray
    center_offset: tuple[float, float] = (0.0, 0.0)

    @cached_property
    def color_zncc(self) -> tuple[np.ndarray, float]:
        values, norm = _centered_zncc(self.ring[self.mask])
        values.setflags(write=False)
        return values, norm

    @cached_property
    def dim_terrain(self):
        """One frame-derived mask shared by every map, floor and candidate.

        Global NCC is dominated by a bright floor's repeated pattern. A broad,
        textured dim component supplies a second independently normalized view.
        Never infer this mask from a candidate's alpha or matching residuals.
        """
        if self.ring.ndim != 3:
            return None
        gray = _as_gray(self.ring)
        values = gray[self.mask]
        threshold, _ = cv2.threshold(values, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        dim = self.mask & (gray <= threshold)
        fraction = dim.sum() / self.mask.sum()
        if not 0.55 <= fraction <= 0.95:
            return None
        low, high = gray[dim], gray[self.mask & ~dim]
        # A uniform/one-mode image or quantization noise is not independent
        # background evidence. Require texture and a clearly darker population.
        if low.std() < 4 or high.mean() < 2 * max(1, low.mean()):
            return None
        half = gray.shape[0] // 2
        for rows, cols in (
            (slice(None, half), slice(None, half)),
            (slice(None, half), slice(half, None)),
            (slice(half, None), slice(None, half)),
            (slice(half, None), slice(half, None)),
        ):
            if dim[rows, cols].sum() < 0.35 * self.mask[rows, cols].sum():
                return None
        mask8 = dim.astype(np.uint8) * 255
        centered, norm = _centered_zncc(self.ring[dim])
        for pixels in (dim, mask8, centered):
            pixels.setflags(write=False)
        return dim, mask8, (centered, norm)

    @cached_property
    def proposal_dim_mask(self):
        """Allow one-sided dark terrain to order searches, never to score a fix."""
        if self.ring.ndim != 3:
            return None
        gray = _as_gray(self.ring)
        threshold, _ = cv2.threshold(gray[self.mask], 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        dim = self.mask & (gray <= threshold)
        if not 0.20 <= dim.sum() / self.mask.sum() <= 0.95:
            return None
        low, high = gray[dim], gray[self.mask & ~dim]
        if low.std() < 4 or high.mean() < 2 * max(1, low.mean()):
            return None
        dim.setflags(write=False)
        return dim


@dataclass(frozen=True)
class NccScore:
    """Full and robust scores for one aligned template patch."""

    score: float
    full_score: float
    inlier_ratio: float


def minimap_map_scale(screen_width: int, *, scale_constant: float = DEFAULT_SCALE_CONSTANT) -> float:
    """Return map-pixel -> minimap-pixel scale for a calibrated 4 px/m map."""

    width = int(screen_width)
    if width <= 0 or scale_constant <= 0:
        raise ValueError("screen width and scale constant must be positive")
    return width / (MAP_PIXELS_PER_METER * float(scale_constant))


def masked_zncc(left: np.ndarray, right: np.ndarray, mask: np.ndarray) -> float:
    """Zero-normalized cross-correlation over one shared boolean mask."""

    selected = np.asarray(mask, dtype=bool)
    if left.shape != right.shape or selected.shape != left.shape[:2]:
        raise ValueError("ZNCC inputs and mask must have the same shape")
    if not selected.any():
        return 0.0
    left_values = np.asarray(left, dtype=np.float64)[selected]
    right_values = np.asarray(right, dtype=np.float64)[selected]
    return _zncc_values(left_values, right_values)


_DOT_EXPRESSION = "i,i->"


def _centered_zncc(values: np.ndarray) -> tuple[np.ndarray, float]:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return values.ravel(), 0.0
    values -= values.mean(axis=0)
    values = values.ravel()
    # These are short 1-D reductions repeated for every candidate. Dispatching
    # them to a process-wide BLAS thread pool adds latency on the control path.
    return values, math.sqrt(float(np.einsum(_DOT_EXPRESSION, values, values, optimize=False)))


def _zncc_centered(left: tuple[np.ndarray, float], right: tuple[np.ndarray, float]) -> float:
    left_values, left_norm = left
    right_values, right_norm = right
    denominator = left_norm * right_norm
    if denominator <= 1e-12:
        return 0.0
    return float(np.einsum(_DOT_EXPRESSION, left_values, right_values, optimize=False) / denominator)


def _zncc_values(left_values: np.ndarray, right_values: np.ndarray) -> float:
    if left_values.size == 0 or right_values.size == 0:
        return 0.0
    return _zncc_centered(_centered_zncc(left_values), _centered_zncc(right_values))


def robust_masked_zncc(
    left: np.ndarray,
    right: np.ndarray,
    mask: np.ndarray,
    *,
    trim_ratio: float = DEFAULT_ROBUST_TRIM_RATIO,
) -> NccScore:
    """Full-ring ZNCC with outlier trimming for fixed UI overlays.

    不收缩环带。先计算完整环带的 NCC，再在归一化后按逐像素残差去掉最不一致的
    ``trim_ratio`` 部分，通常对应中心箭头、图标和固定 HUD。为了避免错误候选因为
    “只保留少数同色像素”而获得虚高分，鲁棒分相对完整分最多只能提升 0.20。
    """

    selected = np.asarray(mask, dtype=bool)
    if left.shape != right.shape or selected.shape != left.shape[:2]:
        raise ValueError("ZNCC inputs and mask must have the same shape")
    if not selected.any():
        return NccScore(0.0, 0.0, 0.0)
    left_values = np.asarray(left, dtype=np.float64)[selected]
    right_values = np.asarray(right, dtype=np.float64)[selected]
    full_score = _zncc_values(left_values, right_values)
    if left.ndim == 3:
        return NccScore(full_score, full_score, 1.0)
    trim = max(0.0, min(0.60, float(trim_ratio)))
    if left_values.size < 16 or trim <= 0.0:
        return NccScore(full_score, full_score, 1.0)
    left_std = float(left_values.std())
    right_std = float(right_values.std())
    if left_std <= 1e-9 or right_std <= 1e-9:
        return NccScore(full_score, full_score, 1.0)
    left_norm = (left_values - left_values.mean()) / left_std
    right_norm = (right_values - right_values.mean()) / right_std
    residual = np.abs(left_norm - right_norm)
    keep_count = max(8, round(left_values.size * (1.0 - trim)))
    boundary = np.partition(residual, keep_count - 1)[keep_count - 1]
    keep = residual <= boundary
    if np.count_nonzero(keep) != keep_count:
        # Preserve the previous choice among pixels tied at the trim boundary.
        keep = np.argsort(residual)[:keep_count]
    trimmed_score = _zncc_values(left_values[keep], right_values[keep])
    # Do not let trimming turn a weak overall match into a high-confidence one.
    robust_score = min(trimmed_score, full_score + 0.20)
    return NccScore(
        score=float(robust_score),
        full_score=float(full_score),
        inlier_ratio=float(keep_count / left_values.size),
    )


def _subpixel_offset(negative: float, center: float, positive: float) -> float:
    """Log-parabola peak offset, same idea as the old Go MapTracker."""

    values = (float(negative), float(center), float(positive))
    if any(value <= 1e-9 for value in values):
        return 0.0
    ln_neg, ln_center, ln_pos = (math.log(value) for value in values)
    denominator = 2.0 * (ln_neg + ln_pos - 2.0 * ln_center)
    if denominator >= -1e-12:
        return 0.0
    offset = -(ln_pos - ln_neg) / denominator
    return float(max(-1.0, min(1.0, offset)))


def _peak_subpixel_offset(
    scores: np.ndarray,
    left: int,
    top: int,
) -> tuple[float, float]:
    height, width = scores.shape[:2]
    center = float(scores[top, left])
    dx = dy = 0.0
    if 0 < left < width - 1:
        dx = _subpixel_offset(scores[top, left - 1], center, scores[top, left + 1])
    if 0 < top < height - 1:
        dy = _subpixel_offset(scores[top - 1, left], center, scores[top + 1, left])
    return dx, dy


def deduplicate_candidates(
    candidates: list[MapCandidate] | tuple[MapCandidate, ...],
    *,
    min_world_distance_m: float = DEFAULT_CANDIDATE_MERGE_DISTANCE_M,
) -> list[MapCandidate]:
    """Keep only the strongest candidate for each physical location."""

    kept: list[MapCandidate] = []
    for candidate in sorted(candidates, key=lambda item: item.ncc, reverse=True):
        if any(
            candidate.tier_id == other.tier_id
            and candidate.map_id == other.map_id
            and math.hypot(candidate.world_x - other.world_x, candidate.world_z - other.world_z) < min_world_distance_m
            for other in kept
        ):
            continue
        kept.append(candidate)
    return kept


def _as_gray(image: np.ndarray | None) -> np.ndarray | None:
    if image is None or not isinstance(image, np.ndarray) or image.ndim < 2:
        return None
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@lru_cache(maxsize=8)
def _texture_sectors(size: int, center: tuple[float, float], radius: float) -> tuple[np.ndarray, ...]:
    """Inspect distributed terrain texture, excluding the player arrow and HUD rim."""
    yy, xx = np.mgrid[:size, :size]
    xx, yy = xx - center[0], yy - center[1]
    distance = np.hypot(xx, yy)
    annulus = (distance >= radius * 0.3) & (distance <= radius * 0.8)
    angles = (np.arctan2(yy, xx) + np.pi) * (4 / np.pi)
    return tuple(annulus & (angles >= i) & (angles < i + 1) for i in range(8))


def _texture_details(ring: np.ndarray, center: tuple[float, float], radius: float) -> tuple[float, ...]:
    sectors = _texture_sectors(ring.shape[0], center, radius)
    detail = cv2.absdiff(ring, cv2.GaussianBlur(ring, (5, 5), 0))
    return tuple(float(detail[sector].mean()) if sector.any() else 0.0 for sector in sectors)


def _has_minimap_texture(ring: np.ndarray, center: tuple[float, float], radius: float) -> bool:
    """Conservative plausibility gate, not a semantic guarantee that HUD is visible.

    Loading-logo edges may have high contrast but only occupy a few sectors.
    Require terrain detail in at least six sectors; color is intentionally irrelevant.
    """
    return sum(value >= 1.0 for value in _texture_details(ring, center, radius)) >= 6


def _minimap_rim_sectors(frame: np.ndarray, geometry: MinimapGeometry) -> int:
    """Independent HUD evidence for sparse terrain, outside the matching annulus.

    Require a thin bright circular ridge in six of eight sectors at one common
    radius. A three-sample radial tolerance accommodates subpixel HUD centering;
    unrelated edges at different radii cannot collectively manufacture a rim.
    This only permits a search: NCC, uniqueness and temporal gates still apply.
    """
    radius = geometry.outer_radius
    delta = max(1.0, radius * 0.036)
    extent = radius * 1.10 + delta + 1
    height, width = frame.shape[:2]
    cx, cy = geometry.center_x, geometry.center_y
    if cx - extent < 0 or cy - extent < 0 or cx + extent >= width or cy + extent >= height:
        return 0
    # Convert only the HUD crop, not every full frame for every map/layer.
    x0, y0 = math.floor(cx - extent), math.floor(cy - extent)
    roi = frame[y0 : math.ceil(cy + extent) + 1, x0 : math.ceil(cx + extent) + 1]
    gray = _as_gray(roi).astype(np.float32)
    angles = (np.arange(128) + 0.5) * (2 * np.pi / 128)
    radii = np.linspace(0.94, 1.10, 17)[:, None] * radius
    cos, sin = np.cos(angles), np.sin(angles)

    def sample(offset):
        x = (cx - x0 + cos * (radii + offset)).astype(np.float32)
        y = (cy - y0 + sin * (radii + offset)).astype(np.float32)
        return cv2.remap(gray, x, y, cv2.INTER_LINEAR)

    ridge = sample(0) - (sample(-delta) + sample(delta)) / 2
    ridge = np.maximum.reduce((ridge[:-2], ridge[1:-1], ridge[2:]))
    coverage = (ridge > 4.0).reshape(-1, 8, 16).mean(axis=2)
    return int((coverage >= 0.5).sum(axis=1).max())


def _sanitize_scores(scores: np.ndarray) -> np.ndarray:
    return np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)


def _top_peaks(scores: np.ndarray, count: int, *, min_distance: int) -> list[tuple[float, tuple[int, int]]]:
    if scores.size == 0 or count <= 0:
        return []
    work = scores.copy()
    np.nan_to_num(work, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
    peaks: list[tuple[float, tuple[int, int]]] = []
    radius = max(1, int(min_distance))
    while len(peaks) < count:
        _min_value, max_value, _min_loc, max_loc = cv2.minMaxLoc(work)
        if not math.isfinite(max_value) or max_value < 0:
            break
        peaks.append((float(max_value), max_loc))
        x, y = max_loc
        work[max(0, y - radius) : y + radius + 1, max(0, x - radius) : x + radius + 1] = -1.0
    return peaks


class MinimapMapLocator:
    """Coarse global search plus local masked-ZNCC refinement."""

    def __init__(
        self,
        map_image: np.ndarray | TiledMapImage,
        calibration: MapCalibration,
        *,
        scale_override: float | None = None,
        coarse_factor: float = COARSE_FACTOR,
        refine_radius_map_px: int = DEFAULT_REFINE_RADIUS_MAP_PX,
        top_k: int = DEFAULT_TOP_K,
        candidate_merge_distance_m: float = DEFAULT_CANDIDATE_MERGE_DISTANCE_M,
        robust_trim_ratio: float = DEFAULT_ROBUST_TRIM_RATIO,
        min_valid_ncc: float | None = None,
        min_accept_ncc: float | None = None,
        min_accept_margin: float | None = None,
    ) -> None:
        tiled = isinstance(map_image, TiledMapImage)
        image = map_image
        if isinstance(image, np.ndarray) and image.ndim == 3 and image.shape[2] == 4:
            image = image[:, :, :3].copy()
            image[map_image[:, :, 3] == 0] = 0
        if not tiled:
            if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
                raise ValueError("Map image must be grayscale, BGR or BGRA")
            if image.ndim == 3 and image.shape[2] != 3:
                raise ValueError("Map image must be grayscale, BGR or BGRA")
        if not 0 < coarse_factor <= 1:
            raise ValueError("coarse_factor must be in (0, 1]")
        if scale_override is not None and scale_override <= 0:
            raise ValueError("scale_override must be positive")
        self.map_image = image
        self.color = tiled or image.ndim == 3
        self.calibration = calibration
        self.scale_override = scale_override
        self.coarse_factor = float(coarse_factor)
        self.refine_radius_map_px = max(1, int(refine_radius_map_px))
        self.top_k = max(1, int(top_k))
        self.candidate_merge_distance_m = float(candidate_merge_distance_m)
        if self.candidate_merge_distance_m <= 0:
            raise ValueError("candidate_merge_distance_m must be positive")
        self.robust_trim_ratio = float(robust_trim_ratio)
        if not 0.0 <= self.robust_trim_ratio < 1.0:
            raise ValueError("robust_trim_ratio must be in [0, 1)")
        self.min_valid_ncc = float(
            min_valid_ncc if min_valid_ncc is not None else (0.45 if self.color else MIN_VALID_NCC)
        )
        self.min_accept_ncc = float(
            min_accept_ncc if min_accept_ncc is not None else (0.45 if self.color else MIN_ACCEPT_NCC)
        )
        self.min_accept_margin = float(
            min_accept_margin if min_accept_margin is not None else (0.10 if self.color else MIN_ACCEPT_MARGIN)
        )
        self._mask_cache: OrderedDict[MinimapGeometry, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._coarse_cache: dict[tuple[float, float], np.ndarray | ZeroPaddedSearchRaster] = {}
        self._pending_coarse = {}
        self._closed = False
        self.last_failure_reason = ""

    def _reject(self, reason):
        self.last_failure_reason = reason
        return None

    @property
    def resident_bytes(self):
        return self.map_image.nbytes + sum(image.nbytes for image in self._coarse_cache.values())

    def close(self):
        self._closed = True
        for future in self._pending_coarse.values():
            future.cancel()
        self._pending_coarse.clear()
        self._coarse_cache.clear()
        if isinstance(self.map_image, TiledMapImage):
            self.map_image.close()

    @classmethod
    def from_files(
        cls,
        map_path: str | Path,
        mapping_path: str | Path,
        **kwargs: Any,
    ) -> MinimapMapLocator:
        calibration = MapCalibration.from_mapping_file(mapping_path)
        size = (calibration.image_width, calibration.image_height)
        map_image = TiledMapImage.open_prepared(map_path, size)
        if map_image is None:
            map_image = cv2.imread(str(map_path), cv2.IMREAD_COLOR)
        if map_image is None:
            raise ValueError(f"Could not read map image: {map_path}")
        actual_height, actual_width = map_image.shape[:2]
        if calibration.image_width <= 0 or calibration.image_height <= 0:
            raise ValueError(f"Mapping does not declare image_size: {mapping_path}")
        if (actual_width, actual_height) != (calibration.image_width, calibration.image_height):
            raise ValueError(
                f"Map image size {actual_width}x{actual_height} does not match mapping image_size "
                f"{calibration.image_width}x{calibration.image_height}: {map_path}"
            )
        return cls(map_image, calibration, **kwargs)

    def _scale(self, frame_width: int) -> float:
        return self.scale_override if self.scale_override is not None else minimap_map_scale(frame_width)

    def _prepare_template(
        self,
        frame: np.ndarray | None,
        geometry: MinimapGeometry,
    ) -> _PreparedTemplate | None:
        self.last_failure_reason = ""
        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
            return self._reject("minimap_frame_missing_or_invalid")
        height, width = frame.shape[:2]
        half = math.ceil(geometry.match_outer_radius)
        center_x = round(geometry.center_x)
        center_y = round(geometry.center_y)
        x0, y0 = center_x - half, center_y - half
        x1, y1 = center_x + half + 1, center_y + half + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return self._reject(f"minimap_roi_out_of_bounds: roi=({x0},{y0},{x1},{y1}) frame={width}x{height}")
        cached = self._geometry_masks(geometry, half, x0, y0)
        if cached is None:
            return self._reject("minimap_mask_too_small: required_pixels=64")
        mask, mask8 = cached
        roi = frame[y0:y1, x0:x1]
        ring = roi[:, :, :3].copy() if self.color else _as_gray(roi).copy()
        center = (geometry.center_x - x0, geometry.center_y - y0)
        rim_sectors = None

        def texture_valid(pixels, stage):
            nonlocal rim_sectors
            details = _texture_details(_as_gray(pixels), center, geometry.outer_radius)
            textured = sum(value >= 1.0 for value in details)
            if textured >= 6:
                return True
            if rim_sectors is None:
                rim_sectors = _minimap_rim_sectors(frame, geometry)
            # A rim alone is not terrain. Masked glyphs must not be the only
            # surviving detail, and a loading logo without a HUD cannot pass.
            if textured >= 1 and rim_sectors >= 6:
                return True
            self._reject(
                f"minimap_insufficient_texture: stage={stage} sectors={textured}/8 required=6 "
                f"rim_sectors={rim_sectors}/8 details=" + ",".join(f"{v:.2f}" for v in details)
            )
            return False

        if not texture_valid(ring, "raw"):
            return None
        visible = self._visible_masks(roi, mask, mask8, half)
        if self.color and visible is not None:
            visible = self._white_visible_masks(roi, visible[0], visible[1], half)
        if visible is None:
            return self._reject("minimap_icon_occlusion: required_total=0.55 required_quadrant=0.35")
        mask, mask8 = visible
        mean = ring[mask].mean(axis=0)
        flat = ring.copy()
        flat[~mask] = mean
        if not texture_valid(flat, "masked"):
            if self.color:
                return None
            visible = self._legacy_visible_masks(roi, cached[0], cached[1], half)
            if visible is None:
                return None
            mask, mask8 = visible
            flat = ring.copy()
            flat[~mask] = float(ring[mask].mean())
            if not texture_valid(flat, "legacy_masked"):
                return None
        self.last_failure_reason = ""
        return _PreparedTemplate(
            flat=flat,
            ring=ring,
            mask=mask,
            mask8=mask8,
            center_offset=(geometry.center_x - center_x, geometry.center_y - center_y),
        )

    def _geometry_masks(self, geometry, half, x0, y0):
        cached = self._mask_cache.get(geometry)
        if cached is None:
            size = 2 * half + 1
            mask = (
                annulus_mask(
                    size,
                    size,
                    (geometry.center_x - x0, geometry.center_y - y0),
                    geometry.match_inner_radius,
                    geometry.match_outer_radius,
                )
                >= 0.5
            )
            if int(mask.sum()) < 64:
                return None
            mask8 = mask.astype(np.uint8) * 255
            mask.setflags(write=False)
            mask8.setflags(write=False)
            cached = (mask, mask8)
            self._mask_cache[geometry] = cached
            if len(self._mask_cache) > 8:
                self._mask_cache.popitem(last=False)
        self._mask_cache.move_to_end(geometry)
        return cached

    @staticmethod
    def _white_visible_masks(roi, mask, mask8, half):
        white = np.all(roi[:, :, :3] == 255, axis=2).astype(np.uint8)
        excluded = cv2.dilate(white, np.ones((5, 5), dtype=np.uint8))
        return MinimapMapLocator._checked_visible_masks(mask, excluded, half)

    @staticmethod
    def _visible_masks(roi, mask, mask8, half):
        """Exclude glyph cores, connected antialiasing and resolution-scaled outlines."""
        # Every candidate receives the same frame-derived exclusions. Geometry
        # masks stay immutable, with no icon-coverage activation threshold.
        if roi.ndim != 3 or roi.shape[2] < 3:
            return mask, mask8
        colors = roi[:, :, :3]
        high, low = colors.max(axis=2), colors.min(axis=2)
        # White UI glyphs include dim antialiasing and a dark outline. Mask
        # those edges too, using padding scaled with the minimap's radius.
        colored = (high >= 190) & (high.astype(np.int16) - low > 90)
        seeds = (low >= 210) | colored
        if not np.any(seeds):
            return mask, mask8
        # Grow only dim glyph pixels connected to a bright/color seed. Isolated
        # gray terrain must not become an icon merely because it exceeds 190.
        count, labels = cv2.connectedComponents(((low >= 190) | colored).astype(np.uint8), connectivity=8)
        selected = np.zeros(count, dtype=np.uint8)
        selected[np.unique(labels[seeds])] = 1
        selected[0] = 0
        overlay = selected[labels]
        padding = max(1, round(half / 35))
        overlay = cv2.dilate(overlay, np.ones((2 * padding + 1, 2 * padding + 1), np.uint8))
        if not np.any(overlay & mask):
            return mask, mask8
        visible = MinimapMapLocator._checked_visible_masks(mask, overlay, half)
        if visible is not None:
            return visible
        # Bright roads/buildings can join the dim-glyph components and exhaust
        # coverage. Drop that growth before dropping resolution-scaled outline
        # padding: jumping straight to one pixel leaves dark icon borders in NCC.
        if padding > 1:
            visible = MinimapMapLocator._legacy_visible_masks(roi, mask, mask8, half, padding=padding)
            if visible is not None:
                return visible
        return MinimapMapLocator._legacy_visible_masks(roi, mask, mask8, half)

    @staticmethod
    def _legacy_visible_masks(roi, mask, mask8, half, *, padding=1):
        """Mask bright glyph seeds with bounded padding, without dim-pixel growth."""
        if roi.ndim != 3 or roi.shape[2] < 3:
            return mask, mask8
        colors = roi[:, :, :3]
        high, low = colors.max(axis=2), colors.min(axis=2)
        overlay = (low >= 210) | ((high >= 190) & (high.astype(np.int16) - low > 90))
        if not np.any(overlay & mask):
            return mask, mask8
        overlay = cv2.dilate(overlay.astype(np.uint8), np.ones((2 * padding + 1, 2 * padding + 1), np.uint8))
        return MinimapMapLocator._checked_visible_masks(mask, overlay, half)

    @staticmethod
    def _checked_visible_masks(mask, overlay, half):
        """Enforce the same total and quadrant coverage for every mask strategy."""
        visible = mask & (overlay == 0)
        if visible.sum() < 0.55 * mask.sum():
            return None
        # A surviving corner alone cannot prove that terrain remains visible.
        for sy, sx in (
            (slice(None, half), slice(None, half)),
            (slice(None, half), slice(half, None)),
            (slice(half, None), slice(None, half)),
            (slice(half, None), slice(half, None)),
        ):
            if visible[sy, sx].sum() < 0.35 * mask[sy, sx].sum():
                return None
        return visible, visible.astype(np.uint8) * 255

    def _build_coarse(self, coarse_scale):
        if isinstance(self.map_image, TiledMapImage):
            return self.map_image.coarse_overview(coarse_scale)
        return cv2.resize(self.map_image, None, fx=coarse_scale, fy=coarse_scale, interpolation=cv2.INTER_AREA)

    def _remember_coarse(self, key, pixels):
        self._coarse_cache[key] = pixels
        while len(self._coarse_cache) > 2:
            self._coarse_cache.pop(next(iter(self._coarse_cache)))
        return pixels

    def prepare_global_search(self, frame_width, executor, *, wait=False, stop=None):
        """Prepare pixels; only a background hint job may wait on its snapshot."""
        if self._closed:
            raise ValueError("minimap locator closed")
        scale = self._scale(frame_width) * self.coarse_factor
        key = (self.coarse_factor, scale)
        # A resize supersedes queued work. Completed old widths must not stay
        # alive in futures outside the two-entry coarse cache indefinitely.
        for pending_key in tuple(self._pending_coarse):
            if pending_key != key:
                self._pending_coarse.pop(pending_key).cancel()
        if key in self._coarse_cache:
            return True
        future = self._pending_coarse.get(key)
        if future is None:
            output_pixels = round(self.map_image.shape[0] * scale) * round(self.map_image.shape[1] * scale)
            memory_bytes = (
                self.map_image.overview_memory_bytes(scale)
                if isinstance(self.map_image, TiledMapImage)
                else 64 * 1024**2 + output_pixels * 4
            )
            self._pending_coarse[key] = executor.submit(
                self._build_coarse,
                scale,
                memory_bytes=memory_bytes,
            )
            future = self._pending_coarse[key]
            if not wait:
                return False
        if wait:
            try:
                ordered_results((future,), stop)
            except Exception:
                self._pending_coarse.pop(key, None)
                raise
        if not future.done():
            return False
        del self._pending_coarse[key]
        self._remember_coarse(key, future.result())
        return True

    def _coarse_map(self, coarse_scale: float) -> np.ndarray | ZeroPaddedSearchRaster:
        key = (self.coarse_factor, coarse_scale)
        cached = self._coarse_cache.get(key)
        if cached is None:
            cached = self._remember_coarse(key, self._build_coarse(coarse_scale))
        return cached

    def _candidate_from_patch(
        self,
        template: _PreparedTemplate,
        patch: np.ndarray,
        map_x: float,
        map_y: float,
        *,
        coarse_score: float,
    ) -> MapCandidate:
        if template.ring.ndim == 3:
            full = _zncc_centered(template.color_zncc, _centered_zncc(patch[template.mask]))
            dim = template.dim_terrain
            ncc = full if dim is None else 0.5 * (full + _zncc_centered(dim[2], _centered_zncc(patch[dim[0]])))
            score = NccScore(ncc, full, 1.0)
        else:
            score = robust_masked_zncc(
                template.ring,
                patch,
                template.mask,
                trim_ratio=self.robust_trim_ratio,
            )
        world_x, world_z = self.calibration.map_to_world(map_x, map_y)
        return MapCandidate(
            map_x=float(map_x),
            map_y=float(map_y),
            world_x=world_x,
            world_z=world_z,
            ncc=score.score,
            coarse_score=float(coarse_score),
            full_ncc=score.full_score,
            inlier_ratio=score.inlier_ratio,
            color=self.color,
        )

    def _match_scores(self, image, template, *, coarse=False, allowed=None):
        """Use the same balanced evidence in peak generation and final scoring."""
        dim = template.dim_terrain
        if coarse:
            target = max(4, round(template.flat.shape[0] * self.coarse_factor))
            pixels = cv2.resize(template.flat, (target, target), interpolation=cv2.INTER_AREA)
            mask = None
            dim_mask = (
                None
                if dim is None
                else (
                    cv2.resize(dim[0].astype(np.float32), (target, target), interpolation=cv2.INTER_AREA) >= 0.99
                ).astype(np.uint8)
                * 255
            )
        else:
            pixels, mask = template.ring, template.mask8
            dim_mask = None if dim is None else dim[1]
        method = cv2.TM_CCOEFF_NORMED if coarse or self.color else cv2.TM_CCORR_NORMED

        if (
            self.color
            and (coarse or (dim_mask is not None and np.count_nonzero(dim_mask) >= 16))
            and pixels.dtype == np.uint8
            and (not isinstance(image, np.ndarray) or image.dtype == np.uint8)
        ):
            masks = (mask,) if dim_mask is None or np.count_nonzero(dim_mask) < 16 else (mask, dim_mask)
            bounds = image.correlation_bounds(pixels.shape[:2]) if isinstance(image, ZeroPaddedSearchRaster) else None
            return correlate_color_zncc(
                image, pixels, masks=masks, allowed=allowed, output_bounds=bounds, compact=not coarse
            )

        def match(selected):
            if allowed is not None:
                scores = correlate_bounded(image, pixels, mask=selected, block_size=256, allowed=allowed, method=method)
            elif isinstance(image, ZeroPaddedSearchRaster):
                scores = image.match_scores(pixels, mask=selected)
            elif coarse:
                scores = correlate_bounded(image, pixels, mask=selected)
            else:
                scores = cv2.matchTemplate(image, pixels, method, mask=selected)
            return _sanitize_scores(scores)

        full = match(mask)
        if dim_mask is None or np.count_nonzero(dim_mask) < 16:
            return full
        return 0.5 * (full + match(dim_mask))

    def _candidates_from_patches(self, template, patches, positions):
        """Score bounded batches without changing masks, precision or top-k."""
        if not patches:
            return []
        if template.ring.ndim != 3:
            return [
                self._candidate_from_patch(template, pixels, x, y, coarse_score=score)
                for pixels, (x, y, score) in zip(patches, positions, strict=True)
            ]

        def zncc(pixels, mask, prepared):
            values = pixels[:, mask, :].astype(np.float64)
            values -= values.mean(axis=1, keepdims=True)
            norms = np.sqrt(np.einsum("npc,npc->n", values, values, optimize=False)) * prepared[1]
            numerator = np.einsum("npc,pc->n", values, prepared[0].reshape(-1, 3), optimize=False)
            return np.divide(numerator, norms, out=np.zeros_like(numerator), where=norms > 1e-12)

        candidates = []
        dim = template.dim_terrain
        # A per-call batch bounds scratch memory even when several floors are
        # searched concurrently. No float patch arrays stay resident in caches.
        for offset in range(0, len(patches), 8):
            pixels = np.stack(patches[offset : offset + 8])
            full = zncc(pixels, template.mask, template.color_zncc)
            scores = full if dim is None else 0.5 * (full + zncc(pixels, dim[0], dim[2]))
            for index, (x, y, coarse) in enumerate(positions[offset : offset + 8]):
                world_x, world_z = self.calibration.map_to_world(x, y)
                candidates.append(
                    MapCandidate(
                        float(x),
                        float(y),
                        world_x,
                        world_z,
                        float(scores[index]),
                        coarse_score=float(coarse),
                        full_ncc=float(full[index]),
                        inlier_ratio=1.0,
                        color=True,
                    )
                )
        return candidates

    def _candidate_grid(self, shape, *, origin, scale, center):
        mask = getattr(self.map_image, "candidate_mask", None)
        if callable(mask):
            xs = origin[0] + (np.arange(shape[1]) + center[0]) / scale - 0.5
            ys = origin[1] + (np.arange(shape[0]) + center[1]) / scale - 0.5
            return mask(xs, ys)
        return None

    def _mask_candidate_centers(self, scores, **geometry):
        allowed = self._candidate_grid(scores.shape, **geometry)
        if allowed is not None:
            scores[~allowed] = -1.0
        return scores

    def _candidate_center_valid(self, x, y):
        mask = getattr(self.map_image, "candidate_mask", None)
        return not callable(mask) or bool(mask([x], [y])[0, 0])

    def _refine(
        self,
        template: _PreparedTemplate,
        map_x: float,
        map_y: float,
        *,
        scale: float,
        coarse_score: float,
    ) -> MapCandidate | None:
        size = template.ring.shape[0]
        radius = math.ceil((size - 1) / (2.0 * scale)) + self.refine_radius_map_px
        x0 = max(0, round(map_x) - radius)
        y0 = max(0, round(map_y) - radius)
        x1 = min(self.map_image.shape[1], round(map_x) + radius + 1)
        y1 = min(self.map_image.shape[0], round(map_y) + radius + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        local_map = self.map_image[y0:y1, x0:x1]
        if local_map.size == 0:
            return None
        minimum, maximum = local_map.min(), local_map.max()
        if minimum == maximum:
            return None
        local_map = cv2.resize(local_map, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if local_map.shape[0] < size or local_map.shape[1] < size:
            return None
        allowed = self._candidate_grid(
            (local_map.shape[0] - size + 1, local_map.shape[1] - size + 1),
            origin=(x0, y0),
            scale=scale,
            center=(size / 2 + template.center_offset[0], size / 2 + template.center_offset[1]),
        )
        scores = self._match_scores(local_map, template, allowed=allowed)
        if allowed is not None:
            scores[~allowed] = -1.0
        peaks = _top_peaks(scores, max(8, self.top_k), min_distance=1)
        max_distance = self.refine_radius_map_px + 1.0
        patches, positions = [], []
        for _score, (left, top) in peaks:
            dx, dy = _peak_subpixel_offset(scores, left, top)
            # OpenCV resize uses source pixel centers: (u + 0.5) / scale - 0.5.
            refined_x = x0 + (left + dx + size / 2.0 + template.center_offset[0]) / scale - 0.5
            refined_y = y0 + (top + dy + size / 2.0 + template.center_offset[1]) / scale - 0.5
            if math.hypot(refined_x - map_x, refined_y - map_y) > max_distance:
                continue
            if not self._candidate_center_valid(refined_x, refined_y):
                continue
            # Score the same subpixel location that we publish. Scoring the
            # integer crop while reporting an interpolated peak made an
            # unchanged frame cross the acquisition threshold when the ROI
            # origin or search radius shifted by a fraction of a pixel.
            patch = cv2.getRectSubPix(
                local_map,
                (size, size),
                (left + dx + (size - 1) / 2, top + dy + (size - 1) / 2),
            )
            patches.append(patch)
            positions.append((refined_x, refined_y, coarse_score))
        candidates = self._candidates_from_patches(template, patches, positions)
        return max(candidates, key=lambda candidate: candidate.ncc, default=None)

    def _status(self, candidates: list[MapCandidate]) -> tuple[str, str]:
        if not candidates:
            return "rejected", "no candidate"
        best = candidates[0].ncc
        margin = candidate_margin(candidates)
        if best < self.min_valid_ncc:
            return "rejected", f"low NCC {best:.3f}"
        if best >= self.min_accept_ncc and margin >= self.min_accept_margin:
            return "accepted", "high NCC with clear margin"
        return "ambiguous", f"NCC={best:.3f}, margin={margin:.3f}"

    def proposal_background_score(self, template, frame_width, prior, *, radius=8.0):
        """Score a bounded ordering hint without producing a position or evidence."""
        mask = template.proposal_dim_mask
        if mask is None:
            return 0.0
        scale = self._scale(frame_width)
        px, py = self.calibration.world_to_map(*prior)
        size = template.ring.shape[0]
        radii = radius * np.linalg.norm(self.calibration.inverse_matrix[:, :2], axis=1)
        mx, my = (math.ceil(value + size / (2 * scale)) for value in radii)
        x0, y0 = max(0, round(px) - mx), max(0, round(py) - my)
        x1 = min(self.map_image.shape[1], round(px) + mx + 1)
        y1 = min(self.map_image.shape[0], round(py) + my + 1)
        if min(round((x1 - x0) * scale), round((y1 - y0) * scale)) < size:
            return 0.0
        image = cv2.resize(self.map_image[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        scores = correlate_color_zncc(image, template.ring, masks=(mask.astype(np.uint8) * 255,), compact=True)
        return float(np.clip(scores.max(), 0, 1))

    def locate_global(
        self,
        frame: np.ndarray | None,
        geometry: MinimapGeometry,
    ) -> LocateResult | None:
        """Search the complete calibrated map and refine the top candidates."""

        start = time.perf_counter()
        template = self._prepare_template(frame, geometry)
        if template is None:
            return None
        scale = self._scale(frame.shape[1])
        coarse_scale = scale * self.coarse_factor
        coarse_map = self._coarse_map(coarse_scale)
        target = max(4, round(template.flat.shape[0] * self.coarse_factor))
        if coarse_map.shape[0] < target or coarse_map.shape[1] < target:
            return self._reject(f"minimap_map_too_small: map={coarse_map.shape} template={target}")
        allowed = self._candidate_grid(
            (coarse_map.shape[0] - target + 1, coarse_map.shape[1] - target + 1),
            origin=(0, 0),
            scale=coarse_scale,
            center=(target / 2, target / 2),
        )
        scores = self._match_scores(coarse_map, template, coarse=True, allowed=allowed)
        if allowed is not None:
            scores[~allowed] = -1.0
        peaks = _top_peaks(scores, self.top_k, min_distance=max(4, target // 2))
        candidates = []
        for coarse_score, (left, top) in peaks:
            map_x = (left + target / 2.0) / coarse_scale - 0.5
            map_y = (top + target / 2.0) / coarse_scale - 0.5
            refined = self._refine(
                template,
                map_x,
                map_y,
                scale=scale,
                coarse_score=coarse_score,
            )
            if refined is not None:
                candidates.append(refined)
        candidates = deduplicate_candidates(candidates, min_world_distance_m=self.candidate_merge_distance_m)[
            : self.top_k
        ]
        status, reason = self._status(candidates)
        if not candidates:
            return self._reject(f"minimap_no_candidates: search=global peaks={len(peaks)}")
        return LocateResult(
            status=status,
            reason=reason,
            best=candidates[0],
            candidates=tuple(candidates),
            elapsed_s=time.perf_counter() - start,
            scale=scale,
            used_prior=False,
        )

    def locate_near(
        self,
        frame: np.ndarray | None,
        previous_world: tuple[float, float],
        geometry: MinimapGeometry,
        *,
        search_radius_m: float = 80.0,
    ) -> LocateResult | None:
        """Locate again inside a bounded region around the previous world pose."""

        start = time.perf_counter()
        template = self._prepare_template(frame, geometry)
        if template is None:
            return None
        return self._locate_near_template(template, frame.shape[1], previous_world, search_radius_m, start=start)

    def _locate_near_template(self, template, frame_width, previous_world, search_radius_m, *, start=None):
        if start is None:
            start = time.perf_counter()
        self.last_failure_reason = ""
        if search_radius_m <= 0:
            return self._reject("minimap_invalid_search_radius")
        scale = self._scale(frame_width)
        previous_x, previous_y = self.calibration.world_to_map(*previous_world)
        inverse_linear = self.calibration.inverse_matrix[:, :2]
        radii_map = float(search_radius_m) * np.linalg.norm(inverse_linear, axis=1)
        size = template.ring.shape[0]
        margin_x, margin_y = (math.ceil(radius + size / (2.0 * scale)) for radius in radii_map)
        x0 = max(0, round(previous_x) - margin_x)
        y0 = max(0, round(previous_y) - margin_y)
        x1 = min(self.map_image.shape[1], round(previous_x) + margin_x + 1)
        y1 = min(self.map_image.shape[0], round(previous_y) + margin_y + 1)
        if x1 <= x0 or y1 <= y0:
            return self._reject("minimap_search_outside_map")
        resized_shape = (round((y1 - y0) * scale), round((x1 - x0) * scale))
        if min(resized_shape) < size:
            return self._reject(f"minimap_map_too_small: map={resized_shape} template={size}")
        allowed = self._candidate_grid(
            (resized_shape[0] - size + 1, resized_shape[1] - size + 1),
            origin=(x0, y0),
            scale=scale,
            center=(size / 2 + template.center_offset[0], size / 2 + template.center_offset[1]),
        )
        if allowed is not None and not allowed.any():
            return self._reject("minimap_search_region_uncovered")
        if allowed is not None and not self._coverage_reaches_radius(
            allowed, (x0, y0), scale, size, template.center_offset, previous_world, search_radius_m
        ):
            return self._reject("minimap_search_region_uncovered")
        local_map = self.map_image[y0:y1, x0:x1]
        if local_map.size == 0:
            return self._reject("minimap_search_outside_map")
        minimum, maximum = local_map.min(), local_map.max()
        if minimum == maximum:
            return self._reject("minimap_search_region_blank")
        local_map = cv2.resize(local_map, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if local_map.shape[0] < size or local_map.shape[1] < size:
            return self._reject(f"minimap_map_too_small: map={local_map.shape} template={size}")
        scores = self._match_scores(local_map, template, allowed=allowed)
        if allowed is not None:
            scores[~allowed] = -1.0
        peak_distance = max(
            2,
            math.ceil(self.candidate_merge_distance_m * np.linalg.svd(inverse_linear, compute_uv=False)[-1] * scale),
        )
        peaks = _top_peaks(scores, max(16, self.top_k * 3), min_distance=peak_distance)
        patches, positions = [], []
        for coarse_score, (left, top) in peaks:
            dx, dy = _peak_subpixel_offset(scores, left, top)
            map_x = x0 + (left + dx + size / 2.0 + template.center_offset[0]) / scale - 0.5
            map_y = y0 + (top + dy + size / 2.0 + template.center_offset[1]) / scale - 0.5
            if math.dist(self.calibration.map_to_world(map_x, map_y), previous_world) > search_radius_m:
                continue
            if not self._candidate_center_valid(map_x, map_y):
                continue
            patch = cv2.getRectSubPix(
                local_map,
                (size, size),
                (left + dx + (size - 1) / 2, top + dy + (size - 1) / 2),
            )
            patches.append(patch)
            positions.append((map_x, map_y, coarse_score))
        candidates = self._candidates_from_patches(template, patches, positions)
        candidates = deduplicate_candidates(candidates, min_world_distance_m=self.candidate_merge_distance_m)[
            : self.top_k
        ]
        status, reason = self._status(candidates)
        if not candidates:
            return self._reject(f"minimap_no_candidates: search=local peaks={len(peaks)}")
        return LocateResult(
            status=status,
            reason=reason,
            best=candidates[0],
            candidates=tuple(candidates),
            elapsed_s=time.perf_counter() - start,
            scale=scale,
            used_prior=True,
        )

    def _coverage_reaches_radius(self, allowed, origin, scale, size, center_offset, world, radius):
        """Reject a whole floor only if no subpixel peak can enter the circle."""
        rows, cols = np.nonzero(allowed)
        x = origin[0] + (cols + size / 2 + center_offset[0]) / scale - 0.5
        y = origin[1] + (rows + size / 2 + center_offset[1]) / scale - 0.5
        a = self.calibration.matrix
        dx = a[0, 0] * x + a[0, 1] * y + a[0, 2] - world[0]
        dz = a[1, 0] * x + a[1, 1] * y + a[1, 2] - world[1]
        # Peak interpolation can move by half a score pixel in each axis.
        # Keep that entire affine uncertainty envelope, even at circle edges.
        slack = np.linalg.norm(a[:, :2], ord=2) * math.sqrt(0.5) / scale
        return bool(np.any(dx * dx + dz * dz <= (radius + slack + 1e-9) ** 2))


class MinimapTracker:
    """Go-MapTracker-style convinced/pending state around a visual locator.

    A single low-confidence frame must not replace a trusted position. The tracker keeps
    a ``convinced`` pose and a ``pending`` candidate:

    * acquisition/reacquisition requires high robust and full-ring NCC, a distinct
      alternative-candidate margin, and at least two consistent valid frames;
    * a candidate close to a fresh ``convinced`` refreshes it even when the locator reports
      ``ambiguous``;
    * a candidate far from ``convinced`` must be seen consistently for ``promote_hits``
      frames, or for ``promote_window_s`` seconds, before it is promoted;
    * fresh poses use a radius bounded by elapsed time and maximum speed; weak
      local evidence retries the full configured radius, then rejection triggers global search;
    * pending observations separated by more than ``pending_max_gap_s`` restart confirmation;
    * ``prior_world`` may seed a lost tracker from the packet-capture position. The
      backend position is only a search prior; the minimap result remains the final pose.
    """

    def __init__(
        self,
        locator: MinimapMapLocator,
        *,
        promote_hits: int = DEFAULT_PROMOTE_HITS,
        promote_window_s: float = DEFAULT_PROMOTE_WINDOW_S,
        consistency_distance_m: float = DEFAULT_CONSISTENCY_DISTANCE_M,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        search_radius_m: float = 80.0,
        min_search_radius_m: float = 8.0,
        max_speed_m_s: float = 20.0,
        pending_max_gap_s: float = 0.5,
        initial_min_ncc: float | None = None,
        initial_min_full_ncc: float | None = None,
        initial_min_margin: float | None = None,
    ) -> None:
        if promote_hits < 1:
            raise ValueError("promote_hits must be >= 1")
        if promote_window_s < 0 or consistency_distance_m <= 0 or stale_after_s < 0 or search_radius_m <= 0:
            raise ValueError("tracker thresholds must be non-negative/positive as appropriate")
        if not all(
            math.isfinite(v) and v > 0
            for v in (
                min_search_radius_m,
                max_speed_m_s,
                pending_max_gap_s,
            )
        ):
            raise ValueError("adaptive search and pending thresholds must be finite and positive")
        if not all(
            v is None or (math.isfinite(v) and 0 <= v <= 1)
            for v in (
                initial_min_ncc,
                initial_min_full_ncc,
                initial_min_margin,
            )
        ):
            raise ValueError("initial confidence thresholds must be finite and in [0, 1]")
        color = getattr(locator, "color", False) is True
        self.initial_min_ncc = initial_min_ncc if initial_min_ncc is not None else (0.45 if color else 0.85)
        self.initial_min_full_ncc = (
            initial_min_full_ncc if initial_min_full_ncc is not None else (0.45 if color else 0.65)
        )
        self.initial_min_margin = initial_min_margin if initial_min_margin is not None else (0.10 if color else 0.08)
        self.min_search_radius_m = min(float(min_search_radius_m), float(search_radius_m))
        self.max_speed_m_s = float(max_speed_m_s)
        self.pending_max_gap_s = float(pending_max_gap_s)
        self.locator = locator
        self.promote_hits = int(promote_hits)
        self.promote_window_s = float(promote_window_s)
        self.consistency_distance_m = float(consistency_distance_m)
        self.stale_after_s = float(stale_after_s)
        self.search_radius_m = float(search_radius_m)
        self._convinced: MapCandidate | None = None
        self._convinced_t: float | None = None
        self._pending: MapCandidate | None = None
        self._pending_hits = 0
        self._pending_since: float | None = None
        self._last_update_t: float | None = None
        self._prediction_verified_t: float | None = None
        self._prediction_verified: MapCandidate | None = None
        self._prediction_margin = 0.0

    @property
    def convinced(self) -> MapCandidate | None:
        return self._convinced

    @property
    def pending(self) -> MapCandidate | None:
        return self._pending

    def reset(self) -> None:
        self._convinced = None
        self._convinced_t = None
        self._last_update_t = None
        self._pending = None
        self._pending_hits = 0
        self._pending_since = None
        self._prediction_verified_t = None
        self._prediction_verified = None
        self._prediction_margin = 0.0

    @staticmethod
    def _distance(left: MapCandidate, right: MapCandidate) -> float:
        if left.tier_id != right.tier_id or left.map_id != right.map_id:
            return math.inf
        return math.hypot(left.world_x - right.world_x, left.world_z - right.world_z)

    def _set_convinced(self, candidate: MapCandidate, now: float) -> None:
        self._convinced = candidate
        self._convinced_t = float(now)
        self._pending = None
        self._pending_hits = 0
        self._pending_since = None

    def confirm_visual_continuity(
        self, result: TrackResult, predicted_world: tuple[float, float], *, now: float
    ) -> TrackResult:
        """Revalidate a short sampling gap using separately verified visual motion.

        The caller must supply a valid phase-correlation measurement, never a
        stale position or packet prior. This cannot acquire an initial fix or
        change map/floor. Every accepted coordinate still comes from this frame.
        """
        previous, previous_t = self._convinced, self._convinced_t
        locate = result.locate
        if (
            result.status != "pending"
            or previous is None
            or previous_t is None
            or locate is None
            or self._last_update_t != now
            or not self.stale_after_s < now - previous_t <= 4.0
        ):
            return result
        best = locate.best
        if (
            best.map_id != previous.map_id
            or best.tier_id is None
            or best.tier_id != previous.tier_id
            or not strong_candidate(
                best, locate.margin, self.initial_min_ncc, self.initial_min_full_ncc, self.initial_min_margin
            )
            or not all(math.isfinite(v) for v in predicted_world)
            or math.hypot(best.world_x - predicted_world[0], best.world_z - predicted_world[1]) > 1.0
            or self._distance(best, previous) > self.consistency_distance_m + self.max_speed_m_s * (now - previous_t)
        ):
            return result
        self._set_convinced(best, now)
        return TrackResult("convinced", True, "visual_motion_confirmed", locate, best, 0, result.used_prior)

    def update(
        self,
        frame: np.ndarray | None,
        geometry: MinimapGeometry,
        *,
        now: float | None = None,
        prior_world: tuple[float, float] | None = None,
        prior_radius_m: float = 50.0,
        prediction_world: tuple[float, float] | None = None,
        prediction_radius_m: float = 35.0,
        recovery_world: tuple[float, float] | None = None,
        recovery_map_id: str | None = None,
    ) -> TrackResult:
        """Run one visual frame and update the convinced/pending state."""

        current = time.monotonic() if now is None else float(now)
        if not math.isfinite(current):
            raise ValueError("now must be finite")
        if self._last_update_t is not None and (
            current < self._last_update_t or current - self._last_update_t > self.pending_max_gap_s
        ):
            self._pending = None
            self._pending_hits = 0
            self._pending_since = None
            if current < self._last_update_t:
                self.reset()
        previous_update = self._last_update_t
        self._last_update_t = current
        fresh = (
            self._convinced is not None
            and self._convinced_t is not None
            and 0 <= current - self._convinced_t <= self.stale_after_s
        )
        if recovery_world is not None:
            # A live visual chain supplies a search region, never an absolute
            # observation. Keep incomplete local results instead of escalating
            # to all maps/floors when the last absolute fix becomes stale.
            result = self._locate_recovery(frame, geometry, recovery_world, recovery_map_id, current, fresh)
            used_prior, used_prediction = True, False
            if result is not None and result.status != "accepted":
                result = replace(result, status="rejected")
        else:
            result, used_prior, used_prediction = self._locate_frame(
                frame, geometry, current, fresh, prior_world, prior_radius_m, prediction_world, prediction_radius_m
            )
        if result is None:
            # Invalid/absent minimaps must invalidate even a recently trusted pose.
            self.reset()
            reason = getattr(self.locator, "last_failure_reason", "") or "no_valid_minimap_or_match"
            return TrackResult("lost", False, reason, None, None, 0, used_prior)
        if used_prediction:
            result = self._verify_prediction_competition(frame, geometry, result, current)
        if result.status == "rejected":
            self._pending = None
            self._pending_hits = 0
            self._pending_since = None
            return TrackResult("lost", False, result.reason, result, None, 0, used_prior)

        best = result.best
        convinced_radius = self.consistency_distance_m + self.max_speed_m_s * (
            max(0.0, current - self._convinced_t) if fresh else 0.0
        )
        if (
            fresh
            and (not used_prediction or result.margin >= self.initial_min_margin)
            and self._distance(best, self._convinced) <= convinced_radius
        ):
            self._set_convinced(best, current)
            return TrackResult("convinced", True, result.reason, result, best, 0, used_prior)

        return self._confirm_acquisition(result, current, previous_update, used_prior)

    def _locate_recovery(self, frame, geometry, world, map_id, now, fresh):
        """Track near a fresh anchor; periodically recheck the full recovery ROI.

        Only a recent accepted wide search can authorize the small window.
        Weak evidence, a different map/floor, or expiration forces a same-frame
        wide check. An unresolved visual chain stays on its anchored map.
        """
        locate_on_map = getattr(self.locator, "locate_on_map", None)

        def search(radius):
            if callable(locate_on_map):
                return locate_on_map(frame, map_id, world, geometry, search_radius_m=radius)
            return self.locator.locate_near(frame, world, geometry, search_radius_m=radius)

        verified = self._prediction_verified
        if (
            fresh
            and verified is not None
            and self._prediction_verified_t is not None
            and 0 <= now - self._prediction_verified_t <= self.pending_max_gap_s
            and self._distance(verified, self._convinced) <= self.consistency_distance_m
            and verified.map_id == map_id
            and self._prediction_margin >= self.initial_min_margin
        ):
            radius = min(
                self.search_radius_m, self.min_search_radius_m + self.max_speed_m_s * (now - self._convinced_t)
            )
            result = search(radius)
            if (
                result is not None
                and result.status == "accepted"
                and strong_candidate(
                    result.best, result.margin, self.initial_min_ncc, self.initial_min_full_ncc, self.initial_min_margin
                )
                and self._distance(result.best, verified) <= self.consistency_distance_m
            ):
                return replace(result, spatial_margin=min(result.margin, self._prediction_margin))
        self._prediction_verified = self._prediction_verified_t = None
        self._prediction_margin = 0.0
        result = search(self.search_radius_m)
        if (
            result is not None
            and result.status == "accepted"
            and strong_candidate(
                result.best, result.margin, self.initial_min_ncc, self.initial_min_full_ncc, self.initial_min_margin
            )
        ):
            self._prediction_verified, self._prediction_verified_t = result.best, now
            self._prediction_margin = result.margin
        return result

    def _verify_prediction_competition(self, frame, geometry, result, now):
        """Require an accepted predicted peak to survive the normal wider search."""

        best = result.best
        if (
            self._prediction_verified is not None
            and self._prediction_verified_t is not None
            and now - self._prediction_verified_t <= self.pending_max_gap_s
            and self._distance(best, self._prediction_verified) <= self.consistency_distance_m
            and self._prediction_margin >= self.initial_min_margin
        ):
            return replace(
                result,
                reason=f"prediction_cached_{result.reason}",
                spatial_margin=min(result.margin, self._prediction_margin),
            )
        wide = self.locator.locate_near(
            frame,
            (best.world_x, best.world_z),
            geometry,
            search_radius_m=self.search_radius_m,
        )
        self._prediction_verified = None
        self._prediction_verified_t = None
        self._prediction_margin = 0.0
        if wide is None:
            return replace(result, status="rejected", reason="prediction_competition_unavailable", spatial_margin=0.0)
        if wide.status != "accepted":
            # An ambiguous/incomplete search cannot prove absence of competitors.
            # Keep the local observation for visual propagation, never promotion.
            return replace(result, status="rejected", reason=f"prediction_{wide.reason}", spatial_margin=0.0)
        competitors = [
            candidate
            for candidate in wide.candidates
            if candidate.map_id != best.map_id
            or candidate.tier_id != best.tier_id
            or math.hypot(candidate.world_x - best.world_x, candidate.world_z - best.world_z)
            > self.consistency_distance_m
        ]
        wide_margin = min(wide.margin, candidate_margin([best, *competitors]))
        if wide_margin >= self.initial_min_margin:
            self._prediction_verified_t = now
            self._prediction_verified = best
            self._prediction_margin = wide_margin
        return replace(
            result,
            reason=f"prediction_{wide.reason}",
            candidates=tuple(deduplicate_candidates([*result.candidates, *competitors])),
            spatial_margin=min(result.margin, wide_margin),
        )

    def _locate_frame(
        self, frame, geometry, current, fresh, prior_world, prior_radius_m, prediction_world, prediction_radius_m
    ):
        used_prior = False
        used_prediction = False
        result, used_prediction = self._locate_prediction(
            frame,
            geometry,
            prediction_world,
            prediction_radius_m,
        )
        used_prior = used_prediction
        if result is None and fresh:
            elapsed = current - self._convinced_t
            radius = min(self.search_radius_m, self.min_search_radius_m + self.max_speed_m_s * elapsed)
            center = (self._convinced.world_x, self._convinced.world_z)
            result = self.locator.locate_near(frame, center, geometry, search_radius_m=radius)
            used_prior = True
            # Weak evidence in a narrow window must not conceal a better distant match.
            if (result is None or result.status != "accepted") and radius < self.search_radius_m:
                result = self.locator.locate_near(
                    frame,
                    center,
                    geometry,
                    search_radius_m=self.search_radius_m,
                )
            if result is None or result.status == "rejected":
                result = self.locator.locate_global(frame, geometry)
                used_prior = False
        elif result is None and prior_world is not None:
            result = self.locator.locate_near(
                frame,
                prior_world,
                geometry,
                search_radius_m=max(1.0, float(prior_radius_m)),
            )
            used_prior = True
        elif result is None:
            result = self.locator.locate_global(frame, geometry)

        return result, used_prior, used_prediction

    def _locate_prediction(self, frame, geometry, prediction_world, prediction_radius_m):
        if prediction_world is None:
            return None, False
        result = self.locator.locate_near(
            frame,
            prediction_world,
            geometry,
            search_radius_m=max(1.0, float(prediction_radius_m)),
        )
        if result is None or result.status != "accepted":
            return None, False
        return result, True

    def _confirm_acquisition(self, result, current, previous_update, used_prior):
        best = result.best
        # Time alone cannot turn a repeated weak or ambiguous distant match into a fix.
        if not strong_candidate(
            best, result.margin, self.initial_min_ncc, self.initial_min_full_ncc, self.initial_min_margin
        ):
            self._pending = None
            self._pending_hits = 0
            self._pending_since = None
            return TrackResult("lost", False, "insufficient_acquisition_evidence", result, None, 0, used_prior)

        pending_radius = self.consistency_distance_m + self.max_speed_m_s * (
            max(0.0, current - previous_update) if previous_update is not None else 0.0
        )
        if self._pending is not None and self._distance(best, self._pending) <= pending_radius:
            self._pending = best
            self._pending_hits += 1
        else:
            self._pending = best
            self._pending_hits = 1
            self._pending_since = current

        elapsed = 0.0 if self._pending_since is None else current - self._pending_since
        promoted = self._pending_hits >= max(2, self.promote_hits) or (
            self._pending_hits >= 2 and elapsed >= self.promote_window_s
        )
        if promoted:
            hits = self._pending_hits
            self._set_convinced(best, current)
            return TrackResult("convinced", True, "temporal_promotion", result, best, hits, used_prior)
        return TrackResult(
            "pending",
            False,
            result.reason,
            result,
            None,
            self._pending_hits,
            used_prior,
        )


__all__ = [
    "DEFAULT_CANDIDATE_MERGE_DISTANCE_M",
    "DEFAULT_CONSISTENCY_DISTANCE_M",
    "DEFAULT_PROMOTE_HITS",
    "DEFAULT_PROMOTE_WINDOW_S",
    "DEFAULT_REFINE_RADIUS_MAP_PX",
    "DEFAULT_ROBUST_TRIM_RATIO",
    "DEFAULT_STALE_AFTER_S",
    "DEFAULT_TOP_K",
    "MAP_PIXELS_PER_METER",
    "MIN_ACCEPT_MARGIN",
    "MIN_ACCEPT_NCC",
    "MIN_VALID_NCC",
    "LocateResult",
    "MapCalibration",
    "MapCandidate",
    "MinimapGeometry",
    "MinimapMapLocator",
    "MinimapTracker",
    "NccScore",
    "TrackResult",
    "deduplicate_candidates",
    "masked_zncc",
    "minimap_map_scale",
    "robust_masked_zncc",
]
