"""Fast enemy-presence probe based on Endfield's enemy HP-bar UI color."""

from __future__ import annotations

import json
import math
import queue
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from ok import Box, og

from src.data.combat_observation import EnemyPresence

# Samples: 1920x1080 combat screenshots supplied for character-mechanics-v1.
# Enemy fill is a very stable UI pink (BGR around 102, 68, 255), so stay in
# BGR and avoid a full-frame BGR->HSV conversion.
ENEMY_HP_BGR_LOWER = np.array([95, 58, 220], dtype=np.uint8)
ENEMY_HP_BGR_UPPER = np.array([125, 100, 255], dtype=np.uint8)

# Fixed HUD/scene regions. Boss HP is top-center. Normal HP bars can reach the
# left/right screen edge, so keep the full width but stop before bottom HUD.
ENEMY_BOSS_HP_REGION = (0.25, 0.025, 0.75, 0.10)
ENEMY_NORMAL_HP_REGION = (0.0, 0.12, 1.0, 0.70)
ENEMY_NORMAL_HP_SLICES = 4

# Absence is intentionally slower than presence: one normal slice per probe,
# and two complete clean rounds before ABSENT. Timed combat keeps attacking and
# target-locking during ABSENT, so a later PRESENT resumes immediately.
ENEMY_ABSENT_CONFIRM_ROUNDS = 2

# 1080p reference geometry. Sampling and candidate height scale with capture
# height, while width decisions are derived from the measured candidate height
# so distant/thinner bars get proportionally smaller width thresholds.
ENEMY_HP_SAMPLE_STEP_1080 = 3
ENEMY_HP_MIN_HEIGHT_1080 = 4
ENEMY_HP_MIN_WIDTH_HEIGHT_RATIO = 2.0
ENEMY_HP_DIRECT_WIDTH_HEIGHT_RATIO = 10.0
ENEMY_HP_MAX_DISCOVERY_ROWS = 64
ENEMY_HP_MAX_GEOMETRY_ROWS = 16

# Short pink runs are accepted only when a nearby horizontal UI edge confirms
# that the pixels belong to an HP/stagger-bar structure instead of combat VFX.
# Candidate widths below 2x their measured height are rejected, widths from 2x
# to below 10x require this context, and widths at least 10x pass directly.
ENEMY_HP_CONTEXT_SEARCH_DOWN_1080 = 12
ENEMY_HP_CONTEXT_X_PAD_1080 = 80
ENEMY_HP_CONTEXT_MIN_EDGE_RUN_1080 = 18
ENEMY_HP_CONTEXT_MIN_OVERLAP_1080 = 3
ENEMY_HP_CONTEXT_EDGE_DELTA = 25

# AutoCombatTask explicit diagnostics switch. It is intentionally independent
# from the framework's project debug flag and live-overlay toggle.
KEY_SAVE_ENEMY_PRESENCE_FRAMES = "保存敌人检测调试帧"

# Saved evidence uses obvious BGR colors: yellow = scanned ROI, green = the
# exact HP-colored run that satisfied the detector.
_DEBUG_SCAN_COLOR = (0, 255, 255)
_DEBUG_HIT_COLOR = (0, 255, 0)
_DEBUG_TEXT_COLOR = (255, 255, 255)

# A 4K BGR frame is ~24 MiB before Python/OpenCV overhead. Keep only a small
# bounded backlog so slow PNG encoding or a busy disk can never grow memory
# without bound. Diagnostic capture may drop frames under sustained overload.
_ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE = 8

_RUN_KERNELS: dict[int, np.ndarray] = {}
_FULL_SLICE_MASK = (1 << ENEMY_NORMAL_HP_SLICES) - 1


def reset_enemy_presence_probe(task) -> None:
    task._enemy_hp_last_slice = None
    task._enemy_hp_checked_mask = 0
    task._enemy_hp_clean_rounds = 0
    task._enemy_hp_scan_cursor = 0


def _scaled_px(value: int, _screen_width: int, screen_height: int) -> int:
    scale = screen_height / 1080.0
    return max(1, int(round(value * scale)))


