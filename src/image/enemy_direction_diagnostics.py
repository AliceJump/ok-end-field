"""Diagnostics for the off-screen enemy-direction marker probe."""

from __future__ import annotations

import json
import math
import queue
import threading
from datetime import datetime
from pathlib import Path

import cv2
from ok import Box, og

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_probe import (
    EnemyDirectionObservation,
    _INNER_REFERENCE_RING,
    _OUTER_REFERENCE_RING,
    _TARGET_RING,
)
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES

_DEBUG_SCAN_COLOR = (0, 255, 255)
_DEBUG_HIT_COLOR = (0, 255, 0)
_DEBUG_TEXT_COLOR = (255, 255, 255)
_DIRECTION_ARTIFACT_QUEUE_MAXSIZE = 8
_RING_SPECS = (
    ("inner_reference", _INNER_REFERENCE_RING),
    ("target", _TARGET_RING),
    ("outer_reference", _OUTER_REFERENCE_RING),
)


def _debug_overlay_enabled(task) -> bool:
    checker = getattr(task, "_is_debug_overlay_enabled", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _save_direction_frames_enabled(task) -> bool:
    """Reuse the existing enemy-detection capture option for direction probes."""
    config = getattr(task, "config", None)
    getter = getattr(config, "get", None)
    if not callable(getter):
        return False
    try:
        value = getter(KEY_SAVE_ENEMY_PRESENCE_FRAMES, False)
    except Exception:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _ring_radii(frame, ring: tuple[float, float]) -> tuple[int, int]:
    height = frame.shape[0]
    return int(round(ring[0] * height)), int(round(ring[1] * height))


def _ring_box(frame, ring: tuple[float, float]) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    cx = width / 2.0
    cy = height / 2.0
    _inner, outer = _ring_radii(frame, ring)
    x1 = max(0, int(round(cx - outer)))
    y1 = max(0, int(round(cy - outer)))
    x2 = min(width, int(round(cx + outer + 1)))
    y2 = min(height, int(round(cy + outer + 1)))
    return x1, y1, x2, y2


def _marker_box(frame, observation: EnemyDirectionObservation) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    cx = width / 2.0
    cy = height / 2.0
    inner, outer = _ring_radii(frame, _TARGET_RING)
    radius = (inner + outer) / 2.0
    radians = math.radians(observation.angle_deg)
    x = int(round(cx + math.cos(radians) * radius))
    y = int(round(cy + math.sin(radians) * radius))
    half = max(4, int(round(14 * height / 1080.0)))
    x1 = max(0, x - half)
    y1 = max(0, y - half)
    x2 = min(width, x + half + 1)
    y2 = min(height, y + half + 1)
    return x1, y1, x2, y2


def _draw_live_overlay(task, frame, observation: EnemyDirectionObservation | None, streak: int, action: str) -> None:
    if not _debug_overlay_enabled(task):
        return
    draw_boxes = getattr(task, "draw_boxes", None)
    if not callable(draw_boxes):
        return

    boxes = []
    for label, ring in _RING_SPECS:
        x1, y1, x2, y2 = _ring_box(frame, ring)
        box = Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        box.name = f"enemy_direction_scan:{label}"
        box.confidence = 1.0
        boxes.append(box)

    if observation is not None:
        x1, y1, x2, y2 = _marker_box(frame, observation)
        box = Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        box.name = (
            f"enemy_direction_hit:{observation.angle_deg:.1f}deg:"
            f"score={observation.score:.1f}:streak={streak}:{action}"
        )
        box.confidence = 1.0
        boxes.append(box)

    # Keep all direction diagnostics in one layer so each draw replaces the
    # previous probe result without clearing overlays owned by other features.
    draw_boxes("enemy_direction_debug", boxes, color="blue", debug=True)


def _annotate_direction_frame(
    frame,
    observation: EnemyDirectionObservation | None,
    presence: EnemyPresence,
    streak: int,
    action: str,
    mouse_delta: tuple[int, int] | None,
    error: str | None,
):
    annotated = frame.copy()
    height, width = annotated.shape[:2]
    cx = width // 2
    cy = height // 2
    thickness = max(1, int(round(height / 540.0)))
    font_scale = max(0.45, height / 2160.0)

    for label, ring in _RING_SPECS:
        inner, outer = _ring_radii(annotated, ring)
        cv2.circle(annotated, (cx, cy), inner, _DEBUG_SCAN_COLOR, thickness, cv2.LINE_AA)
        cv2.circle(annotated, (cx, cy), outer, _DEBUG_SCAN_COLOR, thickness, cv2.LINE_AA)
        cv2.putText(
            annotated,
            label,
            (max(2, cx - outer), max(18, cy - outer - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            _DEBUG_SCAN_COLOR,
            thickness,
            cv2.LINE_AA,
        )

    cv2.drawMarker(
        annotated,
        (cx, cy),
        _DEBUG_SCAN_COLOR,
        markerType=cv2.MARKER_CROSS,
        markerSize=max(12, int(round(22 * height / 1080.0))),
        thickness=thickness,
        line_type=cv2.LINE_AA,
    )

    if observation is not None:
        x1, y1, x2, y2 = _marker_box(annotated, observation)
        hit_x = (x1 + x2 - 1) // 2
        hit_y = (y1 + y2 - 1) // 2
        cv2.line(annotated, (cx, cy), (hit_x, hit_y), _DEBUG_HIT_COLOR, thickness, cv2.LINE_AA)
        cv2.rectangle(
            annotated,
            (x1, y1),
            (max(x1, x2 - 1), max(y1, y2 - 1)),
            _DEBUG_HIT_COLOR,
            thickness,
        )
        cv2.putText(
            annotated,
            f"HIT {observation.angle_deg:.1f}deg score={observation.score:.1f}",
            (max(2, x1), max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            _DEBUG_HIT_COLOR,
            thickness,
            cv2.LINE_AA,
        )

    result = "error" if error else ("hit" if observation is not None else "miss")
    lines = [
        f"enemy_direction={result} presence={presence.value}",
        f"streak={streak} action={action}",
    ]
    if mouse_delta is not None:
        lines.append(f"mouse_delta=({mouse_delta[0]},{mouse_delta[1]})")
    if error:
        lines.append(f"error={error}")

    line_step = max(22, int(round(30 * height / 1080.0)))
    for index, text in enumerate(lines):
        cv2.putText(
            annotated,
            text,
            (10, max(24, line_step * (index + 1))),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.55, height / 1800.0),
            _DEBUG_TEXT_COLOR,
            thickness,
            cv2.LINE_AA,
        )
    return annotated


def _build_direction_inform(
    stem: str,
    frame,
    observation: EnemyDirectionObservation | None,
    presence: EnemyPresence,
    streak: int,
    action: str,
    mouse_delta: tuple[int, int] | None,
    error: str | None,
    sequence: int,
    captured_at: datetime,
) -> dict:
    height, width = frame.shape[:2]
    ring_data = []
    for label, ring in _RING_SPECS:
        inner, outer = _ring_radii(frame, ring)
        x1, y1, x2, y2 = _ring_box(frame, ring)
        ring_data.append(
            {
                "label": label,
                "inner_radius": inner,
                "outer_radius": outer,
                "bounding_box": {
                    "x": x1,
                    "y": y1,
                    "width": x2 - x1,
                    "height": y2 - y1,
                    "x2": x2,
                    "y2": y2,
                },
            }
        )

    marker = None
    if observation is not None:
        x1, y1, x2, y2 = _marker_box(frame, observation)
        marker = {
            "angle_deg": float(observation.angle_deg),
            "score": float(observation.score),
            "box": {
                "x": x1,
                "y": y1,
                "width": x2 - x1,
                "height": y2 - y1,
                "x2": x2,
                "y2": y2,
            },
        }

    return {
        "schema": "enemy_direction_probe/v1",
        "image": f"{stem}.png",
        "captured_at": captured_at.isoformat(timespec="microseconds"),
        "sequence": int(sequence),
        "presence": presence.value,
        "detected": observation is not None,
        "result": "error" if error else ("hit" if observation is not None else "miss"),
        "streak": int(streak),
        "action": action,
        "mouse_delta": (
            {"dx": int(mouse_delta[0]), "dy": int(mouse_delta[1])}
            if mouse_delta is not None
            else None
        ),
        "error": error,
        "screen": {
            "width": int(width),
            "height": int(height),
            "center": {"x": width / 2.0, "y": height / 2.0},
        },
        "scan_rings": ring_data,
        "observation": marker,
    }


def _resolve_direction_capture_dir(task) -> Path | None:
    override = getattr(task, "_enemy_direction_debug_folder", None)
    if override:
        return Path(override)

    screenshot_manager = getattr(getattr(og, "ok", None), "screenshot", None)
    screenshot_folder = getattr(screenshot_manager, "screenshot_folder", None)
    if not screenshot_folder:
        return None
    return Path(screenshot_folder) / "enemy_direction"


def _write_direction_artifact(output_dir: Path, stem: str, annotated, inform: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{stem}.png"
    info_path = output_dir / f"{stem}.inform.json"
    temp_image = output_dir / f".{stem}.tmp.png"
    temp_info = output_dir / f".{stem}.tmp.inform.json"

    try:
        if not cv2.imwrite(str(temp_image), annotated):
            raise OSError(f"cv2.imwrite returned false for {temp_image}")
        with temp_info.open("w", encoding="utf-8") as file:
            json.dump(inform, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temp_image.replace(image_path)
        temp_info.replace(info_path)
    finally:
        for temp_path in (temp_image, temp_info):
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def _log_direction_save_error_once(task, exc: Exception) -> None:
    if getattr(task, "_enemy_direction_debug_save_error_logged", False):
        return
    task._enemy_direction_debug_save_error_logged = True
    logger = getattr(task, "log_debug", None)
    if callable(logger):
        try:
            logger(f"敌人方向检测调试帧保存失败: {exc}")
        except Exception:
            pass


def _direction_artifact_worker(work_queue) -> None:
    while True:
        task, output_dir, stem, annotated, inform = work_queue.get()
        try:
            _write_direction_artifact(output_dir, stem, annotated, inform)
        except Exception as exc:
            _log_direction_save_error_once(task, exc)
        finally:
            work_queue.task_done()


def _queue_direction_artifact(task, output_dir, stem, annotated, inform) -> None:
    if getattr(task, "_enemy_direction_debug_sync_save", False):
        try:
            _write_direction_artifact(output_dir, stem, annotated, inform)
        except Exception as exc:
            _log_direction_save_error_once(task, exc)
        return

    work_queue = getattr(task, "_enemy_direction_artifact_queue", None)
    if work_queue is None:
        work_queue = queue.Queue(maxsize=_DIRECTION_ARTIFACT_QUEUE_MAXSIZE)
        task._enemy_direction_artifact_queue = work_queue
        worker = threading.Thread(
            target=_direction_artifact_worker,
            args=(work_queue,),
            name="EnemyDirectionArtifactWriter",
            daemon=True,
        )
        task._enemy_direction_artifact_worker = worker
        worker.start()

    try:
        work_queue.put_nowait((task, output_dir, stem, annotated, inform))
    except queue.Full:
        _log_direction_save_error_once(
            task,
            RuntimeError("敌人方向检测调试帧写入队列已满，已丢弃当前帧"),
        )


def _save_direction_frame(
    task,
    frame,
    observation: EnemyDirectionObservation | None,
    presence: EnemyPresence,
    streak: int,
    action: str,
    mouse_delta: tuple[int, int] | None,
    error: str | None,
) -> None:
    if not _save_direction_frames_enabled(task):
        return

    try:
        output_dir = _resolve_direction_capture_dir(task)
        if output_dir is None:
            raise RuntimeError("screenshot folder is unavailable")

        sequence = int(getattr(task, "_enemy_direction_debug_frame_seq", 0)) + 1
        task._enemy_direction_debug_frame_seq = sequence
        captured_at = datetime.now().astimezone()
        result = "error" if error else ("hit" if observation is not None else "miss")
        stem = (
            f"enemy_direction_{captured_at.strftime('%Y%m%d_%H%M%S')}_"
            f"{captured_at.microsecond:06d}_{sequence:06d}_{result}"
        )
        annotated = _annotate_direction_frame(
            frame,
            observation,
            presence,
            streak,
            action,
            mouse_delta,
            error,
        )
        inform = _build_direction_inform(
            stem,
            frame,
            observation,
            presence,
            streak,
            action,
            mouse_delta,
            error,
            sequence,
            captured_at,
        )
        _queue_direction_artifact(task, output_dir, stem, annotated, inform)
    except Exception as exc:
        _log_direction_save_error_once(task, exc)


def draw_enemy_direction_debug(
    task,
    frame,
    observation: EnemyDirectionObservation | None,
    presence: EnemyPresence,
    *,
    streak: int,
    action: str,
    mouse_delta: tuple[int, int] | None = None,
    error: str | None = None,
) -> None:
    """Emit fail-soft live and persisted evidence for one actual probe attempt."""
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return

    try:
        _draw_live_overlay(task, frame, observation, streak, action)
    except Exception:
        # Live diagnostics must not affect camera recovery.
        pass
    _save_direction_frame(
        task,
        frame,
        observation,
        presence,
        streak,
        action,
        mouse_delta,
        error,
    )
