"""Read CN camera settings and calculate build-verified default input gains.

Counts refer to relative input RECEIVED by the game, not mouse_event dx sent
by automation. Runtime overrides, unsaved settings, IFix and camera collision
remain outside this static default profile. No process memory is accessed.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from itertools import pairwise
from pathlib import Path

_PROFILE_PATH = Path(__file__).resolve().parents[2] / "assets/data/camera_input_profile.json"


def _load_profile(game_dir: str | Path) -> dict:
    """Reject a changed client, VFS index or camera bundle before using defaults."""
    profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    if profile["schema"] != 1:
        raise ValueError("Unsupported camera input profile schema")
    root = Path(game_dir)
    for name, expected in profile["installed_files"].items():
        with (root / name).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"Camera input profile requires a new client audit: {name}")
    for item in profile["bundle_slices"]:
        with (root / item["path"]).open("rb") as stream:
            stream.seek(item["offset"])
            raw = stream.read(item["length"])
        if len(raw) != item["length"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError(f"Camera input profile requires a new resource audit: {item['path']}")
    return profile


def _read_settings(profile: dict, axis: str) -> tuple[float, bool]:
    """Read fresh PlayerPrefs values, preserving their DWORD float bit pattern."""
    import winreg

    config = profile["axes"][axis]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, profile["registry_path"], 0, winreg.KEY_READ) as key:
        speed_bits, speed_kind = winreg.QueryValueEx(key, config["speed_key"])
        reverse, reverse_kind = winreg.QueryValueEx(key, config["reverse_key"])
    if speed_kind != winreg.REG_DWORD or reverse_kind != winreg.REG_DWORD:
        raise ValueError("Camera speed and reverse settings must be REG_DWORD")
    speed = struct.unpack("<f", struct.pack("<I", speed_bits))[0]
    if not math.isfinite(speed) or reverse not in (0, 1):
        raise ValueError("Invalid persisted camera speed or reverse setting")
    return speed, bool(reverse)


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


def _read_axis(screen_size: int, game_dir: str | Path, axis: str) -> tuple[dict, float, float, float, bool]:
    """Return profile, signed linear gain, input gain, saved speed and reverse."""
    if isinstance(screen_size, bool) or not isinstance(screen_size, int) or screen_size <= 0:
        raise ValueError("Actual Unity screen size must be a positive integer")
    profile = _load_profile(game_dir)
    config = profile["axes"][axis]
    speed, reverse = _read_settings(profile, axis)
    curve = _evaluate_curve(config["speed_curve"], speed)
    input_gain = profile["native_mouse_factor"] * config["lua_speed"] * config["mouse_sensitivity"] / screen_size
    factors = (input_gain, config["drag_speed"], config["speed_factor"], curve)
    if any(not math.isfinite(value) or value <= 0 for value in factors):
        raise ValueError("Camera multipliers must be finite and positive")
    sign = config["input_sign"] * (-1 if reverse else 1) * (-1 if config["binding_inverted"] else 1)
    return profile, sign * math.prod(factors), input_gain, speed, reverse


def get_default_camera_yaw_ratio(screen_width: int, *, game_dir: str | Path, minimap: bool = False) -> float:
    """Return signed default yaw degrees per relative X count received by game.

    Pass actual Unity Screen.width, not desktop/saved/resized capture width.
    minimap negates yaw for counterclockwise arrow coordinates, assuming the
    character has settled facing the camera, as in zipline idle. The ordinary
    default camera has no horizontal angle limit. Other camera states may vary.

    The CN saved sensitivity/reverse settings are read on each call. Build and
    resource hashes are verified, so use this at initialization, not per frame.
    Missing settings/files raise OSError; changed builds and unsupported values
    raise ValueError. No silent sensitivity, resolution or version fallback.
    """
    profile, gain, _, _, _ = _read_axis(screen_width, game_dir, "x")
    return gain * profile["degrees_per_turn"] * (-1 if minimap else 1)


def get_default_camera_vertical_track(screen_height: int, *, game_dir: str | Path) -> dict:
    """Read default vertical orbit gain, limits, acceleration and geometry.

    screen_height must be actual Unity Screen.height. The linear gain is TRACK
    UNITS per received relative Y count, not degrees/count. It applies below
    the acceleration threshold, before visible limits and interpolation. The
    internal target verticalValue itself can exceed the configured track range.

    Geometry samples use atan2(arm_height, distance), as in the resource's pitch
    lookup generation. They do not predict world pitch after offsets, zoom,
    aiming, collision, live configuration overrides or IFix. The baked inverse
    pitch lookup ends at track 0.99; do not extrapolate it to track 1.

    Reads current CN saved Y settings and verifies the same installation inputs
    as get_default_camera_yaw_ratio. Use at initialization, not per frame.
    """
    profile, gain, input_gain, speed, reverse = _read_axis(screen_height, game_dir, "y")
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
