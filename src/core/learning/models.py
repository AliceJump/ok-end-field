"""Validated semantic identities and independently usable action/target priors."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from enum import StrEnum

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

    def __post_init__(self):
        _count(self.samples)
        _count(self.legacy_samples)
        _finite(self.mean_dx, self.mean_dy, self.m2_dx, self.m2_dy)
        if type(self.variance_known) is not bool or self.m2_dx < 0 or self.m2_dy < 0:
            raise ValueError("Invalid variance")

    def add(self, dx, dy):
        _finite(dx, dy)
        # Legacy means are retained as priors, but cannot invent within-source variance.
        if not self.variance_known:
            return MotionStatistics(1, dx, dy, legacy_samples=self.samples + self.legacy_samples)
        n = self.samples + 1
        mx = self.mean_dx + (dx - self.mean_dx) / n
        my = self.mean_dy + (dy - self.mean_dy) / n
        return MotionStatistics(
            n,
            mx,
            my,
            self.m2_dx + (dx - self.mean_dx) * (dx - mx),
            self.m2_dy + (dy - self.mean_dy) * (dy - my),
            legacy_samples=self.legacy_samples,
        )

    def merge(self, other):
        if not self.samples:
            return other
        if not other.samples:
            return self
        n = self.samples + other.samples
        delta_x, delta_y = other.mean_dx - self.mean_dx, other.mean_dy - self.mean_dy
        weight = self.samples * other.samples / n
        known = self.variance_known and other.variance_known
        return MotionStatistics(
            n,
            self.mean_dx + delta_x * other.samples / n,
            self.mean_dy + delta_y * other.samples / n,
            self.m2_dx + other.m2_dx + delta_x**2 * weight if known else 0.0,
            self.m2_dy + other.m2_dy + delta_y**2 * weight if known else 0.0,
            known,
            self.legacy_samples + other.legacy_samples,
        )

    @property
    def variance(self):
        if not self.variance_known or self.samples < 2:
            return None
        return self.m2_dx / (self.samples - 1), self.m2_dy / (self.samples - 1)


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

    def successful(self, dx, dy, *, target=None, now, provenance=()):
        return replace(
            self,
            motion=self.motion.add(dx, dy),
            target=target if target is not None else self.target,
            success_count=self.success_count + 1,
            consecutive_failures=0,
            updated_at=now,
            provenance=tuple(dict.fromkeys((*self.provenance, *provenance)))[-64:],
        )

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
        target = values.get("target")
        if target is not None:
            target = dict(target)
            for field in ("initial_position", "aligned_position", "roi"):
                if target.get(field) is not None:
                    target[field] = tuple(target[field])
            values["target"] = TargetPrior(**target)
        values["provenance"] = tuple(values.get("provenance", ()))
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
