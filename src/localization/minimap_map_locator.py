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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.core.NavConfig import DEFAULT_SCALE_CONSTANT
from src.localization.minimap_odometry import (
    DEFAULT_CENTER_RATIO,
    DEFAULT_R_OUTER_RATIO,
    annulus_mask,
    region_geometry,
)

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
            map_name=str(data.get("map_name") or Path(path).stem),
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
        }


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
    if selected.shape != left.shape or selected.shape != right.shape:
        raise ValueError("ZNCC inputs and mask must have the same shape")
    if not selected.any():
        return 0.0
    left_values = np.asarray(left, dtype=np.float64)[selected]
    right_values = np.asarray(right, dtype=np.float64)[selected]
    return _zncc_values(left_values, right_values)


_DOT_EXPRESSION = "i,i->"


def _zncc_values(left_values: np.ndarray, right_values: np.ndarray) -> float:
    if left_values.size == 0 or right_values.size == 0:
        return 0.0
    left_values = np.asarray(left_values, dtype=np.float64)
    right_values = np.asarray(right_values, dtype=np.float64)
    left_values -= left_values.mean()
    right_values -= right_values.mean()
    # These are short 1-D reductions repeated for every candidate. Dispatching
    # them to a process-wide BLAS thread pool adds latency on the control path.
    left_sq = np.einsum(_DOT_EXPRESSION, left_values, left_values, optimize=False)
    right_sq = np.einsum(_DOT_EXPRESSION, right_values, right_values, optimize=False)
    denominator = math.sqrt(float(left_sq)) * math.sqrt(float(right_sq))
    if denominator <= 1e-12:
        return 0.0
    return float(np.einsum(_DOT_EXPRESSION, left_values, right_values, optimize=False) / denominator)


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
    if selected.shape != left.shape or selected.shape != right.shape:
        raise ValueError("ZNCC inputs and mask must have the same shape")
    if not selected.any():
        return NccScore(0.0, 0.0, 0.0)
    left_values = np.asarray(left, dtype=np.float64)[selected]
    right_values = np.asarray(right, dtype=np.float64)[selected]
    full_score = _zncc_values(left_values, right_values)
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


def _has_minimap_texture(ring: np.ndarray, center: tuple[float, float], radius: float) -> bool:
    """Conservative plausibility gate, not a semantic guarantee that HUD is visible.

    Loading-logo edges may have high contrast but only occupy a few sectors.
    Require terrain detail in at least six sectors; color is intentionally irrelevant.
    """
    sectors = _texture_sectors(ring.shape[0], center, radius)
    if any(not sector.any() for sector in sectors):
        return False
    detail = cv2.absdiff(ring, cv2.GaussianBlur(ring, (5, 5), 0))
    return sum(float(detail[sector].mean()) >= 1.0 for sector in sectors) >= 6


def _sanitize_scores(scores: np.ndarray) -> np.ndarray:
    return np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0)


