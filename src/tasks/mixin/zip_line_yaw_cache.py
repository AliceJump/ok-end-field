from __future__ import annotations

import json
import math
import os
from contextlib import suppress
from pathlib import Path
from threading import RLock

_CACHE_VERSION = 1
_DEFAULT_CACHE_PATH = Path("tmp") / "zipline_yaw_cache.json"
_CACHE_LOCK = RLock()
_MAX_TURNS = 512
_YAW_BUCKET_DEGREES = 5.0


def _empty_cache() -> dict:
    return {"version": _CACHE_VERSION, "turns": {}}


def _read_cache() -> dict:
    try:
        payload = json.loads(_DEFAULT_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _empty_cache()
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        return _empty_cache()
    if not isinstance(payload.get("turns"), dict):
        return _empty_cache()
    return payload


def _write_cache(payload: dict) -> bool:
    path = _DEFAULT_CACHE_PATH
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
        return True
    except (OSError, ValueError, TypeError):
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        return False


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _normalize_yaw(value) -> float | None:
    yaw = _finite_number(value)
    if yaw is None:
        return None
    return yaw % 360.0


def _resolution(width, height) -> tuple[int, int] | None:
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError, OverflowError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _yaw_bucket(yaw) -> int | None:
    normalized = _normalize_yaw(yaw)
    if normalized is None:
        return None
    bucket_count = round(360.0 / _YAW_BUCKET_DEGREES)
    return round(normalized / _YAW_BUCKET_DEGREES) % bucket_count


def build_zip_line_motion_route_fingerprint(route_fingerprint, width, height, start_yaw) -> str | None:
    """Extend a route identity with exact capture size and the original starting-yaw bucket."""
    route = str(route_fingerprint or "").strip()
    size = _resolution(width, height)
    yaw_bucket = _yaw_bucket(start_yaw)
    if not route or size is None or yaw_bucket is None:
        return None
    return json.dumps(
        [route, [size[0], size[1]], yaw_bucket],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _turn_key(width, height, start_yaw, target_yaw) -> str | None:
    size = _resolution(width, height)
    start_bucket = _yaw_bucket(start_yaw)
    target_bucket = _yaw_bucket(target_yaw)
    if size is None or start_bucket is None or target_bucket is None:
        return None
    return json.dumps(
        [[size[0], size[1]], start_bucket, target_bucket],
        separators=(",", ":"),
    )


def load_arrow_turn_motion(width, height, start_yaw, target_yaw):
    """Return ``(raw_dx, samples)`` for the same size and yaw buckets."""
    key = _turn_key(width, height, start_yaw, target_yaw)
    if key is None:
        return None
    with _CACHE_LOCK:
        entry = _read_cache()["turns"].get(key)
    if not isinstance(entry, dict):
        return None
    try:
        samples = int(entry.get("samples", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    mean_dx = _finite_number(entry.get("mean_dx"))
    if samples <= 0 or mean_dx is None:
        return None
    move_dx = round(mean_dx)
    if move_dx == 0:
        return None
    return move_dx, samples


def record_arrow_turn_motion(width, height, start_yaw, target_yaw, total_dx) -> bool:
    """Learn a successful horizontal raw-mouse turn for matching size/yaw buckets."""
    key = _turn_key(width, height, start_yaw, target_yaw)
    dx = _finite_number(total_dx)
    if key is None or dx is None or dx == 0:
        return False
    with _CACHE_LOCK:
        payload = _read_cache()
        turns = payload["turns"]
        previous = turns.get(key)
        if isinstance(previous, dict):
            try:
                samples = max(0, int(previous.get("samples", 0)))
            except (TypeError, ValueError, OverflowError):
                samples = 0
            previous_dx = _finite_number(previous.get("mean_dx"))
        else:
            samples = 0
            previous_dx = None

        if samples <= 0 or previous_dx is None:
            mean_dx = dx
            samples = 1
        else:
            new_samples = samples + 1
            alpha = 1.0 / min(new_samples, 20)
            mean_dx = previous_dx + (dx - previous_dx) * alpha
            samples = new_samples

        turns.pop(key, None)
        turns[key] = {"samples": samples, "mean_dx": mean_dx}
        while len(turns) > _MAX_TURNS:
            turns.pop(next(iter(turns)))
        return _write_cache(payload)
