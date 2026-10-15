from __future__ import annotations

import json
import math
import os
from contextlib import suppress
from pathlib import Path
from threading import RLock

from src.tasks.mixin.zip_line_yaw_cache import build_zip_line_motion_route_fingerprint

_CACHE_VERSION = 4
_DEFAULT_CACHE_PATH = Path("tmp") / "zipline_motion_cache.json"
_CACHE_LOCK = RLock()
_MAX_ROUTES = 128
_MAX_SEGMENTS_PER_ROUTE = 64


def build_zip_line_route_fingerprint(config_key, zip_line_list) -> str | None:
    """Build an exact route identity from the config key and full user-entered list.

    The segment index is deliberately not part of this value. One route fingerprint
    owns independent motion records for segment 0, segment 1, and so on. Therefore
    duplicate distances in one list remain distinguishable by their position.
    """
    key = str(config_key or "").strip()
    if not key:
        return None
    try:
        values = [int(value) for value in zip_line_list]
    except (TypeError, ValueError, OverflowError):
        return None
    if not values:
        return None
    return json.dumps([key, values], ensure_ascii=False, separators=(",", ":"))


def _empty_cache() -> dict:
    """Return an empty cache payload for the current schema version."""
    return {"version": _CACHE_VERSION, "routes": {}}


def _read_cache() -> dict:
    """Read current or calibrated-yaw history; fail soft on unsupported/corrupt data."""
    try:
        payload = json.loads(_DEFAULT_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_cache()
    if not isinstance(payload, dict) or payload.get("version") not in (3, _CACHE_VERSION):
        return _empty_cache()
    routes = payload.get("routes")
    if not isinstance(routes, dict):
        return _empty_cache()
    # v3 measured the suffix after yaw calibration, so each old anchor bucket is
    # already a valid starting-yaw history. New records also include any prefix.
    payload["version"] = _CACHE_VERSION
    return payload


def _write_cache(payload: dict) -> bool:
    """Atomically persist the cache to the fixed ignored tmp path."""
    path = _DEFAULT_CACHE_PATH
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)
        return True
    except (OSError, ValueError, TypeError):
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        return False


def _finite_number(value) -> float | None:
    """Coerce a cache field to a finite float, otherwise return None."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _segment_key(segment_index) -> str | None:
    """Validate and normalize a zero-based route segment index for JSON storage."""
    try:
        index = int(segment_index)
    except (TypeError, ValueError, OverflowError):
        return None
    if index < 0 or index >= _MAX_SEGMENTS_PER_ROUTE:
        return None
    return str(index)


def _segment_motion(route, key):
    """Validate one stored segment and return its raw displacement and sample count."""
    if not isinstance(route, dict) or not isinstance(route.get("segments"), dict):
        return None
    entry = route["segments"].get(key)
    if not isinstance(entry, dict):
        return None
    try:
        samples = int(entry.get("samples", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    mean_dx = _finite_number(entry.get("mean_dx"))
    mean_dy = _finite_number(entry.get("mean_dy"))
    if samples <= 0 or mean_dx is None or mean_dy is None:
        return None
    move_dx, move_dy = round(mean_dx), round(mean_dy)
    return (move_dx, move_dy, samples) if move_dx or move_dy else None


def load_zip_line_segment_motion(route_fingerprint: str | None, segment_index):
    """Return ``(raw_dx, raw_dy, samples)`` for one exact route segment.

    Raw relative mouse deltas are intentionally not scaled by render resolution.
    The cache is only eligible when both the config key and the complete configured
    distance list match, and the requested segment index has its own learned entry.
    """
    if not route_fingerprint:
        return None
    key = _segment_key(segment_index)
    if key is None:
        return None

    with _CACHE_LOCK:
        route = _read_cache()["routes"].get(route_fingerprint)
    return _segment_motion(route, key)


def load_nearest_zip_line_segment_motion(route_fingerprint, segment_index, width, height, start_yaw):
    """Find the nearest valid learned yaw for the same route, segment and capture size.

    Returns ``(yaw, raw_dx, raw_dy, samples)``. The caller must physically turn to
    this yaw and verify the actual bucket before replaying the displacement.
    """
    fingerprint = build_zip_line_motion_route_fingerprint(route_fingerprint, width, height, start_yaw)
    key = _segment_key(segment_index)
    if fingerprint is None or key is None:
        return None
    identity = json.loads(fingerprint)[:2]
    yaw = float(start_yaw) % 360.0
    with _CACHE_LOCK:
        routes = _read_cache()["routes"]
    candidates = []
    for stored_key, route in routes.items():
        try:
            fields = json.loads(stored_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(fields, list) or len(fields) != 3 or fields[:2] != identity:
            continue
        bucket = fields[2]
        if type(bucket) is not int or not 0 <= bucket < 72:
            continue
        motion = _segment_motion(route, key)
        if motion is None:
            continue
        target_yaw = bucket * 5.0
        distance = abs((target_yaw - yaw + 180.0) % 360.0 - 180.0)
        candidates.append((distance, -motion[2], target_yaw, motion))
    if not candidates:
        return None
    _, _, target_yaw, motion = min(candidates)
    return target_yaw, *motion


def record_zip_line_segment_motion(
    route_fingerprint: str | None,
    segment_index,
    total_dx: float,
    total_dy: float,
) -> bool:
    """Update the rolling raw mouse total for one successful route segment."""
    if not route_fingerprint:
        return False
    key = _segment_key(segment_index)
    if key is None:
        return False
    dx = _finite_number(total_dx)
    dy = _finite_number(total_dy)
    if dx is None or dy is None or (dx == 0 and dy == 0):
        return False

    with _CACHE_LOCK:
        payload = _read_cache()
        routes = payload["routes"]
        route = routes.get(route_fingerprint)
        if not isinstance(route, dict):
            route = {"segments": {}}
        segments = route.get("segments")
        if not isinstance(segments, dict):
            segments = {}
            route["segments"] = segments

        previous = segments.get(key)
        if isinstance(previous, dict):
            try:
                samples = max(0, int(previous.get("samples", 0)))
            except (TypeError, ValueError, OverflowError):
                samples = 0
            previous_dx = _finite_number(previous.get("mean_dx"))
            previous_dy = _finite_number(previous.get("mean_dy"))
        else:
            samples = 0
            previous_dx = None
            previous_dy = None

        if samples <= 0 or previous_dx is None or previous_dy is None:
            mean_dx = dx
            mean_dy = dy
            samples = 1
        else:
            new_samples = samples + 1
            # Exact running mean for the first 20 successes, then a 1/20 EMA so
            # the route can still adapt if camera sensitivity drifts later.
            alpha = 1.0 / min(new_samples, 20)
            mean_dx = previous_dx + (dx - previous_dx) * alpha
            mean_dy = previous_dy + (dy - previous_dy) * alpha
            samples = new_samples

        segments[key] = {
            "samples": samples,
            "mean_dx": mean_dx,
            "mean_dy": mean_dy,
        }
        routes.pop(route_fingerprint, None)
        routes[route_fingerprint] = route
        while len(routes) > _MAX_ROUTES:
            routes.pop(next(iter(routes)))
        return _write_cache(payload)
