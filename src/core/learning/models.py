"""Validated semantic identities and independently usable action/target priors."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from functools import cached_property

from src.core.learning.environment import LearningEnvironment
from src.core.learning.samples import (
    DECAY_HALF_LIFE_DAYS,
    MotionSample,
    acquisition_day,
    classify_samples,
    history_weights,
)

CONTEXT_VERSION = 1


def canonical_fingerprint(value) -> str:
    """Preserve sequence order while canonicalizing object keys; reject NaN/Infinity."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(*values):
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values
    ):
        raise ValueError("Expected finite numbers")


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer count")


def _identity(*values):
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in values):
        raise ValueError("Context identities must be nonempty canonical strings")


def yaw_bucket(yaw):
    _finite(yaw)
    return round((yaw % 360) / 5.0) % 72


class ContextConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INVALID = "invalid"


@dataclass(frozen=True)
class CaptureProfile:
    width: int
    height: int
    camera: str
    movement_version: str = "v1"
    axes: str = "xy"

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.width, self.height)):
            raise ValueError("Capture dimensions must be positive integers")
        _identity(self.camera, self.movement_version)
        if self.axes not in ("x", "y", "xy"):
            raise ValueError("Unknown movement axes")


@dataclass(frozen=True)
class WorkflowContext:
    task: str
    phase: str
    area: str
    location: str
    entry: str
    origin_anchor: str
    target_id: str
    capture_profile: CaptureProfile
    entry_route_signature: tuple[int, ...] = ()
    start_yaw_bucket: int | None = None

    def __post_init__(self):
        _identity(self.task, self.phase, self.area, self.location, self.entry, self.origin_anchor, self.target_id)
        _validate_profile_and_yaw(self.capture_profile, self.start_yaw_bucket)
        _validate_distances(self.entry_route_signature, allow_empty=True)

    @property
    def fingerprint(self):
        return canonical_fingerprint({"context_version": CONTEXT_VERSION, "kind": "workflow", **asdict(self)})

    @property
    def allows_learning(self):
        phase = self.phase.lower().replace("-", "_")
        return not (phase == "battle" or phase.startswith(("battle/", "post_battle")))


def _validate_profile_and_yaw(profile, bucket):
    if not isinstance(profile, CaptureProfile):
        raise ValueError("A verified capture profile is required")
    if bucket is not None and (type(bucket) is not int or not 0 <= bucket < 72):
        raise ValueError("Invalid starting yaw bucket")


def _validate_distances(values, *, allow_empty=False):
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError("Route distances must be an immutable tuple")
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Route distances must be positive integers")


@dataclass(frozen=True)
class RouteContext:
    config_key: str
    distances: tuple[int, ...]
    segment_index: int
    capture_profile: CaptureProfile
    start_yaw_bucket: int | None
    # area/location/origin/direction/kind; absent family permits private use only.
    family: tuple[str, ...] = ()

    def __post_init__(self):
        _identity(self.config_key)
        _validate_distances(self.distances)
        if type(self.segment_index) is not int or not 0 <= self.segment_index < len(self.distances):
            raise ValueError("Segment index is outside the route")
        _validate_profile_and_yaw(self.capture_profile, self.start_yaw_bucket)
        if not isinstance(self.family, tuple) or (self.family and len(self.family) != 5):
            raise ValueError("Family must identify area/location/origin/direction/kind")
        _identity(*self.family)

    @property
    def target_id(self):
        return f"zipline-distance:{self.distances[self.segment_index]}"

    @property
    def allows_learning(self):
        # Combat restrictions come from the enclosing deny scope, including retries.
        return True

    @property
    def route_prefix(self):
        return self.distances[: self.segment_index + 1]

    @property
    def fingerprint(self):
        return canonical_fingerprint({"context_version": CONTEXT_VERSION, "kind": "route_private", **asdict(self)})

    @property
    def shared_fingerprint(self):
        if not self.family:
            return None
        return canonical_fingerprint(
            {
                "context_version": CONTEXT_VERSION,
                "kind": "route_prefix",
                "family": self.family,
                "route_prefix": self.route_prefix,
                "segment_index": self.segment_index,
                "capture_profile": asdict(self.capture_profile),
                "start_yaw_bucket": self.start_yaw_bucket,
            }
        )


