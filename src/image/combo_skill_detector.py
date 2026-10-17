"""Fixed PC combo portrait slots; identity observations, never cast confirmation.

Frames use OpenCV BGR. The fourth slot is the user-approved equal-pitch
projection of slots two/three, and still needs a four-person live screenshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from src.image.combo_order import ComboOrder

PORTRAITS = Path(__file__).resolve().parents[2] / "assets/data/combo_portraits"
_SLOT_BOXES = ((1134, 450, 1223, 539), (1228, 470, 1294, 536), (1299, 470, 1365, 536))
_SMALL_PITCH = _SLOT_BOXES[2][0] - _SLOT_BOXES[1][0]
_SLOT_BOXES += (tuple(v + (_SMALL_PITCH if i % 2 == 0 else 0) for i, v in enumerate(_SLOT_BOXES[2])),)
_RENDERED_SIZES = (83, 61, 61, 61)
_MIN_SCORE = 0.80
_MIN_MARGIN = 0.08


@dataclass(frozen=True)
class ComboSlotObservation:
    position: int
    box: tuple[int, int, int, int]
    character: str | None = None
    native_id: str | None = None
    team_slot: int | None = None
    score: float | None = None
    margin: float | None = None
    match_scope: str = "team"
    # An unrecognized fixed slot can be empty, obscured or unknown. Keep its
    # position and do not invent absence, canCast, a skill variant or a timer.


@dataclass(frozen=True)
class ComboObservation:
    ready: bool | None
    observed_at: float
    team: tuple[str, ...]
    frame_size: tuple[int, int] | None
    slots: tuple[ComboSlotObservation, ...]
    status: str

    @property
    def head(self) -> ComboSlotObservation | None:
        return self.slots[0] if self.slots and self.slots[0].character else None


class PortraitBank:
    def __init__(self, path: Path = PORTRAITS):
        manifest = json.loads((path / "index.json").read_bytes())
        raw = (path / "portraits.png").read_bytes()
        if (
            manifest["schema_version"] != 1
            or manifest["atlas"] != "portraits.png"
            or manifest["tile_size"] != 154
            or hashlib.sha256(raw).hexdigest() != manifest["atlas_sha256"]
        ):
            raise ValueError("Combo portrait atlas provenance/hash mismatch")
        atlas = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if atlas is None or atlas.ndim != 3 or atlas.shape[2] != 4:
            raise ValueError("Invalid combo portrait atlas")
        self.portraits = {}
        self.characters = {}
        self.aliases = {}
        for row in manifest["portraits"]:
            native_id = row["native_id"]
            x, y, width, height = row["tile"]
            if (
                native_id in self.portraits
                or width != 154
                or height != 154
                or not 0 <= x < x + width <= atlas.shape[1]
                or not 0 <= y < y + height <= atlas.shape[0]
            ):
                raise ValueError("Invalid combo portrait tile")
            tile = atlas[y : y + height, x : x + width].copy()
            tile.flags.writeable = False
            self.portraits[native_id] = tile
            self.characters[native_id] = row["character"]
            for alias in row["aliases"]:
                self.aliases.setdefault(alias, set()).add(native_id)

    def candidates(self, team, variants=None):
        selected = {}
        for slot, alias in enumerate(team, 1):
            available = self.aliases.get(alias, ())
            hint = (variants or {}).get(slot)
            if hint in available:
                available = (hint,)
            for native_id in available:
                name = self.characters[native_id]
                if name is not None:
                    selected.setdefault(native_id, (name, []))[1].append(slot)
        owner_slots = {}
        for name, slots in selected.values():
            owner_slots.setdefault(name, set()).update(slots)
        return {
            native_id: (name, next(iter(owner_slots[name])) if len(owner_slots[name]) == 1 else None)
            for native_id, (name, _slots) in selected.items()
        }


@lru_cache(maxsize=1)
def load_combo_portraits() -> PortraitBank:
    return PortraitBank()


def combo_slot_boxes(width: int, height: int) -> tuple[tuple[int, int, int, int], ...]:
    """Use the ready marker's center anchoring and uniform reference scaling."""
    scale = min(width / 1920, height / 1080)
    return tuple(
        tuple(
            round((v - (960 if i % 2 == 0 else 540)) * scale + (width / 2 if i % 2 == 0 else height / 2))
            for i, v in enumerate(box)
        )
        for box in _SLOT_BOXES
    )


def _frame_size(frame):
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8 or frame.ndim != 3:
        return None
    if frame.shape[2] not in (3, 4) or frame.shape[0] <= 0 or frame.shape[1] <= 0:
        return None
    return frame.shape[1], frame.shape[0]


