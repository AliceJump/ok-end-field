"""Native skill timelines. Frame boundaries are scheduling hints, not hit proofs."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/skill_timings/20261002"
_SUFFIX = {"battle": "normal_skill", "link": "combo_skill", "ult": "ultimate_skill"}
_MIN_HARD_LOCK_SECONDS = 0.3
_ACTIONABLE_GRACE_SECONDS = 0.15
_EFFECT_HANDOFF_GRACE_SECONDS = 0.05

_EFFECT_ACTION_TYPES = {
    "DamageAction+DamageActionData",
    "SpellInfliction+Data",
    "LaunchProjectile+Data",
    "SpawnAbilityEntity+Data",
    "AuraAction+Data",
    "HealAction+Data",
}
_GAMEPLAY_BUFF_HINTS = (
    "tag",
    "damage",
    "heal",
    "atk",
    "attack",
    "spell",
    "music",
    "aura",
    "shield",
    "protect",
    "enhance",
    "debuff",
    "attach",
    "stack",
    "mark",
    "trigger",
    "regen",
    "recovery",
    "crit",
    "speed",
    "charge",
)
_COSMETIC_BUFF_HINTS = (
    "showhide",
    "_vfx",
    "_ui",
    "uishow",
    "weaponvisible",
    "audio",
    "camera",
    "damage_immune",
    "invinc",
    "superarmor",
    "hitstop",
)


def _action_type_name(action: dict) -> str:
    return str(action.get("$type") or "").rsplit(".", 1)[-1]


def _meaningful_buff_ids(body: dict) -> tuple[str, ...]:
    ids = []
    for key in ("buffs", "buffInput"):
        for item in body.get(key) or ():
            buff_id = str(item.get("buffId") or "")
            lowered = buff_id.lower()
            if not buff_id or any(marker in lowered for marker in _COSMETIC_BUFF_HINTS):
                continue
            if any(marker in lowered for marker in _GAMEPLAY_BUFF_HINTS):
                ids.append(buff_id)
    return tuple(ids)


def _effect_start_frame(data: dict) -> int | None:
    """Earliest unconditional top-level gameplay effect in a decoded SkillData.

    Nested If/Else/EventListener actions are deliberately ignored: they may not
    happen on every cast. This is a cross-actor handoff hint, not hit proof.
    """

    primary = []
    secondary = []
    for timeline in data.get("actionGroupData", {}).get("timelineActions") or ():
        sequence = timeline.get("_sequenceActionData")
        if (
            not sequence
            or sequence.get("onlyExecuteWhenSourceIsMainChar")
            or sequence.get("onlyExecuteWhenSourceIsGuard")
        ):
            continue
        frame = timeline.get("_startFrame")
        if not isinstance(frame, int) or frame < 0:
            continue
        for action in sequence.get("actionData") or ():
            action_type = _action_type_name(action)
            body = action.get("$value") or {}
            if action_type in _EFFECT_ACTION_TYPES:
                primary.append(frame)
            elif action_type == "CreateBuffAction+Data" and _meaningful_buff_ids(body):
                primary.append(frame)
            elif action_type == "ChangeSkillAction+Data":
                secondary.append(frame)
    if primary:
        return min(primary)
    if secondary:
        return min(secondary)
    return None


@dataclass(frozen=True)
class SkillTiming:
    skill_id: str
    duration: float
    exclusive: float
    cooldown: float
    skill_points: int | None
    allow_next: tuple[tuple[float, float, tuple[str, ...]], ...]
    sp_cost: float | None = None
    effect_start: float | None = None

    @property
    def hard_lock(self) -> float:
        """Conservative same-actor fallback from the native exclusive timeline."""
        return max(_MIN_HARD_LOCK_SECONDS, self.exclusive)

    @property
    def actionable(self) -> float:
        """Fallback handoff point when no candidate-specific allow-next window matches.

        duration is the complete authored timeline, not a generic input lock.
        Keep a small grace after exclusive to avoid scheduling exactly on a
        boundary while still allowing the next action far earlier than full duration.
        """
        return max(
            self.hard_lock,
            min(self.duration, self.hard_lock + _ACTIONABLE_GRACE_SECONDS),
        )

    @property
    def handoff(self) -> float:
        """Cross-actor handoff after the first verified gameplay effect starts.

        Unknown effect timing falls back to the previous conservative actionable
        boundary. The small grace avoids scheduling exactly on a native frame.
        """
        if self.effect_start is None:
            return self.actionable
        return max(_MIN_HARD_LOCK_SECONDS, self.effect_start + _EFFECT_HANDOFF_GRACE_SECONDS)

    def committed(self, elapsed: float) -> bool:
        return elapsed >= self.handoff

    def allows(self, elapsed: float, candidates: tuple[SkillTiming, ...]) -> bool:
        """Same-actor continuation rule.

        allow-next is permission, not an instruction to interrupt. It can only
        shorten the same-actor fallback after this skill has already committed.
        """
        if not self.committed(elapsed):
            return False
        if candidates and any(
            start <= elapsed <= end and all(candidate.skill_id in allowed for candidate in candidates)
            for start, end, allowed in self.allow_next
        ):
            return True
        return elapsed >= max(self.handoff, self.actionable)


class SkillTimingStore:
    def __init__(self, path: Path = SNAPSHOT):
        self.path = path
        self.index = json.loads((path / "index.json").read_text(encoding="utf-8"))
        if self.index["schema_version"] != 1 or self.index["frames_per_second"] != 30:
            raise ValueError("Unsupported timing snapshot")
        verification = self.index["verification"]
        if not verification["all_reached_eof"] or verification["binary_roundtrip"]["failures"]:
            raise ValueError("Unverified timing snapshot")
        self._records = None
        self._effect_start_frames = {}

    def profiles(self, character: str, kind: str) -> tuple[SkillTiming, ...]:
        """Keep both administrator variants; never guess gender from a shared portrait."""
        profiles = []
        fps = self.index["frames_per_second"]
        for cid, row in self.index["characters"].items():
            if character not in (row["name"], row["key"], row["english_name"], cid):
                continue
            skill_id = f"{cid}_{_SUFFIX[kind]}"
            data = self.index["skills"].get(skill_id)
            if data is None:
                continue
            casts = data["level_patches"] or [
                {
                    "coolDown": data["raw_cast_data"]["cooldownTime"],
                    **data["raw_cast_data"]["costData"],
                }
            ]
            # One complete HUD skill bar corresponds to 100 SP. Unknown cost types
            # are deliberately not translated into skill-point readiness.
            costs = [
                math.ceil(cast["costValue"] / 100) if cast["costType"] == 1 else 0 if cast["costValue"] == 0 else None
                for cast in casts
            ]
            effect_frame = self.effect_start_frame(skill_id)
            profiles.append(
                SkillTiming(
                    skill_id=skill_id,
                    duration=max(0, data["duration_frame"]) / fps,
                    exclusive=max(0, data["exclusive_frame"]) / fps,
                    cooldown=max(cast["coolDown"] for cast in casts),
                    skill_points=None if None in costs else max(costs),
                    sp_cost=None if None in costs else max(cast["costValue"] for cast in casts),
                    allow_next=tuple(
                        (
                            window["start_frame"] / fps,
                            window["end_frame"] / fps,
                            tuple(window["allowed_skill_ids"] or ()),
                        )
                        for window in data["allow_next_windows"]
                    ),
                    effect_start=None if effect_frame is None else effect_frame / fps,
                )
            )
        return tuple(profiles)

    def effect_start_frame(self, skill_id: str) -> int | None:
        if skill_id not in self._effect_start_frames:
            self._effect_start_frames[skill_id] = _effect_start_frame(self.record(skill_id)["data"])
        return self._effect_start_frames[skill_id]

    def record(self, skill_id: str) -> dict:
        """Full lossless records are loaded only for inspection, outside the hot loop."""
        if self._records is None:
            compressed = (self.path / "records.json.gz").read_bytes()
            if hashlib.sha256(compressed).hexdigest() != self.index["records_sha256"]:
                raise ValueError("Timing records hash mismatch")
            self._records = json.loads(gzip.decompress(compressed))
        return self._records[skill_id]


@lru_cache(maxsize=1)
def load_skill_timings() -> SkillTimingStore:
    return SkillTimingStore()
