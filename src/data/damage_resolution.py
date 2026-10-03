"""Per-hit damage from a fixed character panel and independently timed modifiers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec


@dataclass(frozen=True)
class FixedDamagePanel:
    """Constant gear/weapon/attribute terms, computed before simulating any actions."""

    attack_white: float
    attack_percent: float
    attack_flat: float
    attribute_factor: float
    crit_rate: float
    crit_damage: float
    amplification: dict[str, float] = field(default_factory=dict)

    def attack(self, additional_percent=0.0):
        return (
            self.attack_white * (1 + self.attack_percent + additional_percent) + self.attack_flat
        ) * self.attribute_factor


@dataclass(frozen=True)
class DamageHit:
    actor: str
    enemy: str
    element: str
    multiplier: float
    damage_bonus: float = 0.0
    damage_tag: str = "skill"
    can_crit: bool = True
    enemy_multiplier: float = 1.0


@dataclass(frozen=True)
class DamageResult:
    non_crit: float | None
    expected: float | None
    buckets: dict[str, float]
    unknown: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActiveDamageModifier:
    spec: DamageModifierSpec
    source_actor: str
    recipient: str
    applied_at: float
    expires_at: float | None
    inputs: dict[str, float]
    value: float | None


class TimedDamageState:
    """Persistent modifier instances; per-source refresh and independent stack timers."""

    def __init__(self, team: tuple[str, ...]):
        self.team = team
        self.modifiers: list[ActiveDamageModifier] = []

    def expire(self, now: float):
        self.modifiers[:] = [m for m in self.modifiers if m.expires_at is None or now < m.expires_at]

    def phase_signature(self, now: float) -> tuple:
        self.expire(now)
        return tuple(
            sorted(
                (
                    (
                        m.spec.key,
                        m.source_actor,
                        m.recipient,
                        m.value,
                        None if m.expires_at is None else round(m.expires_at - now, 5),
                        tuple(sorted(m.inputs.items())),
                    )
                    for m in self.modifiers
                ),
                key=repr,
            )
        )

    def apply(
        self,
        spec: DamageModifierSpec,
        *,
        source_actor: str,
        now: float,
        event: str,
        inputs: dict[str, float] | None = None,
        enemy: str | None = None,
        main_control: str | None = None,
    ) -> bool:
        if event != spec.trigger:
            return False
        inputs = dict(inputs or {})
        if spec.recipient == "team":
            recipients = self.team
        elif spec.recipient == "other_allies":
            recipients = tuple(actor for actor in self.team if actor != source_actor)
        elif spec.recipient == "self":
            recipients = (source_actor,)
        elif spec.recipient == "enemy":
            recipients = (enemy,) if enemy is not None else ()
        elif spec.recipient == "main_control":
            recipients = (main_control,) if main_control is not None else ()
        else:
            raise ValueError(f"Unknown damage recipient: {spec.recipient}")
        if not recipients:
            return False
        if spec.max_stacks < 1 or spec.stack_policy not in {"replace", "independent"}:
            raise ValueError("Invalid damage modifier stack policy")
        duration = inputs.get(spec.duration_input) if spec.duration_input else spec.duration
        if duration is not None and (not math.isfinite(duration) or duration < 0):
            raise ValueError("Invalid damage modifier duration")
        value = spec.magnitude.evaluate(inputs) if spec.evaluation == "application" else None
        unresolved = spec.unresolved or ("unknown duration" if duration is None and not spec.permanent else None)
        self.expire(now)
        for recipient in recipients:
            same = [
                m
                for m in self.modifiers
                if m.spec.key == spec.key and m.source_actor == source_actor and m.recipient == recipient
            ]
            if spec.stack_policy == "replace":
                self.modifiers[:] = [m for m in self.modifiers if m not in same]
            elif len(same) >= spec.max_stacks:
                # A fresh stack replaces the earliest-expiring stack, preserving all other timers.
                self.modifiers.remove(min(same, key=lambda m: m.applied_at))
            self.modifiers.append(
                ActiveDamageModifier(
                    spec,
                    source_actor,
                    recipient,
                    now,
                    now + duration if duration is not None else None,
                    inputs,
                    None if unresolved else value,
                )
            )
        return True

    def remove(self, key: str, source_actor: str, recipient: str | None = None):
        self.modifiers[:] = [
            m
            for m in self.modifiers
            if not (
                m.spec.key == key and m.source_actor == source_actor and (recipient is None or m.recipient == recipient)
            )
        ]

    def resolve_hit(
        self, panel: FixedDamagePanel, hit: DamageHit, *, now: float, inputs: dict[str, float] | None = None
    ) -> DamageResult:
        self.expire(now)
        buckets = {bucket.value: 0.0 for bucket in DamageBucket}
        unknown = []
        for modifier in self.modifiers:
            spec = modifier.spec
            recipient = (
                hit.enemy if spec.bucket in {DamageBucket.VULNERABILITY, DamageBucket.DAMAGE_TAKEN} else hit.actor
            )
            if modifier.recipient != recipient:
                continue
            if hit.element not in spec.elements and "all" not in spec.elements:
                continue
            if spec.damage_tags and hit.damage_tag not in spec.damage_tags:
                continue
            current_inputs = {**modifier.inputs, **(inputs or {})}
            if any(current_inputs.get(key) == 0 for key in spec.condition_inputs):
                continue
            if any(key not in current_inputs for key in spec.condition_inputs):
                unknown.append(spec.key)
                continue
            value = modifier.value
            if spec.evaluation == "hit" and not spec.unresolved and (modifier.expires_at is not None or spec.permanent):
                value = spec.magnitude.evaluate(current_inputs)
            if value is None:
                unknown.append(spec.key)
            else:
                buckets[spec.bucket.value] += value
        if unknown:
            return DamageResult(None, None, buckets, tuple(sorted(set(unknown))))
        attack = panel.attack(buckets[DamageBucket.ATTACK.value])
        crit_rate = min(1.0, max(0.0, panel.crit_rate + buckets[DamageBucket.CRIT_RATE.value]))
        crit_damage = max(0.0, panel.crit_damage + buckets[DamageBucket.CRIT_DAMAGE.value])
        non_crit = attack * hit.multiplier * (1 + hit.damage_bonus + buckets[DamageBucket.DAMAGE_BONUS.value])
        for bucket in (DamageBucket.AMPLIFICATION, DamageBucket.VULNERABILITY, DamageBucket.DAMAGE_TAKEN):
            fixed = panel.amplification.get(hit.element, 0) if bucket == DamageBucket.AMPLIFICATION else 0
            non_crit *= 1 + fixed + buckets[bucket.value]
        non_crit *= hit.enemy_multiplier
        expected = non_crit * (1 + crit_rate * crit_damage) if hit.can_crit else non_crit
        return DamageResult(non_crit, expected, buckets)

    def resolve_quote(
        self,
        value: float,
        *,
        actor: str,
        enemy: str,
        element: str,
        now: float,
        damage_bonus=0.0,
        crit_rate=0.0,
        crit_damage=0.0,
        amplification=0.0,
        panel: FixedDamagePanel | None = None,
    ) -> float | None:
        """Scale an existing direct-damage quote; exact attack basis is required for ATK buffs.

        This preserves existing mechanic envelopes instead of fabricating a skill multiplier.
        A normalized panel suffices for independent non-attack groups, whose ratio cancels ATK.
        """
        normalized = panel or FixedDamagePanel(1, 0, 0, 1, crit_rate, crit_damage, {element: amplification})
        hit = DamageHit(actor, enemy, element, 1, damage_bonus=damage_bonus)
        result = self.resolve_hit(normalized, hit, now=now)
        if result.expected is None or (panel is None and result.buckets[DamageBucket.ATTACK.value] != 0):
            return None
        baseline = normalized.attack() * (1 + damage_bonus) * (1 + normalized.amplification.get(element, 0))
        baseline *= 1 + min(1, max(0, normalized.crit_rate)) * normalized.crit_damage
        return value * result.expected / baseline if baseline > 0 else None
