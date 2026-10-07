"""Per-hit damage from a fixed character panel and independently timed modifiers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from src.data.damage_attributes import ATTRIBUTES, DamageAttributeBasis, FinalAttributeDelta
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec
from src.data.immutable_combat_value import ImmutableCombatValue


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
    damage_bonus: dict[str, float] = field(default_factory=dict)
    attribute_basis: DamageAttributeBasis | None = None

    def with_attribute_deltas(self, deltas):
        if (not set(deltas) <= set(ATTRIBUTES) or any(type(value) not in (int, float)
                or not math.isfinite(value) for value in deltas.values())):
            raise ValueError("Invalid final attack attribute delta")
        if not any(value != 0 for value in deltas.values()):
            return self
        if self.attribute_basis is None:
            raise ValueError("Missing dynamic attack attribute basis")
        return replace(self, attribute_factor=self.attribute_factor + self.attribute_basis.factor_delta(deltas))

    def bonus_for(self, element, tags=()):
        """Fixed bonuses follow each native hit's element and decoration flags."""
        value = self.damage_bonus.get("all", 0) + self.damage_bonus.get(element, 0)
        if any(tag in {"normal", "skill", "combo", "ultimate"} for tag in tags):
            value += self.damage_bonus.get("all_skill", 0)
        return value + sum(self.damage_bonus.get(tag, 0) for tag in set(tags))

    def attack(self, additional_percent=0.0):
        return (
            self.attack_white * (1 + self.attack_percent + additional_percent) + self.attack_flat
        ) * self.attribute_factor


@dataclass(frozen=True)
class DamageHit(ImmutableCombatValue):
    actor: str
    enemy: str
    element: str
    multiplier: float
    damage_bonus: float = 0.0
    damage_tag: str = "skill"
    can_crit: bool = True
    enemy_multiplier: float = 1.0
    damage_tags: tuple[str, ...] = ()


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
class DamageField(ImmutableCombatValue):
    source_actor: str
    expires_at: float


class TimedDamageState:
    """Persistent modifier instances; per-source refresh and independent stack timers."""

    def __init__(self, team: tuple[str, ...]):
        self.team = team
        self.modifiers: list[ActiveDamageModifier] = []
        self.fields: dict[str, DamageField] = {}
        self.attribute_changes: list[FinalAttributeDelta] = []

    def apply_final_attribute_delta(self, *, actor, key, source, attribute, amount, now, duration=None, permanent=False):
        """Refresh one confirmed final delta; independent layers need distinct keys."""
        if (actor not in self.team or attribute not in ATTRIBUTES or not key or not source
                or not math.isfinite(now) or (amount is not None and
                    (type(amount) not in (int, float) or not math.isfinite(amount)))
                or (duration is None and not permanent) or (duration is not None and
                    (not math.isfinite(duration) or duration < 0)) or (permanent and duration is not None)):
            raise ValueError("Invalid confirmed final attribute change")
        self.expire(now)
        self.remove_final_attribute_delta(actor=actor, key=key, source=source)
        self.attribute_changes.append(FinalAttributeDelta(actor, key, source, attribute, amount,
                                      None if permanent else now + duration))

    def remove_final_attribute_delta(self, *, actor, key, source):
        self.attribute_changes[:] = [change for change in self.attribute_changes
                                    if (change.actor, change.key, change.source) != (actor, key, source)]

    def final_attribute_deltas(self, actor, *, now):
        if not self.attribute_changes:
            return {}, set()
        self.expire(now)
        deltas, unknown = {}, set()
        for change in self.attribute_changes:
            if change.actor != actor:
                continue
            if change.amount is None:
                unknown.add(change.attribute)
            else:
                deltas[change.attribute] = deltas.get(change.attribute, 0) + change.amount
        return deltas, unknown

    def effective_attributes(self, actor, fixed, *, now):
        deltas, unknown = self.final_attribute_deltas(actor, now=now)
        result = dict(fixed)
        for attribute, delta in deltas.items():
            if attribute in result:
                result[attribute] += delta
        for attribute in unknown:
            result.pop(attribute, None)
        return result

    def expire(self, now: float):
        self.attribute_changes[:] = [change for change in self.attribute_changes
                                    if change.expires_at is None or now < change.expires_at]
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
        entries.extend(("attribute", change.actor, change.key, change.source, change.attribute, change.amount,
                        None if change.expires_at is None else round(change.expires_at - now, 5))
                       for change in self.attribute_changes)
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
        self, panel: FixedDamagePanel, hit: DamageHit, *, now: float, inputs: dict[str, float] | None = None,
        native_damage_taken: float = 0.0,
        native_attack_percent: float = 0.0,
    ) -> DamageResult:
        self.expire(now)
        buckets = {bucket.value: 0.0 for bucket in DamageBucket}
        unknown = []
        deltas, missing_attributes = self.final_attribute_deltas(hit.actor, now=now)
        if missing_attributes:
            return DamageResult(None, None, buckets, tuple(f"Unknown final attribute: {key}" for key in sorted(missing_attributes)))
        try:
            panel = panel.with_attribute_deltas(deltas)
        except ValueError as error:
            return DamageResult(None, None, buckets, (str(error),))
        if not math.isfinite(native_damage_taken):
            return DamageResult(None, None, buckets, ("Non-finite native defender damage scale",))
        buckets[DamageBucket.DAMAGE_TAKEN.value] += native_damage_taken
        if not math.isfinite(native_attack_percent):
            return DamageResult(None, None, buckets, ("Non-finite native attack percentage",))
        buckets[DamageBucket.ATTACK.value] += native_attack_percent
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
            if spec.damage_tags and not set(spec.damage_tags).intersection(hit.damage_tags or (hit.damage_tag,)):
                continue
            current_inputs = {**modifier.inputs, **(inputs or {})}
            if spec.evaluation == "hit":
                # An unavailable current source attribute cannot fall back to
                # its old application-time value. Application snapshots keep
                # their already-resolved value, as authored.
                _, missing_source_attributes = self.final_attribute_deltas(modifier.source_actor, now=now)
                for attribute in missing_source_attributes:
                    current_inputs.pop("source." + attribute, None)
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
        # NormalCalcZone keeps separate attacker/defender additive arrays, then
        # multiplies both and clamps the combined zone to zero.
        normal_scale = max(0.0, (1 + hit.damage_bonus + buckets[DamageBucket.DAMAGE_BONUS.value])
                           * (1 + buckets[DamageBucket.DAMAGE_TAKEN.value]))
        non_crit = attack * hit.multiplier * normal_scale
        for bucket in (DamageBucket.AMPLIFICATION, DamageBucket.VULNERABILITY):
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
