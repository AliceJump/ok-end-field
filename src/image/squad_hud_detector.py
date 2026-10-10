"""Observe fixed left-aligned squad slots without using skill keys or OCR.

The death marker establishes occupancy, not identity. Health is measured inside
the fixed bar container; the length/centre of the fill never positions a slot.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from weakref import ReferenceType, ref

import cv2
import numpy as np

_DEATH_TEMPLATE = Path(__file__).resolve().parents[2] / "assets/data/squad/dead_marker.png"
DEATH_THRESHOLD = 0.68
SLOT_PITCH = 350 / 3
_MIN_GAP = 0.03
_MAX_GAP = 2.5


@dataclass(frozen=True)
class SquadSlotObservation:
    occupied: bool
    death_visible: bool
    death_score: float
    hp_fraction: float | None = None
    # Coordinates in the canonical 1080p HUD crop, useful for diagnostics.
    bar_start: int | None = None
    uncertain: bool = False


@dataclass(frozen=True)
class SquadHudObservation:
    slots: tuple[SquadSlotObservation, ...]

    @property
    def present(self) -> bool:
        return any(slot.occupied for slot in self.slots)

    @property
    def count_candidate(self) -> int | None:
        occupied = [index for index, slot in enumerate(self.slots) if slot.occupied]
        if not occupied:
            return None
        count = occupied[-1] + 1
        # An interior hole is unknown, never a smaller formation.
        return (
            count
            if (
                all(slot.occupied for slot in self.slots[:count])
                and not any(slot.uncertain for slot in self.slots[count:])
            )
            else None
        )


@lru_cache(maxsize=1)
def _death_template():
    template = cv2.imread(str(_DEATH_TEMPLATE), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(_DEATH_TEMPLATE)
    return template


def _bar_endcaps(crop, slot):
    """Find the two small neutral endcaps, including an empty health fill."""
    offset = round(slot * SLOT_PITCH)
    strip = crop[78:85, offset + 15 : offset + 154]
    values = strip.astype(np.int16)
    # 1440p resampling spreads a two-pixel white cap over adjacent scanlines.
    neutral = (values.min(axis=2) >= 120) & (np.ptp(values, axis=2) <= 35)
    candidates = []
    for row in neutral:
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        runs = [(start, end) for start, end in zip(starts, ends, strict=True) if 1 <= end - start <= 4]
        for left_start, left_end in runs:
            left = (left_start + left_end) / 2 + offset + 15
            if not offset + 20 <= left <= offset + 58:
                continue
            for right_start, right_end in runs:
                right = (right_start + right_end) / 2 + offset + 15
                if abs(right - left - 95.5) <= 2.5:
                    candidates.append(left)
    # Both endcaps must persist over at least two scanlines.
    for left in candidates:
        if sum(abs(other - left) <= 1 for other in candidates) >= 2:
            return round(left + 5.5)
    return None


def _read_hp_fraction(crop, start):
    """Estimate coloured primary fill; absence/occlusion is unknown, not death."""
    bar = crop[78:84, start : start + 84]
    if bar.shape[:2] != (6, 84):
        return None
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    colour = ((hue >= 85) & (hue <= 115)) | (hue <= 12) | (hue >= 170) | ((hue >= 35) & (hue <= 85))
    filled = np.mean(colour & (saturation >= 55) & (value >= 90), axis=0) >= 0.65
    if not np.any(filled[:2]):
        return None
    end = 0
    gaps = 0
    for index, hit in enumerate(filled):
        if hit:
            end, gaps = index + 1, 0
        else:
            gaps += 1
            if gaps >= 2:
                break
    # Detached colour is likely a flash/VFX rather than a readable bar.
    if np.count_nonzero(filled[end + 2 :]) > 4:
        return None
    return min(1.0, end / 84)


def observe_squad_hud(frame, *, read_health=False) -> SquadHudObservation | None:
    """Return four physical slots, or None for an invalid/unavailable frame."""
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return None
    height, width = frame.shape[:2]
    scale = height / 1080
    right = round(520 * scale)
    if height < 540 or width < right or frame.shape[2] != 3:
        return None
    # Resize only the small HUD crop, never the whole 2K/4K frame.
    crop = frame[round(915 * scale) : round(1007 * scale), :right]
    crop = cv2.resize(crop, (520, 92), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop[:63], cv2.COLOR_BGR2GRAY)
    template = _death_template()
    slots = []
    for slot in range(4):
        left = round(48 + slot * SLOT_PITCH)
        score = float(cv2.minMaxLoc(cv2.matchTemplate(gray[:, left : left + 76], template, cv2.TM_CCOEFF_NORMED))[1])
        death = score >= DEATH_THRESHOLD
        start = _bar_endcaps(crop, slot)
        # Do not accept an incidental pair of menu pixels as a live squad slot.
        # Reading the narrow primary fill also validates the container.
        fill = None if death or start is None else _read_hp_fraction(crop, start)
        hp = (0.0 if death else fill) if read_health else None
        occupied = death or fill is not None
        band = crop[78:84, round(32 + slot * SLOT_PITCH) : round(145 + slot * SLOT_PITCH)]
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        colour_ratio = np.mean((hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 45))
        below = crop[86:92, round(32 + slot * SLOT_PITCH) : round(145 + slot * SLOT_PITCH)]
        below = cv2.cvtColor(below, cv2.COLOR_BGR2HSV)
        background_ratio = np.mean((below[:, :, 1] >= 45) & (below[:, :, 2] >= 45))
        possible_fill = bool(start is not None or (colour_ratio > 0.1 and background_ratio < colour_ratio * 0.5))
        slots.append(SquadSlotObservation(occupied, death, score, hp, start, not occupied and possible_fill))
    return SquadHudObservation(tuple(slots))


@dataclass
class SquadHudState:
    """Confirm count/death/recovery across distinct frames, preserving slot IDs."""

    member_count: int = 0
    dead_slots: set[int] = field(default_factory=set)
    _candidate: int | None = None
    _count_streak: int = 0
    _death_streaks: dict[int, int] = field(default_factory=dict)
    _recovery_streaks: dict[int, int] = field(default_factory=dict)
    _seen_at: float | None = None
    _last_frame: ReferenceType | None = None
    _count_seen_at: float | None = None
    _last_count_frame: ReferenceType | None = None

    def update(self, observation, now, frame, names=(), *, confirm_count=True):
        if confirm_count and (self._last_count_frame is None or frame is not self._last_count_frame()):
            gap = None if self._count_seen_at is None else now - self._count_seen_at
            if gap is None or gap >= _MIN_GAP:
                candidate = None if observation is None else observation.count_candidate
                continued = gap is not None and gap <= _MAX_GAP and candidate == self._candidate
                self._count_streak = self._count_streak + 1 if candidate and continued else 1
                self._candidate = candidate
                self._last_count_frame = ref(frame) if frame is not None else None
                self._count_seen_at = now
                if not self.member_count and candidate and self._count_streak >= 2:
                    self.member_count = candidate
        if self._last_frame is not None and frame is self._last_frame():
            return
        gap = None if self._seen_at is None else now - self._seen_at
        if gap is not None and gap < _MIN_GAP:
            return
        if gap is None or gap > _MAX_GAP:
            self._death_streaks.clear()
            self._recovery_streaks.clear()
        self._last_frame = ref(frame) if frame is not None else None
        self._seen_at = now
        if observation is None:
            self._death_streaks.clear()
            self._recovery_streaks.clear()
            return
        for index, slot in enumerate(observation.slots):
            if slot.death_visible:
                self._recovery_streaks.pop(index, None)
                streak = self._death_streaks.get(index, 0) + 1
                self._death_streaks[index] = streak
                if streak >= 2:
                    self.dead_slots.add(index)
            else:
                self._death_streaks.pop(index, None)
                known = index < len(names) and names[index] != "?"
                # The cheap HUD probe already verifies a primary fill/portrait.
                # It can run before the full identity scan on this same frame.
                if known or slot.occupied:
                    streak = self._recovery_streaks.get(index, 0) + 1
                    self._recovery_streaks[index] = streak
                    if streak >= 2:
                        self.dead_slots.discard(index)
                else:
                    self._recovery_streaks.pop(index, None)