class ComboSkillDetector:
    def __init__(self, bank: PortraitBank | None = None):
        self._bank = bank
        self._signature = None
        self._templates = {}
        self._slots = {}
        self.order = ComboOrder()
        self._order_team = None
        self._last_full_audit = None

    def reset(self, *, clear_order=False):
        """Invalidate identities after input, team/layout changes or hidden HUD."""
        self._signature = None
        self._templates.clear()
        self._slots.clear()
        self._last_full_audit = None
        if clear_order:
            self.order.clear()
            self._order_team = None

    def _template(self, native_id, size):
        key = (native_id, size)
        if key not in self._templates:
            scaled = cv2.resize(self._bank.portraits[native_id], (size, size), interpolation=cv2.INTER_LINEAR)
            center, half = size // 2, round(size * 0.27)
            face = scaled[center - half : center + half, center - half : center + half]
            mask = cv2.erode((face[:, :, 3] >= 250).astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
            self._templates[key] = (cv2.cvtColor(face[:, :, :3], cv2.COLOR_BGR2GRAY), mask)
        return self._templates[key]

    @staticmethod
    def _face(frame, box, size, scale):
        x, y, x2, y2 = box
        cx, cy = round((x + x2) / 2), round((y + y2) / 2)
        half, shift = round(size * 0.27), max(1, round(3 * scale))
        bounds = (cx - half - shift, cy - half - shift, cx + half + shift, cy + half + shift)
        left, top, right, bottom = bounds
        if (
            size < 16
            or not 0 <= x <= left < right <= x2 <= frame.shape[1]
            or not 0 <= y <= top < bottom <= y2 <= frame.shape[0]
        ):
            return None
        crop = frame[top:bottom, left:right]
        return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY if crop.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)

    def _match_slot(self, face, candidates, size, observation):
        scores = []
        for native_id, (name, team_slot) in candidates.items():
            template, mask = self._template(native_id, size)
            if np.count_nonzero(mask) < 30:
                continue
            correlation = cv2.matchTemplate(face, template, cv2.TM_CCOEFF_NORMED, mask=mask)
            correlation[~np.isfinite(correlation)] = -2
            scores.append((float(cv2.minMaxLoc(correlation)[1]), native_id, name, team_slot))
        if not scores:
            return observation
        scores.sort(key=lambda row: (-row[0], row[1]))
        score, native_id, name, team_slot = scores[0]
        # Both administrator portraits belong to one character; the runner-up
        # margin distinguishes different characters, not gender variants.
        runner = max((row[0] for row in scores if row[2] != name), default=0.0)
        margin = score - runner
        known = score >= _MIN_SCORE and margin >= _MIN_MARGIN
        return ComboSlotObservation(
            observation.position,
            observation.box,
            name if known else None,
            native_id if known else None,
            team_slot if known else None,
            score,
            margin,
        )

    def _observe_slots(self, frame, slots, candidates, scale, *, full_audit):
        results = []
        for index, slot in enumerate(slots):
            rendered_size = round(_RENDERED_SIZES[index] * scale)
            face = self._face(frame, slot.box, rendered_size, scale)
            if face is None:
                results.append(slot)
                continue
            # Exact face-pixel equality only. A new portrait, transparency or
            # scene overlay forces matching; elapsed time never renews identity.
            fingerprint = (face.shape, face.tobytes())
            limited = self.order.after([r.native_id for r in results if r.native_id], candidates)
            scope = candidates if full_audit or index == 0 or not limited else limited
            signature = tuple(sorted(scope))
            cached = self._slots.get(index)
            if cached and cached[0] == (fingerprint, signature):
                result = cached[1]
            else:
                result = self._match_slot(face, scope, rendered_size, slot)
                if len(scope) < len(candidates):
                    # History is a hint. A weak/unknown narrowed result must
                    # retry the whole team, and the head is never narrowed.
                    if result.native_id and result.score >= 0.90 and result.margin >= 0.12:
                        result = replace(result, match_scope="battle_order")
                    else:
                        result = self._match_slot(face, candidates, rendered_size, slot)
                self._slots[index] = ((fingerprint, signature), result)
            results.append(result)
        return tuple(results)

    def observe(self, frame, team, *, ready: bool | None, observed_at: float, variants=None) -> ComboObservation:
        if not math.isfinite(observed_at):
            raise ValueError("Combo observation requires a finite capture time")
        team = tuple(team[:4])
        if team != self._order_team:
            self.reset(clear_order=True)
            self._order_team = team
        size = _frame_size(frame)
        boxes = combo_slot_boxes(*size) if size else ((0, 0, 0, 0),) * 4
        slots = tuple(ComboSlotObservation(i + 1, box) for i, box in enumerate(boxes))
        if ready is not True or size is None:
            self.reset()
            return ComboObservation(ready, observed_at, team, size, slots, "not_ready" if ready is False else "unknown")
        signature = (team, size, tuple(sorted((variants or {}).items())))
        if signature != self._signature:
            self.reset()
            self._signature = signature
        if not any(alias and alias != "?" for alias in team):
            return ComboObservation(ready, observed_at, team, size, slots, "unknown_team")
        if self._bank is None:
            try:
                self._bank = load_combo_portraits()
            except (OSError, ValueError, KeyError, TypeError, cv2.error):
                return ComboObservation(ready, observed_at, team, size, slots, "portraits_unavailable")
        candidates = self._bank.candidates(team, variants)
        if not candidates:
            return ComboObservation(ready, observed_at, team, size, slots, "unknown_team")
        scale = min(size[0] / 1920, size[1] / 1080)
        full_audit = (
            self._last_full_audit is None
            or observed_at < self._last_full_audit
            or observed_at - self._last_full_audit >= 0.5
        )
        results = self._observe_slots(frame, slots, candidates, scale, full_audit=full_audit)
        if full_audit:
            self._last_full_audit = observed_at
        if self.order.learn(results):
            self._slots.clear()
            # Any narrowed slots in the conflicting frame need a full retry
            # before consumers see them or the new order learns from them.
            results = self._observe_slots(frame, slots, candidates, scale, full_audit=True)
            self.order.learn(results)
            self._last_full_audit = observed_at
        return ComboObservation(ready, observed_at, team, size, results, "observed")
