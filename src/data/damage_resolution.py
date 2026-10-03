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
    field_id: str | None = None


@dataclass(frozen=True)
class DamageField:
    source_actor: str
    expires_at: float


class TimedDamageState:
    """Persistent modifier instances; per-source refresh and independent stack timers."""

    def __init__(self, team: tuple[str, ...]):
        self.team = team
        self.modifiers: list[ActiveDamageModifier] = []
        self.fields: dict[str, DamageField] = {}

    def expire(self, now: float):
        for field_id, instance in tuple(self.fields.items()):
            if now >= instance.expires_at:
                self.remove_field(field_id)
        self.modifiers[:] = [m for m in self.modifiers if m.expires_at is None or now < m.expires_at]

    def spawn_field(self, field_id: str, *, source_actor: str, now: float, duration: float):
        """Register an actual spawned field; cast/handoff time is not its birth time."""
        if not math.isfinite(duration) or duration <= 0 or not math.isfinite(now):
            raise ValueError("Invalid field lifetime")
        self.expire(now)
        if field_id in self.fields:
            raise ValueError("Field instance already exists")
        self.fields[field_id] = DamageField(source_actor, now + duration)

    def leave_field(self, field_id: str, recipient: str):
        self.modifiers[:] = [m for m in self.modifiers if not (m.field_id == field_id and m.recipient == recipient)]

    def remove_field(self, field_id: str):
        self.fields.pop(field_id, None)
        self.modifiers[:] = [m for m in self.modifiers if m.field_id != field_id]

    def phase_signature(self, now: float) -> tuple:
        self.expire(now)
        entries = [
            (
                m.spec.key,
                m.source_actor,
                m.recipient,
                m.field_id,
                m.value,
                None if m.expires_at is None else round(m.expires_at - now, 5),
                tuple(sorted(m.inputs.items())),
            )
            for m in self.modifiers
        ]
        entries.extend(
            ("field", key, instance.source_actor, round(instance.expires_at - now, 5))
            for key, instance in self.fields.items()
        )
        return tuple(sorted(entries, key=repr))

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
        field_id: str | None = None,
    ) -> bool:
        if event != spec.trigger:
            return False
        inputs = dict(inputs or {})
        self.expire(now)
        if spec.lifetime_scope not in {"timed", "field"}:
            raise ValueError("Unknown damage modifier lifetime scope")
        field = self.fields.get(field_id) if field_id is not None else None
        if spec.lifetime_scope == "field":
            if field is None or field.source_actor != source_actor:
                return False
        elif field_id is not None:
            raise ValueError("Timed modifier cannot inherit a field lifetime")
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
        if field is not None:
            duration = min(duration, field.expires_at - now) if duration is not None else field.expires_at - now
        value = spec.magnitude.evaluate(inputs) if spec.evaluation == "application" else None
        unresolved = spec.unresolved or ("unknown duration" if duration is None and not spec.permanent else None)
        self.expire(now)
        for recipient in recipients:
            same = [
                m
                for m in self.modifiers
                if m.spec.key == spec.key and m.source_actor == source_actor and m.recipient == recipient
                and m.field_id == field_id
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
                    field_id,
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
        field_values = {}
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
            elif modifier.field_id is not None:
                # Identical auras from one producer are one effect. Different strengths
                # need an explicit native priority rule before they can be priced.
                identity = (spec.key, modifier.source_actor, modifier.recipient, spec.bucket)
                if identity in field_values and field_values[identity] != value:
                    unknown.append(spec.key)
                field_values[identity] = value
            else:
                buckets[spec.bucket.value] += value
        for (_, _, _, bucket), value in field_values.items():
            buckets[bucket.value] += value
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
