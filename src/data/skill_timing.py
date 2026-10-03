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
    return str(action.get("$type") or "").split(",", 1)[0].rsplit(".", 1)[-1]


def _action_body(action: dict) -> dict:
    body = action.get("$value")
    return body if isinstance(body, dict) else action


def _walk_actions(value):
    if isinstance(value, dict):
        if "$type" in value:
            yield value
        for child in value.values():
            yield from _walk_actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_actions(child)


def _blackboard_numbers(data: dict) -> dict[str, float]:
    values = {}
    for item in data.get("blackboard") or ():
        key = item.get("key")
        value = item.get("valueDouble")
        if key and isinstance(value, (int, float)):
            values[str(key)] = float(value)
    return values


def _resolve_numeric(value: dict | None, blackboard: dict[str, float]) -> float | None:
    if not isinstance(value, dict):
        return None
    if value.get("useBlackboardKey"):
        resolved = blackboard.get(str(value.get("blackboardKey") or ""))
        return resolved if resolved is not None and resolved > 0 else None
    direct = value.get("value")
    return float(direct) if isinstance(direct, (int, float)) else None


def _normal_attack_sp_gain(data: dict, allow_blackboard_hint=False) -> float | None:
    """Read explicit normal-attack ATB gain from one decoded SkillData record."""

    blackboard = _blackboard_numbers(data)
    gains = []
    for action in _walk_actions(data.get("actionGroupData") or {}):
        if _action_type_name(action) != "ObtainCostAction+Data":
            continue
        body = _action_body(action)
        if (
            body.get("costType") != "Atb"
            or body.get("atbSourceType") != "NormalAttack"
            or body.get("atbGainMethod") != "Gain"
        ):
            continue
        value = _resolve_numeric(body.get("costValue"), blackboard)
        if value is not None and value > 0:
            gains.append(value)

    # Some terminal attacks pass their ATB value to a projectile/ability child.
    # The child contains ObtainCostAction while the parent owns the static "atb"
    # blackboard value, so the terminal parent is also valid evidence.
    if allow_blackboard_hint:
        atb = blackboard.get("atb")
        if atb is not None and atb > 0:
            gains.append(atb)
    return max(gains) if gains else None


def _state_span_frames(data: dict) -> int | None:
    """Longest top-level gameplay-buff span for a replaceable state skill."""

    spans = []
    for timeline in data.get("actionGroupData", {}).get("timelineActions") or ():
        sequence = timeline.get("_sequenceActionData")
        if not sequence:
            continue
        start = timeline.get("_startFrame")
        end = timeline.get("_endFrame")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            continue
        for action in sequence.get("actionData") or ():
            if _action_type_name(action) != "CreateBuffAction+Data":
                continue
            if _meaningful_buff_ids(_action_body(action)):
                spans.append(end - start)
    return max(spans) if spans else None


def _set_skill_cd_seconds(data: dict, skill_id: str) -> float | None:
    blackboard = _blackboard_numbers(data)
    values = []
    for action in _walk_actions(data.get("actionGroupData") or {}):
        if _action_type_name(action) != "SetSkillCdAtOnce+Data":
            continue
        body = _action_body(action)
        if body.get("skillId") != skill_id:
            continue
        value = _resolve_numeric(body.get("value"), blackboard)
        if value is not None and value >= 0:
            values.append(value)
    return max(values) if values else None


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