@dataclass(frozen=True)
class MotionStatistics:
    samples: int = 0
    mean_dx: float = 0.0
    mean_dy: float = 0.0
    m2_dx: float = 0.0
    m2_dy: float = 0.0
    variance_known: bool = True
    legacy_samples: int = 0
    weight_sum: float = 0.0
    weight_square_sum: float = 0.0
    reference_day: int = 0

    def __post_init__(self):
        _count(self.samples)
        _count(self.legacy_samples)
        _count(self.reference_day)
        _finite(self.weight_sum, self.weight_square_sum)
        if self.weight_sum < 0 or self.weight_square_sum < 0 or bool(self.weight_sum) != bool(self.weight_square_sum):
            raise ValueError("Invalid history weights")
        _finite(self.mean_dx, self.mean_dy, self.m2_dx, self.m2_dy)
        if type(self.variance_known) is not bool or self.m2_dx < 0 or self.m2_dy < 0:
            raise ValueError("Invalid variance")

    @property
    def total_weight(self):
        return self.weight_sum or float(self.samples)

    @property
    def squared_weights(self):
        return self.weight_square_sum or float(self.samples)

    @property
    def effective_samples(self):
        return self.total_weight**2 / self.squared_weights if self.squared_weights else 0.0

    def add(self, dx, dy, *, weight=1.0):
        _finite(dx, dy)
        _finite(weight)
        if weight < 0:
            raise ValueError("Negative history weight")
        if weight == 0:
            return self
        # Legacy means are retained as priors, but cannot invent within-source variance.
        if not self.variance_known:
            return MotionStatistics(
                1,
                dx,
                dy,
                legacy_samples=self.samples + self.legacy_samples,
                weight_sum=weight,
                weight_square_sum=weight * weight,
                reference_day=self.reference_day,
            )
        n = self.samples + 1
        total = self.total_weight + weight
        mx = self.mean_dx + (dx - self.mean_dx) * weight / total
        my = self.mean_dy + (dy - self.mean_dy) * weight / total
        return MotionStatistics(
            n,
            mx,
            my,
            self.m2_dx + weight * (dx - self.mean_dx) * (dx - mx),
            self.m2_dy + weight * (dy - self.mean_dy) * (dy - my),
            legacy_samples=self.legacy_samples,
            weight_sum=total,
            weight_square_sum=self.squared_weights + weight * weight,
            reference_day=self.reference_day,
        )

    def merge(self, other):
        if not self.samples:
            return other
        if not other.samples:
            return self
        n = self.samples + other.samples
        day = max(self.reference_day, other.reference_day)
        a = max(1e-150, 2.0 ** (-(day - self.reference_day) / DECAY_HALF_LIFE_DAYS))
        b = max(1e-150, 2.0 ** (-(day - other.reference_day) / DECAY_HALF_LIFE_DAYS))
        left, right = self.total_weight * a, other.total_weight * b
        total = left + right
        delta_x, delta_y = other.mean_dx - self.mean_dx, other.mean_dy - self.mean_dy
        weight = left * right / total
        known = self.variance_known and other.variance_known
        return MotionStatistics(
            n,
            self.mean_dx + delta_x * right / total,
            self.mean_dy + delta_y * right / total,
            self.m2_dx * a + other.m2_dx * b + delta_x**2 * weight if known else 0.0,
            self.m2_dy * a + other.m2_dy * b + delta_y**2 * weight if known else 0.0,
            known,
            self.legacy_samples + other.legacy_samples,
            total,
            self.squared_weights * a * a + other.squared_weights * b * b,
            day,
        )

    @property
    def variance(self):
        if not self.variance_known or self.samples < 2:
            return None
        denominator = self.total_weight - self.squared_weights / self.total_weight
        if denominator <= 0:
            return None
        return self.m2_dx / denominator, self.m2_dy / denominator


@dataclass(frozen=True)
class TargetPrior:
    initial_position: tuple[float, float] | None = None
    aligned_position: tuple[float, float] | None = None
    roi: tuple[float, float, float, float] | None = None
    search_direction: str | None = None

    def __post_init__(self):
        for value, length in ((self.initial_position, 2), (self.aligned_position, 2), (self.roi, 4)):
            if value is not None:
                if not isinstance(value, tuple) or len(value) != length:
                    raise ValueError("Invalid normalized target geometry")
                _finite(*value)
                if any(not 0 <= number <= 1 for number in value):
                    raise ValueError("Target geometry must be normalized")
        if self.roi is not None and (self.roi[0] >= self.roi[2] or self.roi[1] >= self.roi[3]):
            raise ValueError("Empty target ROI")
        if self.search_direction not in (None, "left", "right", "up", "down"):
            raise ValueError("Invalid target search direction")


