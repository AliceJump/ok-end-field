"""Bind ranked/native damage modifiers without interpreting legacy value=1 markers."""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from enum import Enum

from src.data.immutable_combat_value import ImmutableCombatValue


class DamageBucket(str, Enum):
    ATTACK = "attack"
    DAMAGE_BONUS = "damage_bonus"
    AMPLIFICATION = "amplification"
    VULNERABILITY = "vulnerability"
    DAMAGE_TAKEN = "damage_taken"
    CRIT_RATE = "crit_rate"
    CRIT_DAMAGE = "crit_damage"


@dataclass(frozen=True)
class MagnitudeTerm(ImmutableCombatValue):
    input: str
    coefficient: float
    cap: float | None = None


@dataclass(frozen=True)
class ModifierMagnitude(ImmutableCombatValue):
    base: float = 0.0
    terms: tuple[MagnitudeTerm, ...] = ()
    by_count: tuple[tuple[int, float], ...] = ()
    count_input: str | None = None
    accumulation: str = "linear"

    def evaluate(self, inputs: dict[str, float]) -> float | None:
        if self.accumulation == "native_float32_repeated_add":
            if len(self.terms) != 1 or self.count_input is not None or self.by_count or self.terms[0].cap is not None:
                return None
            term = self.terms[0]
            count = inputs.get(term.input)
            # This is a consumer work budget, not a gameplay stack cap. Larger
            # inputs stay unknown; never clamp them or invent a marker count.
            if count is None or not math.isfinite(count) or count != int(count) or not 0 <= count <= 100000:
                return None
            try:
                value = struct.unpack("<f", struct.pack("<f", self.base))[0]
                increment = struct.unpack("<f", struct.pack("<f", term.coefficient))[0]
                for _ in range(int(count)):
                    value = struct.unpack("<f", struct.pack("<f", value + increment))[0]
            except (OverflowError, struct.error):
                return None
            return value if math.isfinite(value) else None
        if self.accumulation != "linear":
            return None
        value = self.base
        if self.count_input is not None:
            count = inputs.get(self.count_input)
            if count is None or not math.isfinite(count) or count != int(count):
                return None
            value += dict(self.by_count).get(int(count), float("nan"))
        for term in self.terms:
            amount = inputs.get(term.input)
            if amount is None or not math.isfinite(amount):
                return None
            contribution = amount * term.coefficient
            value += min(contribution, term.cap) if term.cap is not None else contribution
        return value if math.isfinite(value) else None


@dataclass(frozen=True)
class DamageModifierSpec(ImmutableCombatValue):
    key: str
    bucket: DamageBucket
    elements: tuple[str, ...]
    recipient: str
    magnitude: ModifierMagnitude
    trigger: str
    damage_tags: tuple[str, ...] = ()
    permanent: bool = False
    condition_inputs: tuple[str, ...] = ()
    duration: float | None = None
    duration_input: str | None = None
    evaluation: str = "application"
    max_stacks: int = 1
    stack_policy: str = "replace"
    unresolved: str | None = None
    sources: tuple[str, ...] = ()
    lifetime_scope: str = "timed"


def _number(value, *, percent: bool) -> float:
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip()
        pattern = r"([+-]?\d+(?:\.\d+)?)%" if percent else r"([+-]?\d+(?:\.\d+)?)(?:秒)?"
        match = re.fullmatch(pattern, text)
        if match is None:
            raise ValueError(f"Not a scalar {'percentage' if percent else 'duration'}: {value!r}")
        result = float(match.group(1)) / (100 if percent else 1)
    if not math.isfinite(result):
        raise ValueError("Non-finite damage modifier")
    return result