@dataclass(frozen=True)
class BattleStateSpec:
    base_skill_id: str
    end_skill_id: str
    duration: float
    end_cooldown: float | None
    evidence: tuple[str, ...]


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
        self._normal_attack_sp_gains = {}
        self._global_normal_attack_sp_gain = None
        self._state_specs = {}

    def _entry_skill_id(self, cid: str, kind: str) -> str | None:
        """Resolve the button's native entry skill without character-name exceptions.

        Most characters use *_normal_skill. Stateful characters may expose
        the same battle button as *_normalskill_1 or
        *_normal_skill_floating_start; those are real native entry skills
        and must not disappear from scheduling merely because the id differs.
        """
        exact = f"{cid}_{_SUFFIX[kind]}"
        if exact in self.index["skills"]:
            return exact

        if kind == "link":
            # Multi-stage link buttons may expose numbered native entry ids
            # instead of the usual *_combo_skill. Rossi is the current packaged
            # example: combo_1_skill -> combo_2_skill -> combo_3_skill.
            numbered_links = []
            prefix = f"{cid}_combo_"
            suffix = "_skill"
            for skill_id in self.index["skills"]:
                if not skill_id.startswith(prefix) or not skill_id.endswith(suffix):
                    continue
                stage = skill_id[len(prefix):-len(suffix)]
                if stage.isdigit():
                    numbered_links.append((int(stage), skill_id))
            if numbered_links:
                return min(numbered_links)[1]
            return None

        if kind != "battle":
            return None

        numbered = []
        prefix = f"{cid}_normalskill_"
        for skill_id in self.index["skills"]:
            if not skill_id.startswith(prefix):
                continue
            suffix = skill_id[len(prefix):]
            if suffix.isdigit():
                numbered.append((int(suffix), skill_id))
        if numbered:
            return min(numbered)[1]

        floating = f"{cid}_normal_skill_floating_start"
        return floating if floating in self.index["skills"] else None

    def _profile_for_skill_id(self, skill_id: str) -> SkillTiming:
        fps = self.index["frames_per_second"]
        data = self.index["skills"][skill_id]
        casts = data["level_patches"] or [
            {
                "coolDown": data["raw_cast_data"]["cooldownTime"],
                **data["raw_cast_data"]["costData"],
            }
        ]
        costs = [
            math.ceil(cast["costValue"] / 100)
            if cast["costType"] == 1
            else 0 if cast["costValue"] == 0 else None
            for cast in casts
        ]
        effect_frame = self.effect_start_frame(skill_id)
        return SkillTiming(
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

    def profiles(self, character: str, kind: str) -> tuple[SkillTiming, ...]:
        """Resolve native entry skills; keep both administrator portrait variants."""
        profiles = []
        for cid, row in self.index["characters"].items():
            if character not in (row["name"], row["key"], row["english_name"], cid):
                continue
            skill_id = self._entry_skill_id(cid, kind)
            if skill_id is not None:
                profiles.append(self._profile_for_skill_id(skill_id))
        return tuple(profiles)

    def battle_phase_profiles(self, character: str) -> tuple[SkillTiming, ...]:
        """Return explicit numbered battle-button phases when native data exposes them."""
        phases = []
        for cid in self._character_ids(character):
            prefix = f"{cid}_normalskill_"
            numbered = []
            for skill_id in self.index["skills"]:
                if not skill_id.startswith(prefix):
                    continue
                suffix = skill_id[len(prefix):]
                if suffix.isdigit():
                    numbered.append((int(suffix), skill_id))
            if numbered:
                phases.extend(self._profile_for_skill_id(skill_id) for _, skill_id in sorted(numbered))
        return tuple(phases)

    def _character_ids(self, character: str) -> tuple[str, ...]:
        return tuple(
            cid
            for cid, row in self.index["characters"].items()
            if character in (row["name"], row["key"], row["english_name"], cid)
        )

    def _terminal_normal_attack_id(self, cid: str) -> str | None:
        candidates = []
        prefix = cid + "_attack"
        for skill_id in self.index["skills"]:
            if not skill_id.startswith(prefix):
                continue
            suffix = skill_id[len(prefix) :]
            if suffix.isdigit():
                candidates.append((int(suffix), skill_id))
        return max(candidates, default=(None, None))[1]

    def normal_attack_sp_gain(self, character: str) -> float | None:
        """Return the combo-finisher ATB/SP refund encoded in packaged records."""

        if character in self._normal_attack_sp_gains:
            return self._normal_attack_sp_gains[character]

        gains = []
        for cid in self._character_ids(character):
            terminal = self._terminal_normal_attack_id(cid)
            if terminal is None:
                continue
            for record_id, record in self._all_records().items():
                if record_id != terminal and not record_id.startswith(terminal + "_"):
                    continue
                value = _normal_attack_sp_gain(
                    record["data"],
                    allow_blackboard_hint=record_id == terminal,
                )
                if value is not None:
                    gains.append(value)

        result = max(gains) if gains else None
        self._normal_attack_sp_gains[character] = result
        return result

    def global_normal_attack_sp_gain(self) -> float | None:
        if self._global_normal_attack_sp_gain is None:
            gains = [
                self.normal_attack_sp_gain(cid)
                for cid in self.index["characters"]
            ]
            known = [gain for gain in gains if gain is not None]
            self._global_normal_attack_sp_gain = max(known) if known else -1.0
        return None if self._global_normal_attack_sp_gain < 0 else self._global_normal_attack_sp_gain

    def team_normal_attack_sp_gains(self, team) -> dict[str, float | None]:
        return {name: self.normal_attack_sp_gain(name) for name in team}

    def state_skill(self, character: str, kind: str = "battle") -> BattleStateSpec | None:
        """Detect a skill whose active state replaces the battle button with *_end."""

        if kind not in ("battle", "ult"):
            return None
        cache_key = (character, kind)
        if cache_key in self._state_specs:
            return self._state_specs[cache_key]

        specs = []
        fps = self.index["frames_per_second"]
        for cid in self._character_ids(character):
            battle_id = f"{cid}_normal_skill"
            source_id = battle_id if kind == "battle" else f"{cid}_ultimate_skill"
            end_id = f"{cid}_normal_skill_end"
            source = self.index["skills"].get(source_id)
            end = self.index["skills"].get(end_id)
            if source is None or end is None:
                continue

            allowed = any(
                end_id in (window.get("allowed_skill_ids") or ())
                for window in source.get("allow_next_windows") or ()
            )
            casts = end["level_patches"] or [
                {
                    "coolDown": end["raw_cast_data"]["cooldownTime"],
                    **end["raw_cast_data"]["costData"],
                }
            ]
            zero_cost = casts and all(cast.get("costValue") == 0 for cast in casts)
            if not allowed or not zero_cost:
                continue

            source_record = self.record(source_id)["data"]
            span = _state_span_frames(source_record)
            duration = (
                span / fps
                if span is not None
                else max(0, source["exclusive_frame"]) / fps
            )
            battle_record = self.record(battle_id)["data"]
            end_cd = _set_skill_cd_seconds(battle_record, battle_id)
            specs.append(
                BattleStateSpec(
                    base_skill_id=source_id,
                    end_skill_id=end_id,
                    duration=duration,
                    end_cooldown=end_cd,
                    evidence=(
                        f"allow_next:{end_id}",
                        "end_cost=0",
                        f"state_span={duration:.2f}s",
                        "manual_end_cd=" + ("unknown" if end_cd is None else f"{end_cd:g}s"),
                    ),
                )
            )

        result = specs[0] if len(specs) == 1 else None
        self._state_specs[cache_key] = result
        return result

    def battle_state(self, character: str) -> BattleStateSpec | None:
        return self.state_skill(character, "battle")

    def ultimate_state(self, character: str) -> BattleStateSpec | None:
        return self.state_skill(character, "ult")

    def effect_start_frame(self, skill_id: str) -> int | None:
        if skill_id not in self._effect_start_frames:
            self._effect_start_frames[skill_id] = _effect_start_frame(self.record(skill_id)["data"])
        return self._effect_start_frames[skill_id]

    def _all_records(self) -> dict:
        if self._records is None:
            compressed = (self.path / "records.json.gz").read_bytes()
            if hashlib.sha256(compressed).hexdigest() != self.index["records_sha256"]:
                raise ValueError("Timing records hash mismatch")
            self._records = json.loads(gzip.decompress(compressed))
        return self._records

    def record(self, skill_id: str) -> dict:
        """Full lossless records are loaded only for inspection, outside the hot loop."""
        return self._all_records()[skill_id]


@lru_cache(maxsize=1)
def load_skill_timings() -> SkillTimingStore:
    return SkillTimingStore()
