"""Keep acquisition records intact; classify outliers in a common input scale."""

import math
from dataclasses import dataclass, replace

from src.core.learning.environment import LearningEnvironment

DECAY_HALF_LIFE_DAYS = 7.0
SECONDS_PER_DAY = 86400


def acquisition_day(timestamp):
    """Daily UTC age avoids wall-clock jitter changing classifications within a run."""
    return int(timestamp // SECONDS_PER_DAY)


def history_weights(samples):
    if not samples:
        return ()
    latest = max(acquisition_day(sample.acquired_at) for sample in samples)
    return tuple(
        max(1e-150, 2.0 ** (-(latest - acquisition_day(sample.acquired_at)) / DECAY_HALF_LIFE_DAYS))
        for sample in samples
    )


def weighted_median(values, weights):
    pairs = sorted((value, weight) for value, weight in zip(values, weights, strict=True) if weight > 0)
    half = math.fsum(weight for _, weight in pairs) / 2
    cumulative = 0.0
    for index, (value, weight) in enumerate(pairs):
        cumulative += weight
        if math.isclose(cumulative, half, rel_tol=1e-12, abs_tol=0.0) and index + 1 < len(pairs):
            return (value + pairs[index + 1][0]) / 2
        if cumulative >= half:
            return value
    return pairs[-1][0]


@dataclass(frozen=True)
class MouseInput:
    dx: float
    dy: float
    elapsed: float
    frame_id: int

    def __post_init__(self):
        if (
            any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in (self.dx, self.dy, self.elapsed)
            )
            or self.elapsed < 0
        ):
            raise ValueError("Invalid input observation")
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise ValueError("Invalid preceding frame")


@dataclass(frozen=True)
class MotionSample:
    dx: float
    dy: float
    acquired_at: float
    environment: LearningEnvironment
    inputs: tuple[MouseInput, ...] = ()
    verification: str = "aligned"
    outlier: bool = False
    conversion_version: int = 1

    def __post_init__(self):
        if (
            any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in (self.dx, self.dy, self.acquired_at)
            )
            or self.acquired_at < 0
        ):
            raise ValueError("Invalid raw motion sample")
        if not isinstance(self.environment, LearningEnvironment) or type(self.outlier) is not bool:
            raise ValueError("Invalid sample metadata")
        if self.verification not in ("aligned", "gold_gate", "yaw"):
            raise ValueError("Invalid verification rule")
        if type(self.conversion_version) is not int or self.conversion_version != 1:
            raise ValueError("Unsupported input conversion model")
        if not isinstance(self.inputs, tuple) or any(not isinstance(item, MouseInput) for item in self.inputs):
            raise ValueError("Invalid input trace")
        if self.inputs and (
            not math.isclose(sum(item.dx for item in self.inputs), self.dx, abs_tol=1e-8)
            or not math.isclose(sum(item.dy for item in self.inputs), self.dy, abs_tol=1e-8)
            or any(
                a.elapsed > b.elapsed or a.frame_id > b.frame_id
                for a, b in zip(self.inputs, self.inputs[1:], strict=False)
            )
        ):
            raise ValueError("Input trace does not describe complete raw movement")

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        values["environment"] = LearningEnvironment.from_dict(values["environment"])
        values["inputs"] = tuple(MouseInput(**item) for item in values.get("inputs", ()))
        return cls(**values)


def classify_samples(samples):
    """Time-weighted median/MAD, with raw-count floors in a fixed 1080p reference."""
    reference = LearningEnvironment()
    points = [sample.environment.convert(sample.dx, sample.dy, reference) for sample in samples]
    if len(points) < 3:
        return tuple(replace(sample, outlier=False) for sample in samples)
    weights = history_weights(samples)
    centers = [weighted_median([point[axis] for point in points], weights) for axis in (0, 1)]
    limits = [
        max(
            12.0,
            abs(centers[axis]) * 0.25,
            3.5 * 1.4826 * weighted_median([abs(point[axis] - centers[axis]) for point in points], weights),
        )
        for axis in (0, 1)
    ]
    # A recent stable triple can establish a changed regime without waiting for
    # thousands of old observations to lose their majority. Conversely, one or
    # two recent spikes cannot replace the latest verified stable cluster.
    ordered = sorted(range(len(samples)), key=lambda index: (samples[index].acquired_at, index))
    for end in range(len(ordered), 2, -1):
        cluster = [points[index] for index in ordered[end - 3 : end]]
        candidate = [weighted_median([point[axis] for point in cluster], (1.0, 1.0, 1.0)) for axis in (0, 1)]
        bounds = [max(12.0, abs(value) * 0.25) for value in candidate]
        if any(abs(point[axis] - candidate[axis]) > bounds[axis] for point in cluster for axis in (0, 1)):
            continue
        if any(abs(candidate[axis] - centers[axis]) > max(bounds[axis], abs(centers[axis]) * 0.25) for axis in (0, 1)):
            centers, limits = candidate, bounds
        break
    return tuple(
        replace(sample, outlier=any(abs(point[axis] - centers[axis]) > limits[axis] for axis in (0, 1)))
        for sample, point in zip(samples, points, strict=True)
    )
