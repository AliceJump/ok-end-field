"""Expected-SP guided skill-bar probing for timed combat.

The visual probe is deliberately independent from battle_mixin so timed combat
can use one immutable frame for the whole left/right search without changing
legacy skill-bar APIs used by other combat modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

import cv2
import numpy as np

SKILL_BAR_Y_4K = (1958, 1970)
SKILL_BAR_X_4K = ((1604, 1796), (1824, 2013), (2043, 2231))

_WHITE_LOWER = (0, 0, 170)
_WHITE_UPPER = (180, 60, 255)
_YELLOW_LOWER = (20, 80, 140)
_YELLOW_UPPER = (45, 255, 255)
_PARTIAL_COLUMN_RATIO = 0.55
_FULL_ROW_RATIO = 0.90
_MIN_PARTIAL_RATIO = 0.02
_AMBIGUOUS_MASK_RATIO = 0.05


class SkillBarState(str, Enum):
    EMPTY = "empty"
    PARTIAL = "partial"
    FULL = "full"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SkillBarProbe:
    state: SkillBarState
    ratio: float = 0.0


def _has_consecutive_true(values, required=2):
    streak = 0
    for value in values:
        if value:
            streak += 1
            if streak >= required:
                return True
        else:
            streak = 0
    return False


def _left_fill_ratio(mask):
    """Measure a left-to-right contiguous fill while tolerating a one-column gap."""
    if mask is None or mask.size == 0:
        return None
    height, width = mask.shape
    if height <= 0 or width <= 0:
        return None

    filled_cols = 0
    gap = 0
    for x in range(width):
        ratio = np.count_nonzero(mask[:, x]) / height
        if ratio >= _PARTIAL_COLUMN_RATIO:
            filled_cols = x + 1
            gap = 0
        else:
            gap += 1
            if gap >= 2:
                break
    return filled_cols / width


def classify_skill_bar_roi(bar) -> SkillBarProbe:
    """Classify one bar ROI with one HSV conversion for both white and yellow.

    PARTIAL means the active bar is still white. FULL is reserved for the
    post-fill yellow state. A nearly-full white bar therefore stays PARTIAL,
    which prevents 299.x SP from being promoted to a true 300 SP observation.
    """
    if bar is None or getattr(bar, "size", 0) == 0 or getattr(bar, "ndim", 0) != 3:
        return SkillBarProbe(SkillBarState.UNKNOWN)

    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, _WHITE_LOWER, _WHITE_UPPER)
    yellow_mask = cv2.inRange(hsv, _YELLOW_LOWER, _YELLOW_UPPER)

    height, width = yellow_mask.shape
    if height <= 0 or width <= 0:
        return SkillBarProbe(SkillBarState.UNKNOWN)

    yellow_rows = np.count_nonzero(yellow_mask, axis=1) / width >= _FULL_ROW_RATIO
    if _has_consecutive_true(yellow_rows, required=2):
        return SkillBarProbe(SkillBarState.FULL, 1.0)

    partial_ratio = _left_fill_ratio(white_mask)
    if partial_ratio is None:
        return SkillBarProbe(SkillBarState.UNKNOWN)
    if partial_ratio >= _MIN_PARTIAL_RATIO:
        # Keep PARTIAL strictly below 1.0 so a white near-full bar never becomes
        # numerically indistinguishable from a yellow full bar.
        return SkillBarProbe(SkillBarState.PARTIAL, min(float(partial_ratio), 0.999))

    # Sparse colour effects are not useful directional evidence. Refuse to call
    # them EMPTY so the caller can use the conservative legacy detector instead.
    white_coverage = np.count_nonzero(white_mask) / white_mask.size
    yellow_coverage = np.count_nonzero(yellow_mask) / yellow_mask.size
    if white_coverage >= _AMBIGUOUS_MASK_RATIO or yellow_coverage >= _AMBIGUOUS_MASK_RATIO:
        return SkillBarProbe(SkillBarState.UNKNOWN)
    return SkillBarProbe(SkillBarState.EMPTY)


def _partial_sp(index: int, ratio: float) -> float:
    return min(299.9, index * 100.0 + min(max(float(ratio), 0.0), 0.999) * 100.0)


def resolve_expected_skill_bar_sp(
    expected_sp: float | None,
    probe_slot: Callable[[int], SkillBarProbe],
    fallback: Callable[[], float] | None = None,
) -> float:
    """Resolve SP with an expected-value start and at most one probe per slot.

    FULL moves right; EMPTY moves left. All slot observations belong to the same
    source frame. ``checked`` bounds the search to three probes and prevents a
    contradictory FULL/EMPTY pair from bouncing forever.
    """

    def use_fallback():
        if fallback is None:
            return -1.0
        try:
            return float(fallback())
        except Exception:
            return -1.0

    if expected_sp is None or not np.isfinite(expected_sp):
        index = 2
    else:
        expected = min(300.0, max(0.0, float(expected_sp)))
        index = min(2, int(expected // 100.0))

    checked: dict[int, SkillBarProbe] = {}

    while 0 <= index < 3 and len(checked) < 3 and index not in checked:
        probe = probe_slot(index)
        checked[index] = probe

        if probe.state == SkillBarState.PARTIAL:
            return _partial_sp(index, probe.ratio)

        if probe.state == SkillBarState.FULL:
            if index == 2:
                return 300.0
            right = checked.get(index + 1)
            if right is not None and right.state == SkillBarState.EMPTY:
                return float((index + 1) * 100)
            index += 1
            continue

        if probe.state == SkillBarState.EMPTY:
            if index == 0:
                # EMPTY first bar is visually indistinguishable from a missing
                # HUD, so use the old HUD-aware detector for this rare edge.
                return use_fallback()
            left = checked.get(index - 1)
            if left is not None and left.state == SkillBarState.FULL:
                return float(index * 100)
            index -= 1
            continue

        # UNKNOWN carries no directional information.
        break

    # Try the remaining slots once, in the old right-to-left order. This is the
    # conservative escape hatch for UNKNOWN/contradictory observations.
    for candidate in (2, 1, 0):
        if candidate in checked:
            continue
        checked[candidate] = probe_slot(candidate)
        if checked[candidate].state == SkillBarState.PARTIAL:
            # Only accept an immediately useful partial reading if already-known
            # neighbours do not contradict its monotonic bar position.
            left_ok = all(
                checked[i].state in {SkillBarState.FULL, SkillBarState.UNKNOWN}
                for i in range(candidate)
                if i in checked
            )
            right_ok = all(
                checked[i].state in {SkillBarState.EMPTY, SkillBarState.UNKNOWN}
                for i in range(candidate + 1, 3)
                if i in checked
            )
            if left_ok and right_ok:
                return _partial_sp(candidate, checked[candidate].ratio)

    states = [checked.get(i, SkillBarProbe(SkillBarState.UNKNOWN)).state for i in range(3)]
    valid_boundaries = {
        (SkillBarState.EMPTY, SkillBarState.EMPTY, SkillBarState.EMPTY): 0.0,
        (SkillBarState.FULL, SkillBarState.EMPTY, SkillBarState.EMPTY): 100.0,
        (SkillBarState.FULL, SkillBarState.FULL, SkillBarState.EMPTY): 200.0,
        (SkillBarState.FULL, SkillBarState.FULL, SkillBarState.FULL): 300.0,
    }
    boundary = valid_boundaries.get(tuple(states))
    if boundary is None or boundary == 0.0:
        return use_fallback()
    return boundary


def probe_skill_bar_slot(task, frame, index: int) -> SkillBarProbe:
    if index < 0 or index >= len(SKILL_BAR_X_4K):
        return SkillBarProbe(SkillBarState.UNKNOWN)
    x1, x2 = SKILL_BAR_X_4K[index]
    y1, y2 = SKILL_BAR_Y_4K
    box = task.box_of_screen_scaled(3840, 2160, x1 + 3, y1 + 2, x2 - 3, y2 - 2)
    bar = box.crop_frame(frame)
    return classify_skill_bar_roi(bar)


def read_expected_skill_bar_sp(task, expected_sp: float | None, frame=None) -> float:
    """Read SP from one immutable frame, using the legacy detector only as fallback."""
    source_frame = task.frame if frame is None else frame
    if source_frame is None or getattr(source_frame, "size", 0) == 0:
        return -1.0

    return resolve_expected_skill_bar_sp(
        expected_sp,
        lambda index: probe_skill_bar_slot(task, source_frame, index),
        fallback=getattr(task, "get_skill_bar_sp", None),
    )
