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

# 1080p reference geometry. Sampling and candidate geometry scale with capture
# height, so 4K doubles the UI geometry while ultrawide aspect ratios do not
# incorrectly enlarge the expected HP-bar thickness/length.
ENEMY_HP_SAMPLE_STEP_1080 = 3
ENEMY_HP_MIN_RUN_1080 = 10
ENEMY_HP_DIRECT_RUN_1080 = 40
ENEMY_HP_MIN_HEIGHT_1080 = 4

# Short pink runs are accepted only when a nearby horizontal UI edge confirms
# that the pixels belong to an HP/stagger-bar structure instead of combat VFX.
# Long runs skip this second stage: the supplied 1080p evidence contains false
# fire/VFX runs up to 31 px, while 40+ px hits are unambiguous HP bars.
ENEMY_HP_CONTEXT_SEARCH_DOWN_1080 = 12
ENEMY_HP_CONTEXT_X_PAD_1080 = 80
ENEMY_HP_CONTEXT_MIN_EDGE_RUN_1080 = 18
ENEMY_HP_CONTEXT_MIN_OVERLAP_1080 = 3
ENEMY_HP_CONTEXT_EDGE_DELTA = 25

_RUN_KERNELS: dict[int, np.ndarray] = {}
_FULL_SLICE_MASK = (1 << ENEMY_NORMAL_HP_SLICES) - 1


def reset_enemy_presence_probe(task) -> None:
    task._enemy_hp_last_slice = None
    task._enemy_hp_checked_mask = 0
    task._enemy_hp_clean_rounds = 0
    task._enemy_hp_scan_cursor = 0


def _scaled_px(value: int, _screen_width: int, screen_height: int) -> int:
    scale = screen_height / 1080.0
    return max(1, round(value * scale))


def _crop_normalized(frame, region):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = region
    px1 = max(0, min(width, round(x1 * width)))
    py1 = max(0, min(height, round(y1 * height)))
    px2 = max(px1, min(width, round(x2 * width)))
    py2 = max(py1, min(height, round(y2 * height)))
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
    for start, end in zip(starts, ends, strict=False):
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
    direct_run = _scaled_px(ENEMY_HP_DIRECT_RUN_1080, screen_width, screen_height)
    if width >= direct_run:
        return True

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


def _find_enemy_hp_run(roi, screen_width: int, screen_height: int):
    """Return the local pixel box that passes HP geometry + context checks."""
    if roi is None or getattr(roi, "size", 0) == 0 or roi.ndim != 3:
        return None

    sample_step = _scaled_px(ENEMY_HP_SAMPLE_STEP_1080, screen_width, screen_height)
    min_run = _scaled_px(ENEMY_HP_MIN_RUN_1080, screen_width, screen_height)
    min_height = _scaled_px(ENEMY_HP_MIN_HEIGHT_1080, screen_width, screen_height)

    sampled = roi[::sample_step]
    mask = cv2.inRange(sampled, ENEMY_HP_BGR_LOWER, ENEMY_HP_BGR_UPPER)
    horizontal = cv2.erode(mask, _run_kernel(min_run))
    points = cv2.findNonZero(horizontal)
    if points is None:
        return None

    candidates = points[:, 0, :]
    candidate_points = []
    # Keep the existing sampled-row cap, but inspect every contiguous horizontal
    # segment on each row. A false pink VFX run must not hide a real HP bar that
    # starts farther to the right on the same sampled row.
    for sampled_y in np.unique(candidates[:, 1])[:16]:
        row_xs = np.sort(candidates[candidates[:, 1] == sampled_y, 0])
        segment_starts = row_xs[np.r_[True, np.diff(row_xs) > 1]]
        candidate_points.extend((int(x), int(sampled_y)) for x in segment_starts)

    for x, sampled_y in candidate_points:
        y = sampled_y * sample_step
        radius = min_height + sample_step
        top = max(0, y - radius)
        bottom = min(roi.shape[0], y + radius + 1)
        column = roi[top:bottom, x]
        matching = np.all(
            (column >= ENEMY_HP_BGR_LOWER) & (column <= ENEMY_HP_BGR_UPPER),
            axis=1,
        ).tolist()

        # The vertical evidence must belong to the very same horizontal
        # candidate row. Do not combine a one-pixel horizontal candidate with a
        # separate nearby vertical pink segment and call the union an exact hit.
        run_start, run_end, run_height = _true_run_containing(matching, y - top)
        if run_height < min_height:
            continue

        sampled_row = mask[sampled_y] != 0
        left = x
        while left > 0 and sampled_row[left - 1]:
            left -= 1
        right = x
        while right + 1 < sampled_row.shape[0] and sampled_row[right + 1]:
            right += 1

        hit_top = int(top + run_start)
        hit_width = int(right - left + 1)
        hit_height = int(run_end - run_start + 1)
        if not _has_enemy_hp_context(
            roi,
            int(left),
            hit_top,
            hit_width,
            hit_height,
            screen_width,
            screen_height,
        ):
            # A short pink VFX run is not enough. Keep looking in this ROI for
            # another candidate that has the surrounding HP/stagger-bar shape.
            continue

        return int(left), hit_top, hit_width, hit_height
    return None


def _has_enemy_hp_run(roi, screen_width: int, screen_height: int) -> bool:
    """Find one HP-colored rectangle without contours or full-frame HSV."""
    return _find_enemy_hp_run(roi, screen_width, screen_height) is not None


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

    Pink runs at least 40 px long at 1080p are accepted directly. Shorter runs
    down to the 10 px minimum must also have a nearby aligned horizontal UI edge
    from the HP/stagger-bar structure. All geometry thresholds scale with screen
    height, so the direct-pass threshold is 80 px at 4K.
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
