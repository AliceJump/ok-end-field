"""Bounded search of periodic battle-skill schedules; not an in-game damage proof."""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from pathlib import Path

from src.data.character_mechanics import load_character_mechanics, mechanic_blockers
from src.data.character_skills import get_character, load_all_characters
from src.data.damage_modifiers import DamageModifierSpec
from src.data.damage_resolution import FixedDamagePanel, TimedDamageState
from src.data.hidden_state_expectation import HiddenStateExpectation, load_damage_envelopes
from src.data.skill_allowlist import load_characters
from src.data.skill_rotation import _read_entries

_ATTACH = {
    "ATTACH_COLD": "寒冷",
    "ATTACH_BURN": "灼热",
    "ATTACH_ELECTROMAGNETIC": "电磁",
    "ATTACH_NATURAL": "自然",
}
_SUPPORT = re.compile(r"增幅|脆弱|易伤|连击|恢复.{0,6}技力|回复.{0,6}技力|提高.{0,6}攻击力")
_FIXED_BASELINE = Path(__file__).resolve().parents[2] / "assets/data/fixed_damage_baseline.json"


@dataclass(frozen=True)
class DamageQuote:
    battle: float
    conservative: float
    ult: float
    link: float
    requires: frozenset[str] = frozenset()
    produces: frozenset[str] = frozenset()
    retain: bool = False
    element: str = "物理"
    damage_bonus: float = 0.0
    crit_rate: float = 0.0
    crit_damage: float = 0.0
    amplification: float = 0.0
    panel: FixedDamagePanel | None = None
    modifiers: tuple[DamageModifierSpec, ...] = ()


