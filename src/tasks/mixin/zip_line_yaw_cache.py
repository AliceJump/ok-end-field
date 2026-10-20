from __future__ import annotations

import json
import math
import os
import time
from contextlib import suppress
from pathlib import Path
from threading import RLock

from src.core.camera_input_config import read_saved_camera_settings
from src.core.learning.environment import LearningEnvironment
from src.core.learning.lookup import combine_records
from src.core.learning.models import LearningRecord
from src.core.learning.policy import learning_allowed

_CACHE_VERSION = 2
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
    if not isinstance(payload, dict) or payload.get("version") not in (1, _CACHE_VERSION):
        return _empty_cache()
    if not isinstance(payload.get("turns"), dict):
        return _empty_cache()
    if payload["version"] == 1:
        return {"version": _CACHE_VERSION, "turns": {}, "legacy_turns": payload["turns"]}
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


def yaw_environment(width, height, start_yaw, *, scroll=False):
    values, fallback = read_saved_camera_settings()
    return LearningEnvironment(
        **values,
        capture_size=(width, height),
        scroll=scroll,
        start_yaw=float(start_yaw) % 360.0,
        fallback_fields=fallback,
    )


def _new_yaw_record(entries, requested, environment):
    combined = None
    for source_key, entry in entries.items():
        try:
            if json.loads(source_key)[1:3] != requested:
                continue
            record = LearningRecord.from_dict(entry)
            if not record.observations or any(
                sample.environment.scroll != environment.scroll for sample in record.observations
            ):
                continue
            combined = combine_records(combined, record, environment)
        except (KeyError, TypeError, ValueError):
            continue
    return combined


def _legacy_yaw_motion(legacy_turns, requested, environment):
    """Old counts intentionally stay unchanged, independently of new statistics."""
    if not isinstance(legacy_turns, dict) or environment.scroll:
        return None
    candidates = []
    for old_key, entry in legacy_turns.items():
        try:
            source_key = json.loads(old_key)
            if source_key[1:3] != requested:
                continue
            count = entry["samples"]
            dx = _finite_number(entry["mean_dx"])
            if type(count) is not int or count <= 0 or dx is None or not 0 < abs(dx) <= 6000:
                continue
            candidates.append((source_key[0] == list(environment.capture_size), count, round(dx)))
        except (KeyError, TypeError, ValueError):
            continue
    if not candidates:
        return None
    _, count, dx = max(candidates)
    return dx, count


def load_arrow_turn_motion(width, height, start_yaw, target_yaw, *, environment=None):
    """Prefer converted new history; independently fall back to unconverted old counts."""
    if not learning_allowed():
        return None
    key = _turn_key(width, height, start_yaw, target_yaw)
    if key is None:
        return None
    environment = environment or yaw_environment(width, height, start_yaw)
    requested = json.loads(key)[1:]
    with _CACHE_LOCK:
        payload = _read_cache()
    combined = _new_yaw_record(payload["turns"], requested, environment)
    if combined is None:
        return _legacy_yaw_motion(payload.get("legacy_turns"), requested, environment)
    motion = combined.motion_for(environment)
    if not motion.samples:
        return None
    move_dx = round(motion.mean_dx)
    if move_dx == 0:
        return None
    return move_dx, motion.samples


def record_arrow_turn_motion(width, height, start_yaw, target_yaw, total_dx, *, environment=None) -> bool:
    """Learn a successful horizontal raw-mouse turn for matching size/yaw buckets."""
    if not learning_allowed():
        return False
    key = _turn_key(width, height, start_yaw, target_yaw)
    dx = _finite_number(total_dx)
    if key is None or dx is None or dx == 0:
        return False
    environment = environment or yaw_environment(width, height, start_yaw)
    key = json.dumps([*json.loads(key), environment.scroll], separators=(",", ":"))
    with _CACHE_LOCK:
        payload = _read_cache()
        turns = payload["turns"]
        try:
            previous = LearningRecord.from_dict(turns[key]) if key in turns else LearningRecord()
        except (KeyError, TypeError, ValueError):
            previous = LearningRecord()
        updated = previous.successful(dx, 0, now=time.time(), environment=environment, verification="yaw")
        turns.pop(key, None)
        turns[key] = updated.to_dict()
        while len(turns) > _MAX_TURNS:
            turns.pop(next(iter(turns)))
        return _write_cache(payload)