def _enemy_hp_width_thresholds(height: int) -> tuple[int, int]:
    """Return reject/context and context/direct width boundaries for ``height``."""
    min_width = max(1, int(math.ceil(height * ENEMY_HP_MIN_WIDTH_HEIGHT_RATIO)))
    direct_width = max(min_width, int(math.ceil(height * ENEMY_HP_DIRECT_WIDTH_HEIGHT_RATIO)))
    return min_width, direct_width


def _normalized_rect(frame, region):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    px1 = max(0, min(width, int(round(x1 * width))))
    py1 = max(0, min(height, int(round(y1 * height))))
    px2 = max(px1, min(width, int(round(x2 * width))))
    py2 = max(py1, min(height, int(round(y2 * height))))
    return px1, py1, px2, py2


def _crop_normalized(frame, region):
    px1, py1, px2, py2 = _normalized_rect(frame, region)
    return frame[py1:py2, px1:px2]


def _normal_slice_region(index: int):
    x1, y1, x2, y2 = ENEMY_NORMAL_HP_REGION
    span = (y2 - y1) / ENEMY_NORMAL_HP_SLICES
    sy1 = y1 + span * index
    sy2 = sy1 + span
    return x1, sy1, x2, sy2


def _normal_slice(frame, index: int):
    return _crop_normalized(frame, _normal_slice_region(index))


def _run_kernel(width: int):
    kernel = _RUN_KERNELS.get(width)
    if kernel is None:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width, 1))
        _RUN_KERNELS[width] = kernel
    return kernel


def _true_run_containing(values, index: int) -> tuple[int, int, int]:
    """Return the contiguous true run containing index, or an empty run."""
    if index < 0 or index >= len(values) or not values[index]:
        return 0, -1, 0

    start = index
    while start > 0 and values[start - 1]:
        start -= 1

    end = index
    while end + 1 < len(values) and values[end + 1]:
        end += 1

    return start, end, end - start + 1


def _row_has_overlapping_run(row, candidate_left: int, candidate_right: int, min_run: int, min_overlap: int) -> bool:
    """Return whether one true run is long enough and overlaps the candidate."""
    padded = np.pad(np.asarray(row, dtype=np.int8), (1, 1))
    transitions = np.diff(padded)
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    for start, end in zip(starts, ends):
        if end - start < min_run:
            continue
        overlap = min(end, candidate_right) - max(start, candidate_left)
        if overlap >= min_overlap:
            return True
    return False


