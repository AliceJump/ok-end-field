"""Seven acquisition settings and signed input conversion, independent of tasks."""

import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache

from src.core.camera_input_config import _PROFILE_PATH, _evaluate_curve


@lru_cache(maxsize=1)
def _speed_curves():
    profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    return {axis: profile["axes"][axis]["speed_curve"] for axis in ("x", "y")}


@lru_cache(maxsize=128)
def sensitivity_gain(speed, reverse, axis):
    return _evaluate_curve(_speed_curves()[axis], speed) * (-1 if reverse else 1)


@dataclass(frozen=True)
class LearningEnvironment:
    """Defaults are the user's verified baseline: speed 0, neither axis reversed."""

    speed_x: float = 0.0
    speed_y: float = 0.0
    reverse_x: bool = False
    reverse_y: bool = False
    capture_size: tuple[int, int] = (1920, 1080)
    scroll: bool = False
    start_yaw: float | None = None
    # Read provenance, not an eighth setting. Unknown yaw stays unknown.
    fallback_fields: tuple[str, ...] = ()

    def __post_init__(self):
        for speed in (self.speed_x, self.speed_y):
            if (
                isinstance(speed, bool)
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or not -5 <= speed <= 5
            ):
                raise ValueError("Invalid camera sensitivity")
        if any(type(value) is not bool for value in (self.reverse_x, self.reverse_y, self.scroll)):
            raise ValueError("Invalid direction or scroll setting")
        if (
            not isinstance(self.capture_size, tuple)
            or len(self.capture_size) != 2
            or any(type(value) is not int or value <= 0 for value in self.capture_size)
        ):
            raise ValueError("Invalid capture size")
        if self.start_yaw is not None and (
            isinstance(self.start_yaw, bool)
            or not isinstance(self.start_yaw, (int, float))
            or not math.isfinite(self.start_yaw)
            or not 0 <= self.start_yaw < 360
        ):
            raise ValueError("Invalid starting yaw")
        names = set(asdict(self)) - {"fallback_fields"}
        if not isinstance(self.fallback_fields, tuple) or any(name not in names for name in self.fallback_fields):
            raise ValueError("Invalid fallback provenance")

    def convert(self, dx, dy, target):
        """Map raw sent counts to target settings; callers enforce entry/pose compatibility."""
        return tuple(
            delta
            * target.capture_size[index]
            / self.capture_size[index]
            * sensitivity_gain(getattr(self, f"speed_{axis}"), getattr(self, f"reverse_{axis}"), axis)
            / sensitivity_gain(getattr(target, f"speed_{axis}"), getattr(target, f"reverse_{axis}"), axis)
            for index, (axis, delta) in enumerate(zip(("x", "y"), (dx, dy), strict=True))
        )

    def horizontal_yaw_gain(self, reference_gain):
        """Scale the empirical 1920-wide, speed-zero yaw seed to these settings."""
        return (
            reference_gain
            * 1920
            / self.capture_size[0]
            * sensitivity_gain(self.speed_x, self.reverse_x, "x")
            / sensitivity_gain(0.0, False, "x")
        )

    @classmethod
    def from_dict(cls, data):
        values = dict(data)
        values["capture_size"] = tuple(values["capture_size"])
        values["fallback_fields"] = tuple(values.get("fallback_fields", ()))
        return cls(**values)


def environment_for_context(context):
    profile = context.capture_profile
    camera = profile.camera
    return LearningEnvironment(
        capture_size=(profile.width, profile.height),
        scroll="scroll-max" in camera or "scroll-True" in camera,
        start_yaw=context.start_yaw_bucket * 5.0 if context.start_yaw_bucket is not None else None,
        fallback_fields=("speed_x", "speed_y", "reverse_x", "reverse_y"),
    )
