"""Fast enemy-presence probe based on Endfield's enemy HP-bar UI color."""

from __future__ import annotations

import cv2
import numpy as np

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

# 1080p reference geometry. Both sampling and candidate geometry scale with the
# actual capture size, so 4K does not scan twice as many rows for the same UI.
ENEMY_HP_SAMPLE_STEP_1080 = 3
ENEMY_HP_MIN_RUN_1080 = 4
ENEMY_HP_MIN_HEIGHT_1080 = 4

_RUN_KERNELS: dict[int, np.ndarray] = {}
_FULL_SLICE_MASK = (1 << ENEMY_NORMAL_HP_SLICES) - 1


def _scaled_px(value: int, screen_width: int, screen_height: int) -> int:
    scale = max(screen_width / 1920.0, screen_height / 1080.0)
    return max(1, int(round(value * scale)))


def _crop_normalized(frame, region):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    px1 = max(0, min(width, int(round(x1 * width))))
    py1 = max(0, min(height, int(round(y1 * height))))
    px2 = max(px1, min(width, int(round(x2 * width))))
    py2 = max(py1, min(height, int(round(y2 * height))))
    return frame[py1:py2, px1:px2]


def _normal_slice(frame, index: int):
    x1, y1, x2, y2 = ENEMY_NORMAL_HP_REGION
    span = (y2 - y1) / ENEMY_NORMAL_HP_SLICES
    sy1 = y1 + span * index
    sy2 = sy1 + span
    return _crop_normalized(frame, (x1, sy1, x2, sy2))


def _run_kernel(width: int):
    kernel = _RUN_KERNELS.get(width)
    if kernel is None:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (width, 1))
        _RUN_KERNELS[width] = kernel
    return kernel


def _max_consecutive_true(values) -> int:
    best = 0
    current = 0
    for value in values:
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _has_enemy_hp_run(roi, screen_width: int, screen_height: int) -> bool:
    """Find one HP-colored rectangle without contours or HSV conversion.

    Only every Nth row participates in the wide scan. A horizontal erosion
    finds candidates with at least the minimum width, then a tiny original-
    resolution column check confirms enough vertical thickness. This rejects
    short pink particles while still accepting the 4x5 residual HP bar present
    in the supplied 1080p screenshots.
    """
    if roi is None or getattr(roi, "size", 0) == 0 or roi.ndim != 3:
        return False

    sample_step = _scaled_px(ENEMY_HP_SAMPLE_STEP_1080, screen_width, screen_height)
    min_run = _scaled_px(ENEMY_HP_MIN_RUN_1080, screen_width, screen_height)
    min_height = _scaled_px(ENEMY_HP_MIN_HEIGHT_1080, screen_width, screen_height)

    sampled = roi[::sample_step]
    mask = cv2.inRange(sampled, ENEMY_HP_BGR_LOWER, ENEMY_HP_BGR_UPPER)
    horizontal = cv2.erode(mask, _run_kernel(min_run))
    points = cv2.findNonZero(horizontal)
    if points is None:
        return False

    candidates = points[:, 0, :]
    _, first_indices = np.unique(candidates[:, 1], return_index=True)
    for index in first_indices[:16]:
        x = int(candidates[index, 0])
        y = int(candidates[index, 1]) * sample_step
        radius = min_height + sample_step
        top = max(0, y - radius)
        bottom = min(roi.shape[0], y + radius + 1)
        column = roi[top:bottom, x]
        matching = np.all(
            (column >= ENEMY_HP_BGR_LOWER) & (column <= ENEMY_HP_BGR_UPPER),
            axis=1,
        )
        if _max_consecutive_true(matching.tolist()) >= min_height:
            return True
    return False


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
    """
    frame = getattr(task, "frame", None)
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return EnemyPresence.UNKNOWN

    screen_height, screen_width = frame.shape[:2]

    last_slice = getattr(task, "_enemy_hp_last_slice", None)
    if isinstance(last_slice, int) and 0 <= last_slice < ENEMY_NORMAL_HP_SLICES:
        if _has_enemy_hp_run(_normal_slice(frame, last_slice), screen_width, screen_height):
            return _mark_present(task, last_slice)
        task._enemy_hp_last_slice = None
        _mark_normal_miss(task, last_slice)

    boss_roi = _crop_normalized(frame, ENEMY_BOSS_HP_REGION)
    if _has_enemy_hp_run(boss_roi, screen_width, screen_height):
        return _mark_present(task)

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
        if _has_enemy_hp_run(_normal_slice(frame, scan_index), screen_width, screen_height):
            return _mark_present(task, scan_index)
        _mark_normal_miss(task, scan_index)

    if int(getattr(task, "_enemy_hp_clean_rounds", 0)) >= ENEMY_ABSENT_CONFIRM_ROUNDS:
        return EnemyPresence.ABSENT
    return EnemyPresence.UNKNOWN
