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
    EnemyDirectionMarker,
    EnemyDirectionObservation,
    _ELLIPSE_ANNULUS_SCALE,
    _MARKER_ARC_WIDTH_DEG,
    _direction_to_parameter_deg,
    _ellipse_axes,
)
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES

_DEBUG_HIT_COLOR = (0, 255, 0)
_DEBUG_TEXT_COLOR = (255, 255, 255)
_DIRECTION_ARTIFACT_QUEUE_MAXSIZE = 8


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


def _ellipse_geometry(frame) -> tuple[int, int, float, float]:
    height, width = frame.shape[:2]
    semi_axis_x, semi_axis_y = _ellipse_axes(width, height)
    return width // 2, height // 2, semi_axis_x, semi_axis_y


def _scan_box(frame) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    cx, cy, semi_axis_x, semi_axis_y = _ellipse_geometry(frame)
    outer = _ELLIPSE_ANNULUS_SCALE[1]
    x1 = max(0, int(round(cx - semi_axis_x * outer)))
    y1 = max(0, int(round(cy - semi_axis_y * outer)))
    x2 = min(width, int(round(cx + semi_axis_x * outer + 1)))
    y2 = min(height, int(round(cy + semi_axis_y * outer + 1)))
    return x1, y1, x2, y2


def _observation_markers(frame, observation: EnemyDirectionObservation | None) -> tuple[EnemyDirectionMarker, ...]:
    if observation is None:
        return ()
    if observation.markers:
        return observation.markers
    _cx, _cy, semi_axis_x, semi_axis_y = _ellipse_geometry(frame)
    parameter = _direction_to_parameter_deg(observation.angle_deg, semi_axis_x, semi_axis_y)
    return (
        EnemyDirectionMarker(
            angle_deg=float(observation.angle_deg),
            parameter_angle_deg=parameter,
            score=float(observation.score),
        ),
    )


def _marker_center(frame, marker: EnemyDirectionMarker) -> tuple[int, int]:
    cx, cy, semi_axis_x, semi_axis_y = _ellipse_geometry(frame)
    radians = math.radians(marker.parameter_angle_deg)
    x = int(round(cx + semi_axis_x * math.cos(radians)))
    y = int(round(cy + semi_axis_y * math.sin(radians)))
    return x, y


def _marker_box(frame, marker: EnemyDirectionMarker) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    x, y = _marker_center(frame, marker)
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
    x1, y1, x2, y2 = _scan_box(frame)
    scan_box = Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
    scan_box.name = "enemy_direction_scan:ellipse_annulus"
    scan_box.confidence = 1.0
    boxes.append(scan_box)

    for index, marker in enumerate(_observation_markers(frame, observation), start=1):
        x1, y1, x2, y2 = _marker_box(frame, marker)
        box = Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        box.name = (
            f"enemy_direction_hit:{index}:{marker.angle_deg:.1f}deg:"
            f"score={marker.score:.1f}:streak={streak}:{action}"
        )
        box.confidence = 1.0
        boxes.append(box)

    # Live overlay is constrained to Box primitives. Saved evidence below burns
    # the actual fixed-width ellipse arcs instead of rectangular hit boxes.
    draw_boxes("enemy_direction_debug", boxes, color="blue", debug=True)


def _draw_marker_arc(frame, marker: EnemyDirectionMarker, color, thickness: int) -> None:
    cx, cy, semi_axis_x, semi_axis_y = _ellipse_geometry(frame)
    half_arc = marker.arc_width_deg / 2.0
    cv2.ellipse(
        frame,
        (cx, cy),
        (max(1, int(round(semi_axis_x))), max(1, int(round(semi_axis_y)))),
        0,
        marker.parameter_angle_deg - half_arc,
        marker.parameter_angle_deg + half_arc,
        color,
        thickness,
        cv2.LINE_AA,
    )
    hit_x, hit_y = _marker_center(frame, marker)
    cv2.circle(frame, (hit_x, hit_y), max(2, thickness + 1), color, -1, cv2.LINE_AA)