def bind_damage_modifier(data: dict, *, skill_id: str, skills: dict, rank: int | None, progression=None):
    """Only explicit row/parameter references are evaluated; no description guessing."""
    sources = []
    passives = (
        {p.effect_id: p for p in (*progression.talents, *progression.active_potentials)}
        if progression is not None
        else {}
    )

    def scalar(ref, *, percent=True):
        if isinstance(ref, (int, float)):
            return _number(ref, percent=percent)
        if "passive" in ref:
            passive = passives.get(ref["passive"])
            if passive is None:
                raise ValueError(f"Inactive/missing passive damage source: {ref['passive']}")
            sources.append(f"{passive.source}/parameters/{ref['parameter']}")
            return _number(passive.parameters[ref["parameter"]], percent=percent)
        if "native_skill" in ref:
            from src.data.skill_timing import load_skill_timings

            store = load_skill_timings()
            value = store.ranked_parameter(ref["native_skill"], ref["parameter"], rank)
            sources.append(
                f"skill_timings/{store.index['snapshot_date']}/SkillPatchTable/"
                f"{ref['native_skill']}/{ref['parameter']}/rank={rank or 'max'}"
            )
            return _number(value, percent=percent)
        referenced = ref.get("skill", skill_id)
        stats = skills[referenced]["rank_stats"]
        matches = [r for r in stats["rows"] if r["label"] == ref["row"]]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous/missing damage row: {referenced}/{ref['row']}")
        column = len(stats["levels"]) - 1 if rank is None else rank - 1
        if column < 0 or column >= len(stats["levels"]):
            raise ValueError(f"Unavailable skill rank {rank}: {referenced}")
        sources.append(f"character_skills/{referenced}/rank_stats/{ref['row']}/{stats['levels'][column]}")
        return _number(matches[0]["values"][column], percent=percent)

    strength = data["strength"]
    base = scalar(strength.get("base", 0))
    terms = [
        MagnitudeTerm(t["input"], scalar(t["coefficient"]), scalar(t["cap"]) if "cap" in t else None)
        for t in strength.get("terms", [])
    ]
    by_count = tuple((int(k), scalar(v)) for k, v in strength.get("by_count", {}).items())
    if "multiplier" in strength:
        factor = scalar(strength["multiplier"])
        base *= factor
        terms = [
            MagnitudeTerm(t.input, t.coefficient * factor, t.cap * factor if t.cap is not None else None) for t in terms
        ]
        by_count = tuple((n, v * factor) for n, v in by_count)
    duration_ref = data.get("duration")
    duration = scalar(duration_ref, percent=False) if duration_ref is not None else None
    # Explicit potential adjustments operate on this effect, never create a second payout/buff.
    for adjustment in data.get("potential_adjustments", []):
        if adjustment["passive"] not in passives:
            continue
        value = scalar({"passive": adjustment["passive"], "parameter": adjustment["parameter"]})
        if adjustment["operation"] == "multiply_strength":
            base *= value
            terms = [
                MagnitudeTerm(t.input, t.coefficient * value, t.cap * value if t.cap is not None else None)
                for t in terms
            ]
            by_count = tuple((n, v * value) for n, v in by_count)
        elif adjustment["operation"] == "add_strength":
            base += value
        elif adjustment["operation"] == "add_duration":
            if duration is None:
                raise ValueError("Cannot adjust an unknown duration")
            duration += value
        else:
            raise ValueError(f"Unknown damage adjustment: {adjustment['operation']}")
    accumulation = strength.get("accumulation", "linear")
    if accumulation not in {"linear", "native_float32_repeated_add"}:
        raise ValueError(f"Unknown modifier accumulation: {accumulation}")
    if accumulation == "native_float32_repeated_add" and (
        len(terms) != 1 or terms[0].cap is not None or by_count or strength.get("count_input") is not None
    ):
        raise ValueError("Native repeated addition needs one uncapped marker-count term")
    return DamageModifierSpec(
        key=data["key"],
        bucket=DamageBucket(data["bucket"]),
        elements=tuple(data["elements"]),
        recipient=data["recipient"],
        magnitude=ModifierMagnitude(base, tuple(terms), by_count, strength.get("count_input"), accumulation),
        trigger=data["trigger"],
        damage_tags=tuple(data.get("damage_tags", [])),
        permanent=data.get("permanent", False),
        condition_inputs=tuple(data.get("condition_inputs", [])),
        duration=duration,
        duration_input=data.get("duration_input"),
        evaluation=data.get("evaluation", "application"),
        max_stacks=data.get("max_stacks", 1),
        stack_policy=data.get("stack_policy", "replace"),
        unresolved=data.get("unresolved"),
        sources=tuple(sources),
        lifetime_scope=data.get("lifetime_scope", "timed"),
    )
