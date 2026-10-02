"""Bounded search of periodic battle-skill schedules; not an in-game damage proof."""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from pathlib import Path

from src.data.character_mechanics import mechanic_blockers
from src.data.skill_allowlist import load_characters
from src.data.skill_rotation import _read_entries

_ATTACH = {
    "ATTACH_COLD": "寒冷",
    "ATTACH_BURN": "灼热",
    "ATTACH_ELECTROMAGNETIC": "电磁",
    "ATTACH_NATURAL": "自然",
}
_SUPPORT = re.compile(r"增幅|脆弱|易伤|连击|恢复.{0,6}技力|回复.{0,6}技力|提高.{0,6}攻击力")


@dataclass(frozen=True)
class DamageQuote:
    battle: float
    conservative: float
    ult: float
    link: float
    requires: frozenset[str] = frozenset()
    produces: frozenset[str] = frozenset()
    retain: bool = False


def load_damage_quotes(team: list[str], path: Path | None = None) -> dict[str, DamageQuote]:
    """Reuse the damage button's per-skill baseline, without a fictitious free link/normal bundle.

    The existing conservative snapshot changes only the dependent battle skill.
    No full-link4 multiplier is applied: its shared consumable pool is not monitored.
    """
    snapshots = load_characters()
    quotes = {}
    for row in _read_entries(path):
        name = row.get("character")
        if name not in team:
            continue

        def best(kind, row=row):
            values = []
            for skill in row.get("skills") or []:
                if not isinstance(skill, dict):
                    continue
                if skill.get("type") == kind:
                    value = skill.get("full_expect", skill.get("crit_expect", skill.get("non_crit", 0)))
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(value) and value >= 0:
                        values.append(value)
            return max(values, default=0.0)

        battle = best("战技")
        conservative = battle
        required = (row.get("full_caliber_requires") or {}).get("attach", [])
        required = [required] if isinstance(required, str) else required
        if required and row.get("cycle_expect_conservative") is not None:
            conservative = max(0, battle + float(row["cycle_expect_conservative"]) - float(row["cycle_expect"]))
        produced, retain = set(), False
        for skill in snapshots.get(name, {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("skill_type") != "战技":
                continue
            retain |= bool(_SUPPORT.search(skill.get("description") or ""))
            for effect in skill.get("effects") or []:
                if not isinstance(effect, dict) or (effect.get("count") or 0) <= 0:
                    continue
                element = _ATTACH.get(effect.get("effect_id"))
                if element:
                    produced.add(element)
        quotes[name] = DamageQuote(
            battle,
            conservative,
            best("终结技"),
            best("连携技"),
            frozenset(required),
            frozenset(produced),
            retain,
        )
    return quotes


@dataclass(frozen=True)
class CastOption:
    slot: str
    damage: DamageQuote
    duration: float
    actionable: float
    handoff: float
    cooldown: float
    sp_cost: float
    sp_gate: float


@dataclass(frozen=True)
class CyclePlan:
    slots: tuple[str, ...]
    seconds: float
    damage: float
    opening_damage: float

    @property
    def dps(self):
        return self.damage / self.seconds


def evaluate_cycle(sequence: tuple[CastOption, ...], regen: float = 8.0) -> CyclePlan | None:
    """Find a repeating phase (SP, CDs, attachment) instead of selecting a fixed time horizon.

    Warm-up starts with 300 SP but cannot improve the steady score. The next
    occurrence of the first cast closes the previous cycle's complete window.
    Each skill appears at most once in a candidate; repeating the cycle permits
    spamming a profitable skill. Unsettled candidates are rejected after 64 passes.
    """
    if not sequence or not math.isfinite(regen) or regen <= 0:
        return None
    if any(
        not all(
            math.isfinite(value) and value >= 0
            for value in (
                cast.duration,
                cast.actionable,
                cast.handoff,
                cast.cooldown,
                cast.sp_cost,
                cast.sp_gate,
                cast.damage.battle,
                cast.damage.conservative,
            )
        )
        or cast.sp_cost > cast.sp_gate
        or cast.sp_gate > 300
        for cast in sequence
    ):
        return None
    now, points = 0.0, 300.0
    ready = {}
    attached, expires = None, 0.0
    previous_phase, previous_start = None, 0.0
    previous_damage, opening_damage = 0.0, 0.0
    for cycle in range(64):
        damage = 0.0
        for index, cast in enumerate(sequence):
            start = max(now, ready.get(cast.slot, 0), now + max(0, cast.sp_gate - points) / regen)
            points = min(300, points + (start - now) * regen)
            if start >= expires:
                attached = None
            if index == 0:
                phase = (
                    round(points, 5),
                    tuple(round(max(0, ready.get(item.slot, 0) - start), 5) for item in sequence),
                    attached,
                    round(max(0, expires - start), 5) if attached else 0,
                )
                if cycle > 0 and phase == previous_phase:
                    return CyclePlan(
                        tuple(item.slot for item in sequence), start - previous_start, previous_damage, opening_damage
                    )
                previous_phase, previous_start = phase, start
            funded = not cast.damage.requires or attached in cast.damage.requires
            damage += cast.damage.battle if funded else cast.damage.conservative
            if funded and cast.damage.requires:
                attached = None
            points -= cast.sp_cost
            ready[cast.slot] = start + cast.cooldown

            # Different operators can overlap after the current skill commits;
            # a one-slot cycle repeats the same operator and therefore keeps the
            # conservative same-actor actionable boundary.
            effect_time = start + max(0.3, cast.handoff)
            delay = cast.actionable if len(sequence) == 1 else cast.handoff
            now = start + max(0.3, delay)
            points = min(300, points + (now - start) * regen)
            if now >= expires:
                attached = None
            # Credit the attachment when the gameplay effect starts, not when a
            # same-actor repeat eventually becomes legal.
            if len(cast.damage.produces) == 1:
                element = next(iter(cast.damage.produces))
                attached = element if attached in (None, element) else None
                expires = effect_time + 20 if attached else 0
        previous_damage = damage
        if cycle == 0:
            opening_damage = damage
    return None


def build_options(team, store, quotes) -> tuple[CastOption, ...]:
    # Characters with native multi-stage/resource state machines must not be
    # flattened into the legacy one-battle-button optimizer. Their semantics
    # are preserved by character_mechanics and consumed by the mechanic runner.
    if mechanic_blockers(team):
        return ()

    options = []
    for index, name in enumerate(team):
        profiles = store.profiles(name, "battle")
        if not profiles:
            return ()
        if name not in quotes or any(profile.sp_cost is None or profile.skill_points is None for profile in profiles):
            return ()  # Preserve the existing rotation when damage/cost is unknown.
        options.append(
            CastOption(
                str(index + 1),
                quotes[name],
                max(profile.duration for profile in profiles),
                max(profile.actionable for profile in profiles),
                max(profile.handoff for profile in profiles),
                max(profile.cooldown for profile in profiles),
                max(profile.sp_cost for profile in profiles),
                max(profile.skill_points for profile in profiles) * 100,
            )
        )
    return tuple(options)


def optimize_cycle(options: tuple[CastOption, ...], regen: float = 8.0) -> CyclePlan | None:
    """At most 64 ordered subsets for four slots; keep unpriced support casts."""
    if not options or len(options) > 4 or len({option.slot for option in options}) != len(options):
        return None
    mandatory = {option.slot for option in options if option.damage.retain}
    best = None
    for length in range(1, len(options) + 1):
        for sequence in itertools.permutations(options, length):
            if not mandatory.issubset({option.slot for option in sequence}):
                continue
            plan = evaluate_cycle(sequence, regen)
            if plan is None or plan.damage <= 0:
                continue
            score = (round(plan.dps, 6), plan.opening_damage, -plan.seconds, -len(plan.slots))
            if best is None or score > best[0]:
                best = (score, plan)
    return best[1] if best else None
