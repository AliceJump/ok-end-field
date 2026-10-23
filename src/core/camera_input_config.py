"""Read saved camera settings and calculate default input gains.

Counts refer to relative input RECEIVED by the game, not mouse_event dx sent
by automation. Runtime overrides, unsaved settings, IFix and camera collision
remain outside this static default profile. No process memory is accessed.
"""

from __future__ import annotations

import json
import math
import struct
from itertools import pairwise
from pathlib import Path

_PROFILE_PATH = Path(__file__).resolve().parents[2] / "assets/data/camera_input_profile.json"


def _load_profile() -> dict:
    """Read bundled defaults without making game updates a runtime prerequisite."""
    profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    if profile["schema"] != 1:
        raise ValueError("Unsupported camera input profile schema")
    return profile


def read_saved_camera_settings():
    """Read each saved field independently; use the verified local baseline when unavailable.

    This snapshot reader does not claim an installation audit or live-state read.
    The defaults were read on the user's environment: X/Y speed 0, reverse off.
    """
    values = {"speed_x": 0.0, "speed_y": 0.0, "reverse_x": False, "reverse_y": False}
    fallback = []
    try:
        import winreg

        profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, profile["registry_path"], 0, winreg.KEY_READ) as key:
            for axis in ("x", "y"):
                for kind in ("speed", "reverse"):
                    name = f"{kind}_{axis}"
                    try:
                        values[name] = _read_saved_camera_field(key, profile["axes"][axis], kind)
                    except (OSError, ValueError, TypeError, OverflowError, struct.error):
                        fallback.append(name)
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        fallback = list(values)
    return values, tuple(fallback)


def _read_saved_camera_field(key, config, kind):
    import winreg

    raw, value_type = winreg.QueryValueEx(key, config[f"{kind}_key"])
    if value_type != winreg.REG_DWORD:
        raise ValueError("Expected a DWORD camera setting")
    if kind == "speed":
        value = struct.unpack("<f", struct.pack("<I", raw))[0]
        _evaluate_curve(config["speed_curve"], value)
        return value
    if raw not in (0, 1):
        raise ValueError("Invalid reverse setting")
    return bool(raw)


def _evaluate_curve(keys: list[dict], value: float) -> float:
    """Evaluate the exported nonweighted Unity Hermite curve inside its range."""
    if len(keys) < 2 or any(key["weightedMode"] != 0 for key in keys):
        raise ValueError("Expected at least two nonweighted camera curve keys")
    if any(a["time"] >= b["time"] for a, b in pairwise(keys)):
        raise ValueError("Camera curve times must increase")
    if not math.isfinite(value) or not keys[0]["time"] <= value <= keys[-1]["time"]:
        raise ValueError("Value is outside the verified camera curve range")
    for a, b in pairwise(keys):
        if a["time"] <= value <= b["time"]:
            dt = b["time"] - a["time"]
            t = (value - a["time"]) / dt
            return (
                (2 * t**3 - 3 * t**2 + 1) * a["value"]
                + (t**3 - 2 * t**2 + t) * dt * a["outSlope"]
                + (-2 * t**3 + 3 * t**2) * b["value"]
                + (t**3 - t**2) * dt * b["inSlope"]
            )
    raise ValueError("Cannot evaluate camera curve")


def _read_axis(screen_size: int, axis: str, settings: dict | None) -> tuple[dict, float, float, float, bool]:
    """Return profile, signed linear gain, input gain, saved speed and reverse."""
    if isinstance(screen_size, bool) or not isinstance(screen_size, int) or screen_size <= 0:
        raise ValueError("Actual Unity screen size must be a positive integer")
    profile = _load_profile()
    config = profile["axes"][axis]
    if settings is None:
        settings, _ = read_saved_camera_settings()
    speed, reverse = settings[f"speed_{axis}"], settings[f"reverse_{axis}"]
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or type(reverse) is not bool:
        raise ValueError("Invalid camera speed or reverse setting")
    try:
        curve = _evaluate_curve(config["speed_curve"], speed)
    except ValueError:
        speed = 0.0
        curve = _evaluate_curve(config["speed_curve"], speed)
    input_gain = profile["native_mouse_factor"] * config["lua_speed"] * config["mouse_sensitivity"] / screen_size
    factors = (input_gain, config["drag_speed"], config["speed_factor"], curve)
    if any(not math.isfinite(value) or value <= 0 for value in factors):
        raise ValueError("Camera multipliers must be finite and positive")
    sign = config["input_sign"] * (-1 if reverse else 1) * (-1 if config["binding_inverted"] else 1)
    return profile, sign * math.prod(factors), input_gain, speed, reverse


def get_default_camera_yaw_ratio(screen_width: int, *, minimap: bool = False, settings: dict | None = None) -> float:
    """Return signed default yaw degrees per relative X count received by game.

    Pass actual Unity Screen.width, not desktop/saved/resized capture width.
    minimap negates yaw for counterclockwise arrow coordinates, assuming the
    character has settled facing the camera, as in zipline idle. The ordinary
    default camera has no horizontal angle limit. Other camera states may vary.

    Read current CN saved settings unless a settings snapshot is supplied.
    Missing saved fields use the baseline from read_saved_camera_settings.
    Bundled constants supply an initial estimate; actual control needs feedback.
    No game installation files are read or compared with a pinned build.
    """
    profile, gain, _, _, _ = _read_axis(screen_width, "x", settings)
    return gain * profile["degrees_per_turn"] * (-1 if minimap else 1)


def get_default_camera_vertical_track(screen_height: int, *, settings: dict | None = None) -> dict:
    """Read default vertical orbit gain, limits, acceleration and geometry.

    screen_height must be actual Unity Screen.height. The linear gain is TRACK
    UNITS per received relative Y count, not degrees/count. It applies below
    the acceleration threshold, before visible limits and interpolation. The
    internal target verticalValue itself can exceed the configured track range.

    Geometry samples use atan2(arm_height, distance), as in the resource's pitch
    lookup generation. They do not predict world pitch after offsets, zoom,
    aiming, collision, live configuration overrides or IFix. The baked inverse
    pitch lookup ends at track 0.99; do not extrapolate it to track 1.

    Uses the same saved-setting fallback and bundled-default policy as
    get_default_camera_yaw_ratio; no game installation audit is required.
    """
    profile, gain, input_gain, speed, reverse = _read_axis(screen_height, "y", settings)
    track = profile["vertical_track"]
    samples = []
    for value in (0.0, 0.2, 0.4, 0.6, 0.84, 1.0):
        arm = _evaluate_curve(track["height_curve"], value)
        distance = _evaluate_curve(track["distance_curve"], value)
        samples.append(
            {
                "track_value": value,
                "arm_height": arm,
                "distance": distance,
                "nominal_pitch_degrees": math.degrees(math.atan2(arm, distance)),
            }
        )
    return {
        "screen_height": screen_height,
        "saved_speed_y": speed,
        "saved_reverse_y": reverse,
        "linear_track_units_per_received_y": gain,
        "min_vertical_value": track["min_value"],
        "max_vertical_value": track["max_value"],
        "vertical_tween_speed": track["tween_speed"],
        "acceleration_config": track["acceleration"],
        "acceleration_threshold_received_counts_per_second": track["acceleration"]["_speedMinThreshold"] / input_gain,
        "height_curve": track["height_curve"],
        "distance_curve": track["distance_curve"],
        "pitch_to_track_range": track["pitch_lookup_range"],
        "geometry_samples": samples,
    }