def _top_peaks(scores: np.ndarray, count: int, *, min_distance: int) -> list[tuple[float, tuple[int, int]]]:
    if scores.size == 0 or count <= 0:
        return []
    work = _sanitize_scores(scores).copy()
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
        map_image: np.ndarray,
        calibration: MapCalibration,
        *,
        scale_override: float | None = None,
        coarse_factor: float = COARSE_FACTOR,
        refine_radius_map_px: int = DEFAULT_REFINE_RADIUS_MAP_PX,
        top_k: int = DEFAULT_TOP_K,
        candidate_merge_distance_m: float = DEFAULT_CANDIDATE_MERGE_DISTANCE_M,
        robust_trim_ratio: float = DEFAULT_ROBUST_TRIM_RATIO,
        min_valid_ncc: float = MIN_VALID_NCC,
        min_accept_ncc: float = MIN_ACCEPT_NCC,
        min_accept_margin: float = MIN_ACCEPT_MARGIN,
    ) -> None:
        gray = _as_gray(map_image)
        if gray is None:
            raise ValueError("Map image could not be converted to grayscale")
        if not 0 < coarse_factor <= 1:
            raise ValueError("coarse_factor must be in (0, 1]")
        if scale_override is not None and scale_override <= 0:
            raise ValueError("scale_override must be positive")
        self.map_image = gray
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
        self.min_valid_ncc = float(min_valid_ncc)
        self.min_accept_ncc = float(min_accept_ncc)
        self.min_accept_margin = float(min_accept_margin)
        self._mask_cache: OrderedDict[MinimapGeometry, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._coarse_cache: dict[tuple[float, float], np.ndarray] = {}

    @classmethod
    def from_files(
        cls,
        map_path: str | Path,
        mapping_path: str | Path,
        **kwargs: Any,
    ) -> MinimapMapLocator:
        map_image = cv2.imread(str(map_path), cv2.IMREAD_GRAYSCALE)
        if map_image is None:
            raise ValueError(f"Could not read map image: {map_path}")
        calibration = MapCalibration.from_mapping_file(mapping_path)
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
        if frame is None or not isinstance(frame, np.ndarray) or frame.ndim < 2:
            return None
        height, width = frame.shape[:2]
        half = math.ceil(geometry.match_outer_radius)
        center_x = round(geometry.center_x)
        center_y = round(geometry.center_y)
        x0, y0 = center_x - half, center_y - half
        x1, y1 = center_x + half + 1, center_y + half + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return None
        cached = self._geometry_masks(geometry, half, x0, y0)
        if cached is None:
            return None
        mask, mask8 = cached
        roi = frame[y0:y1, x0:x1]
        ring = _as_gray(roi).copy()
        if not _has_minimap_texture(ring, (geometry.center_x - x0, geometry.center_y - y0), geometry.outer_radius):
            return None
        visible = self._visible_masks(roi, mask, mask8, half)
        if visible is None:
            return None
        mask, mask8 = visible
        mean = float(ring[mask].mean())
        flat = ring.copy()
        flat[~mask] = mean
        if not _has_minimap_texture(flat, (geometry.center_x - x0, geometry.center_y - y0), geometry.outer_radius):
            visible = self._legacy_visible_masks(roi, cached[0], cached[1], half)
            if visible is None:
                return None
            mask, mask8 = visible
            flat = ring.copy()
            flat[~mask] = float(ring[mask].mean())
            if not _has_minimap_texture(flat, (geometry.center_x - x0, geometry.center_y - y0), geometry.outer_radius):
                return None
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
        return visible if visible is not None else MinimapMapLocator._legacy_visible_masks(roi, mask, mask8, half)

    @staticmethod
    def _legacy_visible_masks(roi, mask, mask8, half):
        """Retain the original frame-only mask if wider glyph padding erases terrain."""
        if roi.ndim != 3 or roi.shape[2] < 3:
            return mask, mask8
        colors = roi[:, :, :3]
        high, low = colors.max(axis=2), colors.min(axis=2)
        overlay = (low >= 210) | ((high >= 190) & (high.astype(np.int16) - low > 90))
        if not np.any(overlay & mask):
            return mask, mask8
        overlay = cv2.dilate(overlay.astype(np.uint8), np.ones((3, 3), np.uint8))
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

    def _coarse_map(self, coarse_scale: float) -> np.ndarray:
        key = (self.coarse_factor, coarse_scale)
        cached = self._coarse_cache.get(key)
        if cached is None:
            cached = cv2.resize(
                self.map_image,
                None,
                fx=coarse_scale,
                fy=coarse_scale,
                interpolation=cv2.INTER_AREA,
            )
            self._coarse_cache[key] = cached
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
        )

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
        local_map = self.map_image[y0:y1, x0:x1]
        if local_map.size == 0:
            return None
        minimum, maximum, _min_location, _max_location = cv2.minMaxLoc(local_map)
        if minimum == maximum:
            return None
        local_map = cv2.resize(local_map, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if local_map.shape[0] < size or local_map.shape[1] < size:
            return None
        scores = cv2.matchTemplate(
            local_map,
            template.ring,
            cv2.TM_CCORR_NORMED,
            mask=template.mask8,
        )
        scores = _sanitize_scores(scores)
        peaks = _top_peaks(scores, max(8, self.top_k), min_distance=1)
        max_distance = self.refine_radius_map_px + 1.0
        candidates = []
        for _score, (left, top) in peaks:
            dx, dy = _peak_subpixel_offset(scores, left, top)
            # OpenCV resize uses source pixel centers: (u + 0.5) / scale - 0.5.
            refined_x = x0 + (left + dx + size / 2.0 + template.center_offset[0]) / scale - 0.5
            refined_y = y0 + (top + dy + size / 2.0 + template.center_offset[1]) / scale - 0.5
            if math.hypot(refined_x - map_x, refined_y - map_y) > max_distance:
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
            candidates.append(
                self._candidate_from_patch(
                    template,
                    patch,
                    refined_x,
                    refined_y,
                    coarse_score=coarse_score,
                )
            )
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
        coarse_template = cv2.resize(
            template.flat,
            (target, target),
            interpolation=cv2.INTER_AREA,
        )
        if coarse_map.shape[0] < target or coarse_map.shape[1] < target:
            return None
        scores = _sanitize_scores(cv2.matchTemplate(coarse_map, coarse_template, cv2.TM_CCOEFF_NORMED))
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
        candidates = deduplicate_candidates(
            candidates,
            min_world_distance_m=self.candidate_merge_distance_m,
        )[: self.top_k]
        status, reason = self._status(candidates)
        if not candidates:
            return None
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
        if template is None or search_radius_m <= 0:
            return None
        scale = self._scale(frame.shape[1])
        previous_x, previous_y = self.calibration.world_to_map(*previous_world)
        inverse_linear = self.calibration.inverse_matrix[:, :2]
        radii_map = float(search_radius_m) * np.linalg.norm(inverse_linear, axis=1)
        size = template.ring.shape[0]
        margin_x, margin_y = (math.ceil(radius + size / (2.0 * scale)) for radius in radii_map)
        x0 = max(0, round(previous_x) - margin_x)
        y0 = max(0, round(previous_y) - margin_y)
        x1 = min(self.map_image.shape[1], round(previous_x) + margin_x + 1)
        y1 = min(self.map_image.shape[0], round(previous_y) + margin_y + 1)
        local_map = self.map_image[y0:y1, x0:x1]
        if local_map.size == 0:
            return None
        minimum, maximum, _min_location, _max_location = cv2.minMaxLoc(local_map)
        if minimum == maximum:
            return None
        local_map = cv2.resize(local_map, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if local_map.shape[0] < size or local_map.shape[1] < size:
            return None
        scores = _sanitize_scores(
            cv2.matchTemplate(
                local_map,
                template.ring,
                cv2.TM_CCORR_NORMED,
                mask=template.mask8,
            )
        )
        peak_distance = max(
            2,
            math.ceil(self.candidate_merge_distance_m * np.linalg.svd(inverse_linear, compute_uv=False)[-1] * scale),
        )
        peaks = _top_peaks(scores, max(16, self.top_k * 3), min_distance=peak_distance)
        candidates = []
        for coarse_score, (left, top) in peaks:
            dx, dy = _peak_subpixel_offset(scores, left, top)
            map_x = x0 + (left + dx + size / 2.0 + template.center_offset[0]) / scale - 0.5
            map_y = y0 + (top + dy + size / 2.0 + template.center_offset[1]) / scale - 0.5
            if math.dist(self.calibration.map_to_world(map_x, map_y), previous_world) > search_radius_m:
                continue
            patch = cv2.getRectSubPix(
                local_map,
                (size, size),
                (left + dx + (size - 1) / 2, top + dy + (size - 1) / 2),
            )
            candidates.append(
                self._candidate_from_patch(
                    template,
                    patch,
                    map_x,
                    map_y,
                    coarse_score=coarse_score,
                )
            )
        candidates = deduplicate_candidates(
            candidates,
            min_world_distance_m=self.candidate_merge_distance_m,
        )[: self.top_k]
        status, reason = self._status(candidates)
        if not candidates:
            return None
        return LocateResult(
            status=status,
            reason=reason,
            best=candidates[0],
            candidates=tuple(candidates),
            elapsed_s=time.perf_counter() - start,
            scale=scale,
            used_prior=True,
        )


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
        initial_min_ncc: float = 0.85,
        initial_min_full_ncc: float = 0.65,
        initial_min_margin: float = 0.08,
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
            math.isfinite(v) and 0 <= v <= 1
            for v in (
                initial_min_ncc,
                initial_min_full_ncc,
                initial_min_margin,
            )
        ):
            raise ValueError("initial confidence thresholds must be finite and in [0, 1]")
        self.initial_min_ncc = float(initial_min_ncc)
        self.initial_min_full_ncc = float(initial_min_full_ncc)
        self.initial_min_margin = float(initial_min_margin)
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
            or best.ncc < self.initial_min_ncc
            or best.full_ncc < self.initial_min_full_ncc
            or locate.margin < self.initial_min_margin
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
        result, used_prior = self._locate_frame(frame, geometry, current, fresh, prior_world, prior_radius_m)
        if result is None:
            # Invalid/absent minimaps must invalidate even a recently trusted pose.
            self.reset()
            return TrackResult("lost", False, "no_valid_minimap_or_match", None, None, 0, used_prior)
        if result.status == "rejected":
            self._pending = None
            self._pending_hits = 0
            self._pending_since = None
            return TrackResult("lost", False, result.reason, result, None, 0, used_prior)

        best = result.best
        convinced_radius = self.consistency_distance_m + self.max_speed_m_s * (
            max(0.0, current - self._convinced_t) if fresh else 0.0
        )
        if fresh and self._distance(best, self._convinced) <= convinced_radius:
            self._set_convinced(best, current)
            return TrackResult("convinced", True, result.reason, result, best, 0, used_prior)

        return self._confirm_acquisition(result, current, previous_update, used_prior)

    def _locate_frame(self, frame, geometry, current, fresh, prior_world, prior_radius_m):
        used_prior = False
        if fresh:
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
        elif prior_world is not None:
            result = self.locator.locate_near(
                frame,
                prior_world,
                geometry,
                search_radius_m=max(1.0, float(prior_radius_m)),
            )
            used_prior = True
        else:
            result = self.locator.locate_global(frame, geometry)

        return result, used_prior

    def _confirm_acquisition(self, result, current, previous_update, used_prior):
        best = result.best
        # Time alone cannot turn a repeated weak or ambiguous distant match into a fix.
        if (
            best.ncc < self.initial_min_ncc
            or best.full_ncc < self.initial_min_full_ncc
            or result.margin < self.initial_min_margin
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