def _has_enemy_hp_context(
    roi,
    left: int,
    top: int,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
) -> bool:
    """Confirm a short pink candidate using the nearby horizontal UI structure.

    Real low-HP bars keep a longer HP/stagger-bar slot around or below the small
    remaining pink fill. Fire-team particles can satisfy the pink geometry by
    themselves, but normally do not have a nearby aligned horizontal UI edge.
    """
    search_down = _scaled_px(ENEMY_HP_CONTEXT_SEARCH_DOWN_1080, screen_width, screen_height)
    x_pad = _scaled_px(ENEMY_HP_CONTEXT_X_PAD_1080, screen_width, screen_height)
    min_edge_run = _scaled_px(ENEMY_HP_CONTEXT_MIN_EDGE_RUN_1080, screen_width, screen_height)
    min_overlap = _scaled_px(ENEMY_HP_CONTEXT_MIN_OVERLAP_1080, screen_width, screen_height)

    x1 = max(0, left - x_pad)
    x2 = min(roi.shape[1], left + width + x_pad)
    y1 = max(0, top + height)
    y2 = min(roi.shape[0], y1 + search_down)
    if x2 <= x1 or y2 - y1 < 2:
        return False

    gray = cv2.cvtColor(roi[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    vertical_edges = cv2.absdiff(gray[:-1], gray[1:])
    edge_mask = vertical_edges >= ENEMY_HP_CONTEXT_EDGE_DELTA

    candidate_left = left - x1
    candidate_right = candidate_left + width
    for row in edge_mask:
        if _row_has_overlapping_run(row, candidate_left, candidate_right, min_edge_run, min_overlap):
            return True
    return False


def _find_enemy_hp_run(
    roi,
    screen_width: int,
    screen_height: int,
    *,
    context_roi=None,
    context_offset=(0, 0),
):
    """Return the local pixel box that passes HP geometry + context checks."""
    if roi is None or getattr(roi, "size", 0) == 0 or roi.ndim != 3:
        return None

    sample_step = _scaled_px(ENEMY_HP_SAMPLE_STEP_1080, screen_width, screen_height)
    min_height = _scaled_px(ENEMY_HP_MIN_HEIGHT_1080, screen_width, screen_height)
    # Discovery only needs the smallest width that could ever survive the
    # height-relative reject boundary. The exact threshold is recomputed from
    # each candidate's measured height below.
    discovery_min_run, _ = _enemy_hp_width_thresholds(min_height)

    sampled = roi[::sample_step]
    mask = cv2.inRange(sampled, ENEMY_HP_BGR_LOWER, ENEMY_HP_BGR_UPPER)
    horizontal = cv2.erode(mask, _run_kernel(discovery_min_run))
    points = cv2.findNonZero(horizontal)
    if points is None:
        return None

    candidates = points[:, 0, :]
    discovery_rows = 0
    geometry_rows = 0
    # Thin one-row VFX can satisfy the cheap horizontal discovery gate. Let
    # those rows fail the vertical-height check without consuming the tighter
    # geometry-row budget, while keeping the raw discovery scan explicitly
    # bounded so lowering the discovery width cannot make work unbounded.
    for sampled_y in np.unique(candidates[:, 1]):
        if discovery_rows >= ENEMY_HP_MAX_DISCOVERY_ROWS or geometry_rows >= ENEMY_HP_MAX_GEOMETRY_ROWS:
            break
        discovery_rows += 1
        row_has_geometry = False
        row_xs = np.sort(candidates[candidates[:, 1] == sampled_y, 0])
        segment_starts = row_xs[np.r_[True, np.diff(row_xs) > 1]]

        for x_value in segment_starts:
            x = int(x_value)
            y = int(sampled_y) * sample_step
            column = roi[:, x]
            matching = np.all(
                (column >= ENEMY_HP_BGR_LOWER) & (column <= ENEMY_HP_BGR_UPPER),
                axis=1,
            ).tolist()

            # The vertical evidence must belong to the very same horizontal
            # candidate row. Measure the complete contiguous run in this column
            # so height-relative width thresholds use the real candidate height.
            run_start, run_end, run_height = _true_run_containing(matching, y)
            if run_height < min_height:
                continue

            sampled_row = mask[int(sampled_y)] != 0
            left = x
            while left > 0 and sampled_row[left - 1]:
                left -= 1
            right = x
            while right + 1 < sampled_row.shape[0] and sampled_row[right + 1]:
                right += 1

            hit_top = int(run_start)
            hit_width = int(right - left + 1)
            hit_height = int(run_end - run_start + 1)
            min_width, direct_width = _enemy_hp_width_thresholds(hit_height)
            if hit_width < min_width:
                continue
            row_has_geometry = True

            if hit_width < direct_width:
                context_source = roi if context_roi is None else context_roi
                context_x, context_y = context_offset if context_roi is not None else (0, 0)
                if not _has_enemy_hp_context(
                    context_source,
                    int(left) + int(context_x),
                    hit_top + int(context_y),
                    hit_width,
                    hit_height,
                    screen_width,
                    screen_height,
                ):
                    # Widths in the middle band need the supporting HP/stagger-bar
                    # structure. Keep looking for another candidate if it is absent.
                    continue

            return int(left), hit_top, hit_width, hit_height

        if row_has_geometry:
            geometry_rows += 1
    return None


def _has_enemy_hp_run(roi, screen_width: int, screen_height: int) -> bool:
    """Find one HP-colored rectangle without contours or full-frame HSV."""
    return _find_enemy_hp_run(roi, screen_width, screen_height) is not None


def _probe_region(frame, region, screen_width: int, screen_height: int, *, context_region=None):
    rect = _normalized_rect(frame, region)
    px1, py1, px2, py2 = rect
    context_roi = None
    context_offset = (0, 0)
    if context_region is not None:
        cx1, cy1, cx2, cy2 = _normalized_rect(frame, context_region)
        context_roi = frame[cy1:cy2, cx1:cx2]
        context_offset = (px1 - cx1, py1 - cy1)

    local_hit = _find_enemy_hp_run(
        frame[py1:py2, px1:px2],
        screen_width,
        screen_height,
        context_roi=context_roi,
        context_offset=context_offset,
    )
    if local_hit is None:
        return rect, None

    hit_x, hit_y, hit_width, hit_height = local_hit
    return rect, (px1 + hit_x, py1 + hit_y, hit_width, hit_height)


def _debug_overlay_enabled(task) -> bool:
    checker = getattr(task, "_is_debug_overlay_enabled", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def _save_enemy_presence_frames_enabled(task) -> bool:
    """Read the explicit task option; project debug and live overlay are unrelated."""
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


def _annotate_enemy_presence_frame(frame, scanned_regions, hits, state: EnemyPresence):
    """Draw the exact detector evidence onto a copy of the current capture frame."""
    annotated = frame.copy()
    height = annotated.shape[0]
    thickness = max(1, int(round(height / 540.0)))
    font_scale = max(0.45, height / 2160.0)

    for label, rect, hit in scanned_regions:
        x1, y1, x2, y2 = rect
        cv2.rectangle(annotated, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), _DEBUG_SCAN_COLOR, thickness)
        text_y = min(max(18, y1 + 18), max(18, height - 4))
        cv2.putText(
            annotated,
            f"scan {label} {'hit' if hit else 'miss'}",
            (max(2, x1 + 4), text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            _DEBUG_SCAN_COLOR,
            thickness,
            cv2.LINE_AA,
        )

    for label, rect in hits:
        x, y, width, hit_height = rect
        cv2.rectangle(
            annotated,
            (x, y),
            (max(x, x + width - 1), max(y, y + hit_height - 1)),
            _DEBUG_HIT_COLOR,
            thickness,
        )
        cv2.putText(
            annotated,
            f"HIT {label} {width}x{hit_height}",
            (max(2, x), max(18, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            _DEBUG_HIT_COLOR,
            thickness,
            cv2.LINE_AA,
        )

    cv2.putText(
        annotated,
        f"enemy_presence={state.value}",
        (10, max(26, int(round(32 * height / 1080.0)))),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.55, height / 1800.0),
        _DEBUG_TEXT_COLOR,
        thickness,
        cv2.LINE_AA,
    )
    return annotated


def _scan_rect_to_json(rect) -> dict[str, int]:
    x1, y1, x2, y2 = rect
    return {
        "x": int(x1),
        "y": int(y1),
        "width": int(max(0, x2 - x1)),
        "height": int(max(0, y2 - y1)),
        "x2": int(x2),
        "y2": int(y2),
    }


def _hit_rect_to_json(rect) -> dict[str, int]:
    x, y, width, height = rect
    return {
        "x": int(x),
        "y": int(y),
        "width": int(width),
        "height": int(height),
        "x2": int(x + width),
        "y2": int(y + height),
    }


def _build_enemy_presence_inform(stem, frame, scanned_regions, hits, state, sequence, captured_at):
    """Build the sidecar payload that explains exactly what this probe inspected."""
    screen_height, screen_width = frame.shape[:2]
    hit_map = {label: rect for label, rect in hits}
    regions = []
    for label, rect, hit in scanned_regions:
        regions.append(
            {
                "label": label,
                "result": "hit" if hit else "miss",
                "scan_rect": _scan_rect_to_json(rect),
                "hit_box": _hit_rect_to_json(hit_map[label]) if label in hit_map else None,
            }
        )

    return {
        "schema": "enemy_presence_probe/v1",
        "image": f"{stem}.png",
        "captured_at": captured_at.isoformat(timespec="microseconds"),
        "sequence": int(sequence),
        "final_state": state.value,
        "detected": bool(hits),
        "screen": {
            "width": int(screen_width),
            "height": int(screen_height),
        },
        "scanned_regions": regions,
        "hits": [
            {
                "label": label,
                "box": _hit_rect_to_json(rect),
            }
            for label, rect in hits
        ],
    }


def _resolve_enemy_presence_capture_dir(task) -> Path | None:
    override = getattr(task, "_enemy_presence_debug_folder", None)
    if override:
        return Path(override)

    screenshot_manager = getattr(getattr(og, "ok", None), "screenshot", None)
    screenshot_folder = getattr(screenshot_manager, "screenshot_folder", None)
    if not screenshot_folder:
        return None
    return Path(screenshot_folder) / "enemy_presence"


def _write_enemy_presence_artifact(output_dir: Path, stem: str, annotated, inform: dict) -> None:
    """Write one PNG + same-stem .inform.json pair."""
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


def _log_enemy_presence_save_error_once(task, exc: Exception) -> None:
    if getattr(task, "_enemy_presence_debug_save_error_logged", False):
        return
    task._enemy_presence_debug_save_error_logged = True
    log_debug = getattr(task, "log_debug", None)
    if callable(log_debug):
        try:
            log_debug(f"敌人存在检测调试帧保存失败: {exc}")
        except Exception:
            pass


def _enemy_presence_artifact_worker(work_queue) -> None:
    """Drain PNG/JSON work without blocking the combat probe thread."""
    while True:
        task, output_dir, stem, annotated, inform = work_queue.get()
        try:
            _write_enemy_presence_artifact(output_dir, stem, annotated, inform)
        except Exception as exc:
            _log_enemy_presence_save_error_once(task, exc)
        finally:
            work_queue.task_done()


def _queue_enemy_presence_artifact(task, output_dir, stem, annotated, inform) -> None:
    """Queue one artifact pair, dropping it rather than blocking on overload."""
    # Unit tests can request a synchronous write so they can inspect the pair
    # immediately. Runtime stays asynchronous so diagnostic I/O minimally
    # perturbs the detector cadence being investigated.
    if getattr(task, "_enemy_presence_debug_sync_save", False):
        try:
            _write_enemy_presence_artifact(output_dir, stem, annotated, inform)
        except Exception as exc:
            _log_enemy_presence_save_error_once(task, exc)
        return

    work_queue = getattr(task, "_enemy_presence_artifact_queue", None)
    if work_queue is None:
        work_queue = queue.Queue(maxsize=_ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE)
        task._enemy_presence_artifact_queue = work_queue
        worker = threading.Thread(
            target=_enemy_presence_artifact_worker,
            args=(work_queue,),
            name="EnemyPresenceArtifactWriter",
            daemon=True,
        )
        task._enemy_presence_artifact_worker = worker
        worker.start()

    try:
        work_queue.put_nowait((task, output_dir, stem, annotated, inform))
    except queue.Full:
        _log_enemy_presence_save_error_once(
            task,
            RuntimeError("敌人存在检测调试帧写入队列已满，已丢弃当前帧"),
        )


def _save_enemy_presence_debug_frame(task, frame, scanned_regions, hits, state: EnemyPresence) -> None:
    """Persist every probe frame and a same-stem JSON sidecar when enabled."""
    if not _save_enemy_presence_frames_enabled(task):
        return
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return

    try:
        output_dir = _resolve_enemy_presence_capture_dir(task)
        if output_dir is None:
            raise RuntimeError("screenshot folder is unavailable")

        sequence = int(getattr(task, "_enemy_presence_debug_frame_seq", 0)) + 1
        task._enemy_presence_debug_frame_seq = sequence
        captured_at = datetime.now().astimezone()
        stem = (
            f"enemy_presence_{captured_at.strftime('%Y%m%d_%H%M%S')}_"
            f"{captured_at.microsecond:06d}_{sequence:06d}_{state.value}"
        )
        annotated = _annotate_enemy_presence_frame(frame, scanned_regions, hits, state)
        inform = _build_enemy_presence_inform(
            stem,
            frame,
            scanned_regions,
            hits,
            state,
            sequence,
            captured_at,
        )
        _queue_enemy_presence_artifact(task, output_dir, stem, annotated, inform)
    except Exception as exc:
        # Diagnostics must never be able to stop combat, including allocation,
        # annotation, worker startup or queue submission failures.
        _log_enemy_presence_save_error_once(task, exc)


def _draw_enemy_presence_debug(task, frame, scanned_regions, hits, state: EnemyPresence) -> None:
    overlay_enabled = _debug_overlay_enabled(task)
    if overlay_enabled:
        draw_boxes = getattr(task, "draw_boxes", None)
        if callable(draw_boxes):
            scan_boxes = []
            for label, rect, hit in scanned_regions:
                x1, y1, x2, y2 = rect
                box = Box(x1, y1, max(1, x2 - x1), max(1, y2 - y1))
                box.name = f"enemy_hp_scan:{label}:{'hit' if hit else 'miss'}:{state.value}"
                box.confidence = 1.0
                scan_boxes.append(box)

            hit_boxes = []
            for label, rect in hits:
                x, y, width, height = rect
                box = Box(x, y, max(1, width), max(1, height))
                box.name = f"enemy_hp_hit:{label}:{width}x{height}"
                box.confidence = 1.0
                hit_boxes.append(box)

            # ok-script 2.0.7b1 can only clear all live boxes at once. Do that
            # once on a hit->miss transition; repeated miss frames must not keep
            # deleting unrelated overlays registered by other features.
            if not hit_boxes and getattr(task, "_enemy_presence_live_hit_drawn", False):
                clear_box = getattr(task, "clear_box", None)
                if callable(clear_box):
                    try:
                        clear_box()
                        task._enemy_presence_live_hit_drawn = False
                    except Exception:
                        pass

            # 2.0.7b1 supports red/green/blue only. Use blue for live scan ROIs;
            # saved evidence remains yellow because it is drawn directly by OpenCV.
            draw_boxes("enemy_presence_scan_regions", scan_boxes, color="blue", debug=True)
            if hit_boxes:
                draw_boxes("enemy_presence_hits", hit_boxes, color="green", debug=True)
                task._enemy_presence_live_hit_drawn = True

    _save_enemy_presence_debug_frame(task, frame, scanned_regions, hits, state)


def _mark_normal_miss(task, index: int) -> None:
    mask = int(getattr(task, "_enemy_hp_checked_mask", 0)) | (1 << index)
    if mask == _FULL_SLICE_MASK:
        task._enemy_hp_checked_mask = 0
        task._enemy_hp_clean_rounds = int(getattr(task, "_enemy_hp_clean_rounds", 0)) + 1
    else:
        task._enemy_hp_checked_mask = mask


def _mark_present(task, normal_slice: int | None = None) -> EnemyPresence:
    task._enemy_hp_last_slice = normal_slice
    task._enemy_hp_checked_mask = 0
    task._enemy_hp_clean_rounds = 0
    if normal_slice is not None:
        task._enemy_hp_scan_cursor = (normal_slice + 1) % ENEMY_NORMAL_HP_SLICES
    return EnemyPresence.PRESENT


def probe_enemy_presence_fast(task) -> EnemyPresence:
    """Return PRESENT fast, amortize the more expensive ABSENT confirmation.

    Order:
    1. Recheck the last normal-enemy slice. Stable fights usually return here.
    2. Check the small top-center boss region.
    3. Check one not-yet-visited normal-enemy slice.

    A hit returns immediately; other regions are not scanned. Normal-region
    misses are accumulated across frames. Two complete clean rounds are needed
    for ABSENT; partial rounds stay UNKNOWN so skill scheduling is never paused
    from an incomplete scan.

    Pink-run width thresholds are derived from each candidate's measured height.
    Widths below 2x height are rejected, widths from 2x to below 10x height must
    also have a nearby aligned horizontal HP/stagger-bar edge, and widths at
    least 10x height pass directly. The height floor and sampling cadence still
    scale with capture height, while distance-driven UI shrinkage naturally
    lowers the corresponding width thresholds.

    With framework ``use_overlay`` enabled, the blue debug layer shows the
    exact ROI(s) scanned by this probe call and the green layer shows the exact
    pink pixel run that passed the detector. The first miss after a live hit
    clears that stale hit before redrawing the current scan regions.

    When ``保存敌人检测调试帧`` is enabled on AutoCombatTask, every valid probe
    frame is saved as a PNG with yellow scan ROI / green hit boxes, plus a
    same-stem ``.inform.json`` file describing every scanned region, hit/miss
    result and exact pixel coordinates. This capture option is independent of
    both project ``debug`` and the live-overlay switch. Under sustained disk
    overload, diagnostic frames are dropped instead of blocking combat or
    allowing the in-memory queue to grow without bound.
    """
    frame = getattr(task, "frame", None)
    scanned_regions = []
    hits = []
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        _draw_enemy_presence_debug(task, frame, scanned_regions, hits, EnemyPresence.UNKNOWN)
        return EnemyPresence.UNKNOWN

    screen_height, screen_width = frame.shape[:2]

    last_slice = getattr(task, "_enemy_hp_last_slice", None)
    if isinstance(last_slice, int) and 0 <= last_slice < ENEMY_NORMAL_HP_SLICES:
        label = f"last_slice_{last_slice}"
        rect, hit = _probe_region(
            frame,
            _normal_slice_region(last_slice),
            screen_width,
            screen_height,
            context_region=ENEMY_NORMAL_HP_REGION,
        )
        scanned_regions.append((label, rect, hit is not None))
        if hit is not None:
            hits.append((label, hit))
            state = _mark_present(task, last_slice)
            _draw_enemy_presence_debug(task, frame, scanned_regions, hits, state)
            return state
        task._enemy_hp_last_slice = None
        _mark_normal_miss(task, last_slice)

    rect, hit = _probe_region(frame, ENEMY_BOSS_HP_REGION, screen_width, screen_height)
    scanned_regions.append(("boss", rect, hit is not None))
    if hit is not None:
        hits.append(("boss", hit))
        state = _mark_present(task)
        _draw_enemy_presence_debug(task, frame, scanned_regions, hits, state)
        return state

    checked_mask = int(getattr(task, "_enemy_hp_checked_mask", 0))
    cursor = int(getattr(task, "_enemy_hp_scan_cursor", 0)) % ENEMY_NORMAL_HP_SLICES
    scan_index = None
    for offset in range(ENEMY_NORMAL_HP_SLICES):
        candidate = (cursor + offset) % ENEMY_NORMAL_HP_SLICES
        if not checked_mask & (1 << candidate):
            scan_index = candidate
            break

    if scan_index is not None:
        task._enemy_hp_scan_cursor = (scan_index + 1) % ENEMY_NORMAL_HP_SLICES
        label = f"normal_slice_{scan_index}"
        rect, hit = _probe_region(
            frame,
            _normal_slice_region(scan_index),
            screen_width,
            screen_height,
            context_region=ENEMY_NORMAL_HP_REGION,
        )
        scanned_regions.append((label, rect, hit is not None))
        if hit is not None:
            hits.append((label, hit))
            state = _mark_present(task, scan_index)
            _draw_enemy_presence_debug(task, frame, scanned_regions, hits, state)
            return state
        _mark_normal_miss(task, scan_index)

    if int(getattr(task, "_enemy_hp_clean_rounds", 0)) >= ENEMY_ABSENT_CONFIRM_ROUNDS:
        state = EnemyPresence.ABSENT
    else:
        state = EnemyPresence.UNKNOWN
    _draw_enemy_presence_debug(task, frame, scanned_regions, hits, state)
    return state
