"""Keep acquisition records intact; classify outliers in a common input scale."""

import math
from dataclasses import dataclass, replace
from statistics import median

from src.core.learning.environment import LearningEnvironment


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
    """MAD around the median, with raw-count floors in a fixed 1080p reference."""
    reference = LearningEnvironment()
    points = [sample.environment.convert(sample.dx, sample.dy, reference) for sample in samples]
    if len(points) < 3:
        return tuple(replace(sample, outlier=False) for sample in samples)
    centers = [median(point[axis] for point in points) for axis in (0, 1)]
    limits = [
        max(
            12.0, abs(centers[axis]) * 0.25, 3.5 * 1.4826 * median(abs(point[axis] - centers[axis]) for point in points)
        )
        for axis in (0, 1)
    ]
    return tuple(
        replace(sample, outlier=any(abs(point[axis] - centers[axis]) > limits[axis] for axis in (0, 1)))
        for sample, point in zip(samples, points, strict=True)
    )