def _annotate_direction_frame(
    frame,
    observation: EnemyDirectionObservation | None,
    presence: EnemyPresence,
    streak: int,
    action: str,
    mouse_delta: tuple[int, int] | None,
    error: str | None,
):
    """Burn only detected fixed-width marker arcs; never draw rectangular hits."""
    annotated = frame
    height = annotated.shape[0]
    thickness = max(2, int(round(3 * height / 1080.0)))

    for marker in _observation_markers(annotated, observation):
        _draw_marker_arc(annotated, marker, _DEBUG_HIT_COLOR, thickness)

    result = "error" if error else ("hit" if observation is not None else "miss")
    lines = [
        f"enemy_direction={result} presence={presence.value}",
        f"streak={streak} action={action}",
    ]
    if observation is not None:
        lines.append(f"markers={len(_observation_markers(annotated, observation))}")
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
            max(1, int(round(height / 540.0))),
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
    _cx, _cy, semi_axis_x, semi_axis_y = _ellipse_geometry(frame)
    inner_scale, outer_scale = _ELLIPSE_ANNULUS_SCALE
    markers = _observation_markers(frame, observation)

    marker_data = [
        {
            "angle_deg": float(marker.angle_deg),
            "parameter_angle_deg": float(marker.parameter_angle_deg),
            "score": float(marker.score),
            "arc_width_deg": float(marker.arc_width_deg),
        }
        for marker in markers
    ]
    selected = None
    if observation is not None:
        selected = {
            "angle_deg": float(observation.angle_deg),
            "score": float(observation.score),
            "markers": marker_data,
        }

    return {
        "schema": "enemy_direction_probe/v2",
        "image": f"{stem}.png",
        "annotated_image": f"{stem}.png",
        "raw_image": f"{stem}.raw.png",
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
        "scan_annulus": {
            "semi_axis_x": float(semi_axis_x),
            "semi_axis_y": float(semi_axis_y),
            "inner_scale": float(inner_scale),
            "outer_scale": float(outer_scale),
            "inner_axes": {
                "x": float(semi_axis_x * inner_scale),
                "y": float(semi_axis_y * inner_scale),
            },
            "outer_axes": {
                "x": float(semi_axis_x * outer_scale),
                "y": float(semi_axis_y * outer_scale),
            },
        },
        "marker_arc_width_deg": float(_MARKER_ARC_WIDTH_DEG),
        "observation": selected,
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


def _write_direction_artifact(
    output_dir: Path,
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
) -> None:
    """Write raw input, annotated evidence, and metadata from one owned snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{stem}.raw.png"
    image_path = output_dir / f"{stem}.png"
    info_path = output_dir / f"{stem}.inform.json"
    temp_raw = output_dir / f".{stem}.tmp.raw.png"
    temp_image = output_dir / f".{stem}.tmp.png"
    temp_info = output_dir / f".{stem}.tmp.inform.json"

    try:
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
        if not cv2.imwrite(str(temp_raw), frame):
            raise OSError(f"cv2.imwrite returned false for {temp_raw}")
        annotated = _annotate_direction_frame(
            frame,
            observation,
            presence,
            streak,
            action,
            mouse_delta,
            error,
        )
        if not cv2.imwrite(str(temp_image), annotated):
            raise OSError(f"cv2.imwrite returned false for {temp_image}")
        with temp_info.open("w", encoding="utf-8") as file:
            json.dump(inform, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temp_raw.replace(raw_path)
        temp_image.replace(image_path)
        temp_info.replace(info_path)
    finally:
        for temp_path in (temp_raw, temp_image, temp_info):
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
        (
            task,
            output_dir,
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
        ) = work_queue.get()
        try:
            _write_direction_artifact(
                output_dir,
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
        except Exception as exc:
            _log_direction_save_error_once(task, exc)
        finally:
            work_queue.task_done()


def _queue_direction_artifact(
    task,
    output_dir,
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
) -> None:
    if getattr(task, "_enemy_direction_debug_sync_save", False):
        try:
            snapshot = frame.copy()
            _write_direction_artifact(
                output_dir,
                stem,
                snapshot,
                observation,
                presence,
                streak,
                action,
                mouse_delta,
                error,
                sequence,
                captured_at,
            )
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

    if work_queue.full():
        _log_direction_save_error_once(
            task,
            RuntimeError("敌人方向检测调试帧写入队列已满，已丢弃当前帧"),
        )
        return

    snapshot = frame.copy()
    try:
        work_queue.put_nowait(
            (
                task,
                output_dir,
                stem,
                snapshot,
                observation,
                presence,
                streak,
                action,
                mouse_delta,
                error,
                sequence,
                captured_at,
            )
        )
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
        _queue_direction_artifact(
            task,
            output_dir,
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
    """Emit fail-soft live, raw, annotated, and metadata evidence for one probe."""
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return

    try:
        _draw_live_overlay(task, frame, observation, streak, action)
    except Exception:
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