def load_damage_quotes(team: list[str], path: Path | None = None) -> dict[str, DamageQuote]:
    """Reuse the damage button's per-skill baseline, without a fictitious free link/normal bundle.

    The existing conservative snapshot changes only the dependent battle skill.
    No full-link4 multiplier is applied: its shared consumable pool is not monitored.
    """
    path = path or _FIXED_BASELINE
    snapshots = load_characters()
    canonical = {c.name: c for c in load_all_characters().values()}
    mechanics = load_character_mechanics()
    hidden = HiddenStateExpectation(team, mechanics)
    envelopes = load_damage_envelopes(path)
    quotes = {}
    for row in _read_entries(path):
        name = row.get("character")
        if name not in team:
            continue
        profile = row.get("profile") or {}
        character = canonical.get(name)
        if character is not None and profile:
            character = get_character(character.character_id, skill_rank=profile.get("skill_rank"), potential=profile.get("potential"))
        battle_skills = [s for s in (row.get("skills") or []) if isinstance(s, dict) and s.get("type") == "战技"]

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
        hidden_estimate = None
        mechanic = mechanics.get(name)
        if mechanic is not None:
            transition = next(
                (item for item in mechanic.transitions if item.action == "battle"),
                None,
            )
            hidden_estimate = hidden.estimate_damage(
                name,
                "战技",
                envelopes.get((name, "战技")),
                transition,
            )
            if hidden_estimate is not None:
                battle = hidden_estimate.expected
        conservative = hidden_estimate.low if hidden_estimate is not None else battle
        required = (row.get("full_caliber_requires") or {}).get("attach", [])
        required = [required] if isinstance(required, str) else required
        if hidden_estimate is None and required and row.get("cycle_expect_conservative") is not None:
            try:
                adjustment = float(row["cycle_expect_conservative"]) - float(row["cycle_expect"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                if math.isfinite(adjustment):
                    conservative = max(0, battle + adjustment)
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
            element=next((s.element.value for s in character.skills if s.skill_type.value == "战技"), row.get("element", "物理")) if character else row.get("element", "物理"),
            damage_bonus=max((float(s.get("bonus_pct", 0)) / 100 for s in battle_skills), default=0),
            crit_rate=float((row.get("panel") or {}).get("暴击率", 0)),
            crit_damage=float((row.get("panel") or {}).get("暴击伤害", 0)),
            amplification=max((float(s.get("amplification_pct", 0)) / 100 for s in battle_skills), default=0),
            panel=FixedDamagePanel(**row["panel"]["damage_basis"]) if (row.get("panel") or {}).get("damage_basis") else None,
            modifiers=tuple(e.damage_modifier for s in character.skills if s.skill_type.value == "战技"
                            for e in s.effects if e.damage_modifier is not None
                            and e.damage_modifier.trigger in {"on_hit", "on_cast"}) if character else (),
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
    required_sp: tuple[tuple[str, float], ...] = ()
    support_followups: tuple[tuple[str, str, float], ...] = ()

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
    modifier_state = TimedDamageState(tuple(cast.slot for cast in sequence))
    required_sp, support_followups = {}, {}
    ready = {}
    attached, expires = None, 0.0
    previous_phase, previous_start = None, 0.0
    previous_damage, opening_damage = 0.0, 0.0
    for cycle in range(64):
        damage = 0.0
        for index, cast in enumerate(sequence):
            gate, followup_ready = cast.sp_gate, now
            if len(sequence) > 1:
                followup = sequence[(index + 1) % len(sequence)]
                matches = any(
                    followup.damage.element in spec.elements or "all" in spec.elements
                    for spec in cast.damage.modifiers
                )
                if matches:
                    # Fund the following attack before applying a short support effect.
                    # Otherwise the estimator spends its entire lifetime waiting for SP/CD.
                    delay = max(.3, cast.handoff)
                    gate = min(300, max(gate, cast.sp_cost + followup.sp_gate - delay * regen))
                    followup_ready = ready.get(followup.slot, 0) - delay
                    support_followups[cast.slot] = (cast.slot, followup.slot, delay)
            required_sp[cast.slot] = gate
            start = max(now, ready.get(cast.slot, 0), followup_ready, now + max(0, gate - points) / regen)
            points = min(300, points + (start - now) * regen)
            if start >= expires:
                attached = None
            if index == 0:
                phase = (
                    round(points, 5),
                    tuple(round(max(0, ready.get(item.slot, 0) - start), 5) for item in sequence),
                    attached,
                    round(max(0, expires - start), 5) if attached else 0,
                    modifier_state.phase_signature(start),
                )
                if cycle > 0 and phase == previous_phase:
                    return CyclePlan(
                        tuple(item.slot for item in sequence), start - previous_start, previous_damage, opening_damage,
                        tuple(required_sp.items()), tuple(support_followups.values()),
                    )
                previous_phase, previous_start = phase, start
            funded = not cast.damage.requires or attached in cast.damage.requires
            # This generic estimator only applies explicit base cast/hit producers.
            # Conditional branches, random outcomes and passive listeners need actual outcomes.
            for spec in cast.damage.modifiers:
                modifier_state.apply(spec, source_actor=cast.slot, now=start, event="on_cast", enemy="target")
            effect_time = start + max(0.3, cast.handoff)
            quoted = modifier_state.resolve_quote(
                cast.damage.battle if funded else cast.damage.conservative,
                actor=cast.slot, enemy="target", element=cast.damage.element, now=effect_time,
                damage_bonus=cast.damage.damage_bonus, crit_rate=cast.damage.crit_rate,
                crit_damage=cast.damage.crit_damage, amplification=cast.damage.amplification,
                panel=cast.damage.panel,
            )
            if quoted is None:
                return None
            damage += quoted
            for spec in cast.damage.modifiers:
                modifier_state.apply(spec, source_actor=cast.slot, now=effect_time, event="on_hit", enemy="target")
            if funded and cast.damage.requires:
                attached = None
            points -= cast.sp_cost
            ready[cast.slot] = start + cast.cooldown

            # Different operators can overlap after the current skill commits;
            # a one-slot cycle repeats the same operator and therefore keeps the
            # conservative same-actor actionable boundary.
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