@dataclass(frozen=True)
class LearningRecord:
    motion: MotionStatistics = MotionStatistics()
    target: TargetPrior | None = None
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    updated_at: float = 0.0
    provenance: tuple[str, ...] = ()
    observations: tuple[MotionSample, ...] = ()
    legacy_motion: MotionStatistics | None = None
    legacy_retired: bool = False

    def __post_init__(self):
        if not isinstance(self.motion, MotionStatistics) or (
            self.target is not None and not isinstance(self.target, TargetPrior)
        ):
            raise ValueError("Invalid learning priors")
        for value in (self.success_count, self.failure_count, self.consecutive_failures):
            _count(value)
        _finite(self.updated_at)
        if self.updated_at < 0 or self.consecutive_failures > self.failure_count:
            raise ValueError("Invalid learning history")
        if not isinstance(self.provenance, tuple) or len(self.provenance) > 64:
            raise ValueError("Invalid provenance")
        _identity(*self.provenance)
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, MotionSample) for item in self.observations
        ):
            raise ValueError("Invalid acquisition records")
        if self.legacy_motion is not None and not isinstance(self.legacy_motion, MotionStatistics):
            raise ValueError("Invalid legacy motion")
        if type(self.legacy_retired) is not bool:
            raise ValueError("Invalid legacy retirement")

    @cached_property
    def _reference_motion(self):
        """One scan per immutable record; cache is runtime-only and never serialized."""
        reference = LearningEnvironment()
        day = max((acquisition_day(sample.acquired_at) for sample in self.observations), default=0)
        motion = MotionStatistics(legacy_samples=self.motion.legacy_samples, reference_day=day)
        for sample, weight in zip(self.observations, history_weights(self.observations), strict=True):
            if not sample.outlier:
                motion = motion.add(*sample.environment.convert(sample.dx, sample.dy, reference), weight=weight)
        return motion

    def motion_for(self, environment):
        """Scale full-history sufficient statistics instead of rescanning for each yaw lookup."""
        motion = self._reference_motion
        sx, sy = LearningEnvironment().convert(1, 1, environment)
        return replace(
            motion,
            mean_dx=motion.mean_dx * sx,
            mean_dy=motion.mean_dy * sy,
            m2_dx=motion.m2_dx * sx * sx,
            m2_dy=motion.m2_dy * sy * sy,
        )

    def successful(
        self, dx, dy, *, target=None, now, provenance=(), environment=None, inputs=(), verification="aligned"
    ):
        environment = environment or LearningEnvironment()
        observations = classify_samples(
            (*self.observations, MotionSample(dx, dy, now, environment, inputs, verification))
        )
        legacy = self.motion.legacy_samples + (self.motion.samples if not self.observations else 0)
        legacy_motion = self.legacy_motion
        if not self.observations and self.motion.samples:
            legacy_motion = self.motion
        updated = replace(
            self, observations=observations, motion=MotionStatistics(legacy_samples=legacy), legacy_motion=legacy_motion
        )
        result = replace(
            updated,
            motion=updated.motion_for(environment),
            target=target if target is not None else self.target,
            success_count=self.success_count + 1,
            consecutive_failures=0,
            updated_at=now,
            provenance=tuple(dict.fromkeys((*self.provenance, *provenance)))[-64:],
        )
        variance = result.motion.variance
        ready = (
            result.motion.samples >= 3
            and result.motion.effective_samples >= 3 - 1e-9
            and variance is not None
            and math.sqrt(sum(variance)) <= max(12.0, math.hypot(result.motion.mean_dx, result.motion.mean_dy) * 0.25)
        )
        return replace(result, legacy_retired=self.legacy_retired or ready)

    def failed(self, *, now):
        return replace(
            self,
            failure_count=self.failure_count + 1,
            consecutive_failures=self.consecutive_failures + 1,
            updated_at=now,
        )

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        values["motion"] = MotionStatistics(**values["motion"])
        if values.get("legacy_motion") is not None:
            values["legacy_motion"] = MotionStatistics(**values["legacy_motion"])
        target = values.get("target")
        if target is not None:
            target = dict(target)
            for field in ("initial_position", "aligned_position", "roi"):
                if target.get(field) is not None:
                    target[field] = tuple(target[field])
            values["target"] = TargetPrior(**target)
        values["provenance"] = tuple(values.get("provenance", ()))
        values["observations"] = tuple(MotionSample.from_dict(item) for item in values.get("observations", ()))
        return cls(**values)


@dataclass(frozen=True)
class LearningHint:
    action: tuple[int, int] | None
    target: TargetPrior | None
    confidence: ContextConfidence
    source: str
    samples: int


@dataclass(frozen=True)
class VerificationEvidence:
    target_id: str
    kind: str
    observed_at: float
    frame_id: int

    def __post_init__(self):
        _identity(self.target_id)
        _finite(self.observed_at)
        _count(self.frame_id)
        if self.kind not in ("aligned", "arrived", "interacted", "zipline_arrived"):
            raise ValueError("Unknown verification evidence")
