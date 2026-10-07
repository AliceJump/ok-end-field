"""Transactional combat actions shared by planning and confirmed runtime feedback.

An ActionProgram contains explicit gameplay events. Display descriptions and legacy
marker values are never executable formulas. Missing outcomes remain unresolved.
"""

from __future__ import annotations

import copy
import heapq
import math
import time
from dataclasses import dataclass, field

from src.data.combat_expressions import CombatExpression, MissingCombatInput
from src.data.combat_input_requirements import event_input_keys, required_input_keys
from src.data.combat_model import ATTACH_ELEMENTS, PHYSICAL_RULES, EnemyCombatState
from src.data.combat_value_snapshots import EffectValue, ResourceValue, effect_value, resource_value
from src.data.damage_modifiers import DamageModifierSpec
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState
from src.data.effect_semantics import EFFECT_SEMANTICS, EffectKind, EffectOwner, RefreshPolicy
from src.data.effects import EffectType
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.skill_types import CombatResourceType, ResourceChangeKind, SkillEffect, SkillResourceChange


class UnresolvedMechanic(ValueError):
    """A missing gameplay parameter must not become an implicit zero or one."""


class CombatSearchLimit(RuntimeError):
    """An incomplete bounded search must not select its first explored branch."""


@dataclass(frozen=True)
class EffectInstance(ImmutableCombatValue):
    effect: EffectType
    owner: str
    source: str
    count: int
    expires_at: float | None
    instance_id: str | None = None


@dataclass
class CharacterCombatState:
    energy: float = 0
    energy_cap: float = 200
    alive: bool = True
    attributes: dict[str, float] = field(default_factory=dict)
    panel: FixedDamagePanel | None = None
    blackboard: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectRequirement(ImmutableCombatValue):
    effect: EffectType
    minimum: int = 1
    maximum: int | None = None


@dataclass(frozen=True)
class NativeBuffChange(ImmutableCombatValue):
    key: str
    count: CombatExpression
    duration: CombatExpression | None = None
    permanent: bool = False
    remove_all: bool = False
    maximum: int | None = None
    target: str = "self"
    selector: NativeTarget | None = None
    definition: NativeBuffProgram | None = None
    tags: tuple[int, ...] = ()
    action_finish_after: float | None = None


@dataclass(frozen=True)
class NativeTarget(ImmutableCombatValue):
    kind: str
    key: str = ""


@dataclass(frozen=True)
class NativeSpellInfliction(ImmutableCombatValue):
    infliction_type: int
    source: NativeTarget
    target: NativeTarget
    is_extra: bool = False
    burst_buff: NativeBuffChange | None = None
    cross_buffs: tuple[tuple[int, NativeBuffChange], ...] = ()


@dataclass(frozen=True)
class NativeResourceChange(ImmutableCombatValue):
    resource: CombatResourceType
    amount: CombatExpression
    coefficient: CombatExpression
    source: NativeTarget
    target: NativeTarget
    percent: bool = False
    ignore_energy_gain: bool = False
    returned_sp: bool = False
    only_main_source: bool = False
    default_energy: tuple[float, float] | None = None


@dataclass(frozen=True)
class NativeTargetBinding(ImmutableCombatValue):
    key: str
    selectors: tuple[NativeTarget, ...]


@dataclass(frozen=True)
class NativeBuffQuery(ImmutableCombatValue):
    key: str
    target: NativeTarget
    buff_ids: tuple[str, ...]
    tag_mode: str | None = None
    tags: tuple[int, ...] = ()
    count_type: int = 0


@dataclass(frozen=True)
class NativeAttributeQuery(ImmutableCombatValue):
    key: str
    target: NativeTarget
    attribute: str


@dataclass(frozen=True)
class NativeBuffProgram(ImmutableCombatValue):
    parameters: tuple[tuple[str, float], ...]
    inherited: tuple[tuple[str, CombatExpression], ...]
    duration: CombatExpression | None
    period: CombatExpression
    trigger_limit: CombatExpression
    wait_first: bool
    stacking: int
    maximum: CombatExpression
    callbacks: tuple[tuple[int, ActionProgram], ...]
    subscriptions: tuple[tuple[str, ActionProgram], ...] = ()
    unresolved: tuple[str, ...] = ()
    tags: tuple[int, ...] = ()
    stacking_key: str | None = None
    damage_scales: tuple = ()


@dataclass(frozen=True)
class NativeBuffControl(ImmutableCombatValue):
    instance: str
    kind: str
    expected_at: float | None = None


@dataclass(frozen=True)
class NativeSkillChange(ImmutableCombatValue):
    slot: int
    target_skill: str
    source: NativeTarget
    lifetime: int
    duration: CombatExpression
    inherit_cooldown: bool = False
    reverted_skill: str | None = None


@dataclass(frozen=True)
class NativeSkillOverride(ImmutableCombatValue):
    uid: str
    scope: str
    target_skill: str
    expires: float | None
    ends_with_scope: bool
    reverted_skill: str | None
    inherit_cooldown: bool = False


@dataclass(frozen=True)
class NativeSkillControl(ImmutableCombatValue):
    actor: str
    slot: int
    uid: str


@dataclass(frozen=True)
class NativeIteration(ImmutableCombatValue):
    target: NativeTarget
    events: tuple[CombatEvent, ...]


def walk_combat_events(events):
    for event in events:
        yield event
        for iteration in event.iterations:
            yield from walk_combat_events(iteration.events)
        for listener in event.listeners:
            yield from walk_combat_events(listener.events)
        buffs = (*event.native_buffs, *(spell.burst_buff for spell in event.native_spells if spell.burst_buff is not None),
                 *(change for spell in event.native_spells for _, change in spell.cross_buffs))
        for change in buffs:
            if change.definition is not None:
                if change.definition.unresolved:
                    yield CombatEvent(0, "unresolved_native_buff", unresolved=change.definition.unresolved)
                for _, program in (*change.definition.callbacks, *change.definition.subscriptions):
                    yield from walk_combat_events(program.events)


@dataclass(frozen=True)
class CombatEvent(ImmutableCombatValue):
    """One authored event, timed relative to the action's actual start."""

    at: float
    name: str
    effects: tuple[EffectValue | SkillEffect, ...] = ()
    resources: tuple[ResourceValue | SkillResourceChange, ...] = ()
    modifiers: tuple[DamageModifierSpec, ...] = ()
    hit: DamageHit | None = None
    inputs: tuple[tuple[str, float], ...] = ()
    requires: tuple[EffectRequirement, ...] = ()
    field_id: str | None = None
    any_requires: tuple[tuple[EffectRequirement, ...], ...] = ()
    field_duration: float | None = None
    remove_field: bool = False
    hit_multiplier_input: str | None = None
    unresolved: tuple[str, ...] = ()
    condition: CombatExpression | None = None
    assignments: tuple[tuple[str, CombatExpression], ...] = ()
    hit_multiplier_formula: CombatExpression | None = None
    resource_formulas: tuple[CombatExpression | None, ...] = ()
    persists_after_interrupt: bool = True
    native_buffs: tuple[NativeBuffChange, ...] = ()
    listeners: tuple[NativeListener, ...] = ()
    timers: tuple[tuple[str, CombatExpression], ...] = ()
    native_resources: tuple[NativeResourceChange, ...] = ()
    target_bindings: tuple[NativeTargetBinding, ...] = ()
    iterations: tuple[NativeIteration, ...] = ()
    buff_controls: tuple[NativeBuffControl, ...] = ()
    skill_changes: tuple[NativeSkillChange, ...] = ()
    skill_controls: tuple[NativeSkillControl, ...] = ()
    end_scope: str | None = None
    native_spells: tuple[NativeSpellInfliction, ...] = ()
    native_spell_bursts: tuple[int, ...] = ()
    hit_source: NativeTarget | None = None

    def __post_init__(self):
        object.__setattr__(self, "effects", tuple(effect_value(effect) for effect in self.effects))
        object.__setattr__(self, "resources", tuple(resource_value(change) for change in self.resources))
        super().__post_init__()
        keys = event_input_keys(self)
        if self.hit_multiplier_input:
            keys.add(self.hit_multiplier_input)
        object.__setattr__(self, "input_keys", frozenset(keys))


@dataclass(frozen=True)
class NativeListener(ImmutableCombatValue):
    buff_id: str
    trigger: str
    events: tuple[CombatEvent, ...]


@dataclass(frozen=True)
class ActionProgram(ImmutableCombatValue):
    key: str
    actor: str
    kind: str
    sp_cost: float
    duration: float
    cooldown: float
    events: tuple[CombatEvent, ...]
    requires: tuple[EffectRequirement, ...] = ()
    energy_cost: float = 0
    enemy: str = "target"
    replacement: str | None = None
    gate: float | None = None
    any_requires: tuple[tuple[EffectRequirement, ...], ...] = ()
    forbids: tuple[EffectRequirement, ...] = ()
    actor_lock: float | None = None
    cooldown_key: str | None = None
    parameters: tuple[tuple[str, float], ...] = ()
    next_action_windows: tuple[tuple[float, float, tuple[str, ...]], ...] = ()
    native_buff_ids: tuple[str, ...] = ()
    native_timer_ids: tuple[str, ...] = ()
    native_buff_queries: tuple[NativeBuffQuery, ...] = ()
    native_slot: int | None = None
    native_requires_override: bool = False
    native_attribute_queries: tuple[NativeAttributeQuery, ...] = ()
    scenario_ignored_nodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CombatSnapshot(ImmutableCombatValue):
    time: float
    sp: float
    energy: tuple[tuple[str, float], ...]
    effects: tuple[tuple[str, str, int, float | None], ...]
    cooldowns: tuple[tuple[str, float], ...]
    damage: float


@dataclass(frozen=True)
class ActionOutcome(ImmutableCombatValue):
    program: str
    before: CombatSnapshot
    after: CombatSnapshot
    damage: float
    consumed: tuple[tuple[str, int], ...]
    events: tuple[str, ...]
    sp_overflow: float
    unresolved: tuple[str, ...]


class CombatWorldState:
    """One enemy/character/team/field state, including the pending event queue."""

    def __init__(self, actors: tuple[str, ...], *, sp=300.0, regen=8.0):
        if not math.isfinite(sp) or not 0 <= sp <= 300 or not math.isfinite(regen) or regen < 0:
            raise ValueError("Invalid initial team resources")
        self.time = 0.0
        self.sp = float(sp)
        self.returned_sp = 0.0
        self.regen = float(regen)
        self.characters = {actor: CharacterCombatState() for actor in actors}
        self.enemies: dict[str, EnemyCombatState] = {"target": EnemyCombatState()}
        self.effects: list[EffectInstance] = []
        self.damage_state = TimedDamageState(actors)
        self.cooldowns: dict[str, float] = {}
        self.actor_ready: dict[str, float] = {}
        self.active_actions: dict[str, tuple[str, ActionProgram, float]] = {}
        self.damage = 0.0
        self.sp_overflow = 0.0
        self.unresolved: set[str] = set()
        self._queue = []
        self._sequence = 0
        self._actions_started = 0
        self._seen_events: set[tuple[str, int]] = set()
        self._events: list[str] = []
        self._consumed: dict[tuple[str, EffectType], int] = {}
        self._action_consumed: dict[str, dict[tuple[str, EffectType], int]] = {}
        self._action_inputs: dict[str, dict[str, float]] = {}
        self._action_targets: dict[str, dict[str, tuple[str, ...]]] = {}
        self._executing_action: str | None = None
        self._started_ids: set[str] = set()
        # Scalar reaction parameters are supplied by the chosen damage profile.
        # A state transition still occurs when its damage policy is unresolved.
        self.reaction_inputs: dict[str, float] = {}
        self.passive_modifiers: dict[str, tuple[DamageModifierSpec, ...]] = {}
        self.main_control = actors[0] if actors else None
        self.default_energy_per_sp: dict[str, float] = {}
        self.native_buffs: dict[tuple[str, str], tuple[int, float | None]] = {}
        self.native_buff_instances: dict[str, object] = {}
        self.native_skill_overrides: dict[tuple[str, int], NativeSkillOverride] = {}
        self.native_skill_slots: dict[tuple[str, int], str] = {}
        self.native_programs: dict[tuple[str, str], ActionProgram] = {}
        self.native_passives: dict[str, object] = {}
        self.native_listeners: list[tuple[str, str, ActionProgram, NativeListener]] = []
        self.native_timers: dict[tuple[str, str], float] = {}
        self.native_character_hooks: list[tuple[str, ActionProgram]] = []
        self.native_buff_tags: dict[str, tuple[int, ...]] = {}
        self.pool_expiries: dict[tuple[str, EffectType], float] = {}

    def register_damage_passives(self, actor, modifiers):
        self.passive_modifiers[actor] = tuple(modifiers)
        self.emit("battle_start", actor)

    def damage_inputs(self, actor, enemy="target"):
        values = {f"source.{k}": v for k, v in self.characters[actor].attributes.items()}
        values.update({
            "source.is_main": float(actor == self.main_control),
            "enemy.has_crystal": float(self.count(actor, enemy, EffectType.STATUS_ORIGINIUM_CRYSTAL) > 0),
            "enemy.slowed": float(self.count(actor, enemy, EffectType.STATUS_SLOW) > 0),
            "enemy.staggered": float(self.count(actor, enemy, EffectType.STATUS_STAGGER) > 0),
        })
        return values

    def emit(self, event, actor, enemy="target", inputs=None):
        """Emit only a resolved outcome; registering a passive is not its trigger."""
        values = self.damage_inputs(actor, enemy)
        values.update(inputs or {})
        for spec in self.passive_modifiers.get(actor, ()):
            if spec.trigger == event:
                self.damage_state.apply(spec, source_actor=actor, enemy=enemy, now=self.time,
                                        event=event, inputs=values)
        self._events.append(event)

    def fork(self):
        return copy.deepcopy(self)

    def _owner(self, actor, enemy, effect):
        owner = EFFECT_SEMANTICS[effect].owner
        return enemy if owner == EffectOwner.ENEMY else "team" if owner == EffectOwner.TEAM else actor

    def count(self, actor: str, enemy: str, effect: EffectType) -> int:
        self._expire()
        target = self.enemies.setdefault(enemy, EnemyCombatState())
        if effect in ATTACH_ELEMENTS:
            return target.infliction_stacks if target.infliction_element == effect else 0
        if effect in {EffectType.STACK_SHRED, EffectType.STATUS_SHRED}:
            return target.shred_stacks
        if effect == EffectType.STATUS_SPELL_INFLICT:
            return target.infliction_stacks
        if effect == EffectType.STATUS_SPELL_ANOMALY:
            return max((self.count(actor, enemy, x) for x in (
                EffectType.STATUS_FROZEN, EffectType.STATUS_BURNING,
                EffectType.STATUS_CONDUCTING, EffectType.STATUS_CORROSION,
            )), default=0)
        if effect == EffectType.EVENT_SHRED_CONSUMED:
            return target.transient_events.get(effect, 0)
        owner = self._owner(actor, enemy, effect)
        return sum(x.count for x in self.effects if x.effect == effect and x.owner == owner)

    def satisfies(self, actor, enemy, requirements, any_groups=()):
        return all(
            self.count(actor, enemy, r.effect) >= r.minimum
            and (r.maximum is None or self.count(actor, enemy, r.effect) <= r.maximum)
            for r in requirements
        ) and all(any(self.satisfies(actor, enemy, (r,)) for r in group) for group in any_groups)

    def native_buff_count(self, owner, buff_id):
        if buff_id == "buff_physical_no_guard" and owner in self.enemies:
            return self.enemies[owner].shred_stacks
        if buff_id.startswith("buff_common_energy_shard_attached_"):
            from src.data.native_spell_runtime import attachment_count

            count = attachment_count(self, owner, buff_id)
            if count is not None:
                return count
        return self.native_buffs.get((owner, buff_id), (0, None))[0]

    def _expire(self):
        for (owner, effect), expires in tuple(self.pool_expiries.items()):
            if expires <= self.time:
                if effect == EffectType.STACK_SHRED:
                    self.enemies[owner].shred_stacks = 0
                del self.pool_expiries[owner, effect]
        running = {(v.owner, v.key) for v in self.native_buff_instances.values()}
        self.native_buffs = {key: value for key, value in self.native_buffs.items()
                             if key in running or value[1] is None or value[1] > self.time}
        self.native_listeners[:] = [entry for entry in self.native_listeners
                                   if (entry[0], entry[3].buff_id) in self.native_buffs]
        for instance in self.effects:
            if instance.instance_id and instance.expires_at is not None and self.time >= instance.expires_at:
                self.damage_state.remove_field(instance.instance_id)
        self.effects[:] = [x for x in self.effects if x.expires_at is None or self.time < x.expires_at]
        self.damage_state.expire(self.time)

    def snapshot(self):
        self._expire()
        effects = [(x.owner, x.effect.value, x.count, x.expires_at) for x in self.effects]
        for name, enemy in self.enemies.items():
            if enemy.shred_stacks:
                effects.append((name, EffectType.STACK_SHRED.value, enemy.shred_stacks,
                                self.pool_expiries.get((name, EffectType.STACK_SHRED))))
            if enemy.infliction_element is not None:
                effects.append((name, enemy.infliction_element.value, enemy.infliction_stacks,
                                self.time + enemy.infliction_time_left))
        return CombatSnapshot(
            self.time, self.sp, tuple(sorted((a, s.energy) for a, s in self.characters.items())),
            tuple(sorted(effects, key=repr)), tuple(sorted(self.cooldowns.items())), self.damage,
        )

    def _advance_clock(self, now):
        elapsed = now - self.time
        gained = self.regen * elapsed
        self.sp_overflow += max(0, self.sp + gained - 300)
        self.sp = min(300, self.sp + gained)
        for enemy in self.enemies.values():
            enemy.tick(elapsed)
        self.time = now
        self._expire()

    def advance(self, now: float):
        if not math.isfinite(now) or now < self.time:
            raise ValueError("Combat time cannot move backwards")
        while self._queue and self._queue[0][0] <= now:
            at, sequence, action_id, program, event = heapq.heappop(self._queue)
            self._advance_clock(at)
            self._execute_event(action_id, sequence, program, event)
        self._advance_clock(now)

    def consume(self, actor, enemy, effect, amount: int | None = None):
        available = self.count(actor, enemy, effect)
        used = available if amount is None else min(available, max(0, amount))
        target = self.enemies[enemy]
        if effect in {EffectType.STACK_SHRED, EffectType.STATUS_SHRED}:
            target.shred_stacks -= used
            if target.shred_stacks == 0:
                self.pool_expiries.pop((enemy, EffectType.STACK_SHRED), None)
            if used:
                target.transient_events[EffectType.EVENT_SHRED_CONSUMED] = (
                    target.transient_events.get(EffectType.EVENT_SHRED_CONSUMED, 0) + used
                )
        elif effect in ATTACH_ELEMENTS or effect == EffectType.STATUS_SPELL_INFLICT:
            target.infliction_stacks -= used
            if target.infliction_stacks == 0:
                target.infliction_element = None
                target.infliction_time_left = 0
        else:
            owner = self._owner(actor, enemy, effect)
            remaining = used
            updated = []
            for instance in self.effects:
                if instance.effect == effect and instance.owner == owner and remaining:
                    take = min(instance.count, remaining)
                    remaining -= take
                    if instance.count > take:
                        updated.append(EffectInstance(effect, owner, instance.source, instance.count - take,
                                                      instance.expires_at, instance.instance_id))
                    elif instance.instance_id:
                        self.damage_state.remove_field(instance.instance_id)
                else:
                    updated.append(instance)
            self.effects = updated
        identity = (actor, effect)
        self._consumed[identity] = self._consumed.get(identity, 0) + used
        if self._executing_action is not None:
            counts = self._action_consumed.setdefault(self._executing_action, {})
            counts[identity] = counts.get(identity, 0) + used
        if used and effect == EffectType.STATUS_ORIGINIUM_CRYSTAL:
            self.emit("originium_crystal_consumed", actor, enemy)
        return used

    def apply_effect(self, actor, enemy, effect: SkillEffect, inputs=None):
        """Apply a typed effect after a proven event, preserving holder semantics."""
        inputs = inputs or {}
        eid = effect.effect_id
        semantics = EFFECT_SEMANTICS[eid]
        count = effect.count
        if effect.damage_modifier is not None:
            return  # The modifier's precise event/recipient is handled separately.
        if eid == EffectType.CONSUME_ALL:
            if effect.subject_effect_id is None:
                raise UnresolvedMechanic("CONSUME_ALL requires subject_effect_id")
            self.consume(actor, enemy, effect.subject_effect_id)
            return
        if eid == EffectType.REMOVE_THUNDER_SPEAR:
            for subject in (EffectType.MECH_THUNDER_SPEAR, EffectType.MECH_STRONG_THUNDER_SPEAR):
                self.consume(actor, enemy, subject)
            return
        if eid in {EffectType.CLEAR_ATTACH, EffectType.CLEAR_COLD, EffectType.CLEAR_NATURAL}:
            subject = {EffectType.CLEAR_COLD: EffectType.ATTACH_COLD, EffectType.CLEAR_NATURAL: EffectType.ATTACH_NATURAL}.get(eid, EffectType.STATUS_SPELL_INFLICT)
            self.consume(actor, enemy, subject)
            return
        if semantics.kind in {EffectKind.OPERATION, EffectKind.UNRESOLVED}:
            raise UnresolvedMechanic(f"Unbound operation: {eid.value}")
        if count is not None and count < 0:
            self.consume(actor, enemy, eid, None if effect.consumes_all else -count)
            return
        if eid in {EffectType.STATUS_HEAVY_HIT, EffectType.STATUS_KNOCKDOWN,
                   EffectType.STATUS_HEAVY_STRIKE, EffectType.STATUS_SHATTER}:
            self._physical_anomaly(actor, enemy, effect, inputs)
            return
        if semantics.kind == EffectKind.PREDICATE:
            raise UnresolvedMechanic(f"Predicate cannot be produced: {eid.value}")
        if semantics.kind == EffectKind.EVENT:
            self._events.append(eid.value)
            return
        if count is None:
            count = inputs.get(f"effect.{eid.value}.count")
        if count is None or count != int(count) or count < 0:
            raise UnresolvedMechanic(f"Unknown count: {eid.value}")
        count = int(count)
        if count == 0:
            return
        if eid in ATTACH_ELEMENTS:
            for _ in range(count):
                reaction = self.enemies[enemy].apply_infliction(eid)
                if reaction is not None:
                    self._events.append(reaction.value)
                    # Reaction damage/lifetimes require the native reaction policy;
                    # do not price an attachment producer while omitting this damage.
                    self.unresolved.add(f"Unbound spell reaction: {reaction.value}")
            return
        if eid == EffectType.STACK_SHRED:
            self.add_shred(enemy, count)
            return
        duration = effect.duration
        if duration is None:
            duration = inputs.get(f"effect.{eid.value}.duration")
        if duration is not None and (not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0):
            raise UnresolvedMechanic(f"Unknown duration: {eid.value}")
        if duration is None and semantics.kind in {EffectKind.STATE, EffectKind.ENTITY} and not inputs.get(f"effect.{eid.value}.permanent"):
            raise UnresolvedMechanic(f"Unknown lifetime: {eid.value}")
        owner = self._owner(actor, enemy, eid)
        if semantics.owner == EffectOwner.ACTOR:
            recipients = self.characters if effect.target == "team" else (actor,)
        else:
            recipients = (owner,)
        for recipient in recipients:
            same = [x for x in self.effects if x.owner == recipient and x.effect == eid]
            current = sum(x.count for x in same)
            if semantics.refresh == RefreshPolicy.STACK:
                count_now = current + count
            else:
                count_now = count
            if semantics.cap is not None:
                count_now = min(semantics.cap, count_now)
            if inputs.get(f"effect.{eid.value}.independent"):
                # New layers have their own expiry. Overflow removes the oldest,
                # never refreshes all existing layers to the newest lifetime.
                keep = max(0, count_now - count)
                retained = []
                for instance in reversed(same):
                    n = min(keep, instance.count)
                    if n:
                        retained.append(EffectInstance(eid, recipient, instance.source, n, instance.expires_at))
                        keep -= n
                self.effects[:] = [x for x in self.effects if x not in same]
                self.effects.extend(reversed(retained))
                self.effects.append(EffectInstance(eid, recipient, actor, min(count, count_now),
                                                   self.time + duration if duration is not None else None))
                continue
            if semantics.kind == EffectKind.ENTITY:
                # Entity identity is separate from effect ID. Two fields born at
                # different times must not inherit one another's expiry.
                instance_id = f"{actor}:{eid.value}:{self._sequence}:{len(self.effects)}"
                self.effects.append(EffectInstance(eid, recipient, actor, count,
                                                   self.time + duration if duration is not None else None, instance_id))
                if semantics.owner == EffectOwner.FIELD and duration is not None and duration > 0:
                    self.damage_state.spawn_field(instance_id, source_actor=actor, now=self.time, duration=duration)
                continue
            self.effects[:] = [x for x in self.effects if x not in same]
            self.effects.append(EffectInstance(eid, recipient, actor, count_now, self.time + duration if duration is not None else None))
        if eid == EffectType.STATUS_ORIGINIUM_CRYSTAL:
            self.emit("crystal_attached", actor, enemy)
        elif eid == EffectType.STACK_TRACE:
            self.emit("claw_mark_applied", actor, enemy)
        elif eid == EffectType.STATUS_BURNING:
            self.emit("burning_applied", actor, enemy)

    def add_shred(self, enemy, count):
        self.enemies[enemy].add_shred(count)
        duration = self.reaction_inputs.get("physical.shred.duration")
        if duration is not None:
            if not math.isfinite(duration) or duration <= 0:
                raise UnresolvedMechanic("Invalid native shred lifetime")
            self.pool_expiries[enemy, EffectType.STACK_SHRED] = self.time + duration

    def _physical_anomaly(self, actor, enemy, effect, inputs):
        target = self.enemies[enemy]
        eid = effect.effect_id
        previous = target.shred_stacks
        if previous == 0:
            self.add_shred(enemy, 1)
            self._events.append("physical:first_shred")
            return
        index = {EffectType.STATUS_HEAVY_HIT: 1, EffectType.STATUS_KNOCKDOWN: 2,
                 EffectType.STATUS_HEAVY_STRIKE: 3, EffectType.STATUS_SHATTER: 4}[eid]
        native_type = {EffectType.STATUS_HEAVY_HIT: 0, EffectType.STATUS_KNOCKDOWN: 1,
                       EffectType.STATUS_HEAVY_STRIKE: 3, EffectType.STATUS_SHATTER: 2}[eid]
        self.dispatch_native("OnBeforeOutputPhysicalInfliction", actor,
                             {"event.physical_type": float(native_type), "event.shred_before": float(previous)}, target=enemy)
        native_buff = {EffectType.STATUS_HEAVY_STRIKE: "buff_physical_crushed",
                       EffectType.STATUS_SHATTER: "buff_physical_do_fracture"}.get(eid)
        if native_buff is not None:
            self.dispatch_character_event("OnBeforeAddedBuff", actor, enemy, native_buff)
        rule = PHYSICAL_RULES[index]
        if rule.consumes_all:
            self.consume(actor, enemy, EffectType.STACK_SHRED)
        else:
            self.add_shred(enemy, 1)
        coefficient = self.reaction_inputs.get(f"physical.{eid.value}.multiplier.{previous}")
        native_row = coefficient is not None
        if coefficient is None:
            coefficient = self.reaction_inputs.get(f"physical.{eid.value}.multiplier")
        arts = self.characters[actor].attributes.get("arts_strength")
        panel = self.characters[actor].panel
        if coefficient is None or arts is None or panel is None:
            self.unresolved.add(f"Unbound physical damage: {eid.value}")
        else:
            scale = coefficient * (1 + arts / 100)
            if native_row:
                scalar = self.characters[actor].attributes.get("physical_infliction_damage_scalar")
                if scalar is None or not math.isfinite(scalar) or scalar <= 0:
                    self.unresolved.add("Missing native physical_infliction_damage_scalar")
                    scalar = 0
                scale *= scalar
            if not native_row and rule.scales_with_stacks:
                scale *= 1 + previous
            hit = DamageHit(actor, enemy, "物理", scale, panel.bonus_for("物理", ("physical_anomaly",)),
                            damage_tag="physical_anomaly", can_crit=native_row)
            from src.data.native_damage_processors import resolve_native_hit

            result = resolve_native_hit(self, panel, hit, inputs)
            if result.expected is None:
                self.unresolved.update(result.unknown)
            else:
                self.damage += result.expected
        if eid == EffectType.STATUS_SHATTER:
            duration = self.reaction_inputs.get(f"physical.STATUS_SHATTER.duration.{previous}",
                                                self.reaction_inputs.get("physical.STATUS_SHATTER.duration"))
            magnitude = self.reaction_inputs.get(f"physical.STATUS_SHATTER.damage_taken.{previous}")
            if duration is None or magnitude is None:
                self.unresolved.add("Unbound shatter lifetime/magnitude")
            else:
                from src.data.damage_modifiers import DamageBucket, ModifierMagnitude
                from src.data.native_reactions import reaction_enhancement

                if native_row:
                    magnitude *= 1 + reaction_enhancement("Debuff", arts)

                modifier = DamageModifierSpec("physical:shatter", DamageBucket.DAMAGE_TAKEN, ("物理",), "enemy",
                                              ModifierMagnitude(magnitude), "on_shatter", duration=duration)
                self.damage_state.apply(modifier, source_actor=actor, enemy=enemy, now=self.time, event="on_shatter")
        elif eid != EffectType.STATUS_HEAVY_STRIKE:
            duration = effect.duration if effect.duration is not None else inputs.get(f"effect.{eid.value}.duration")
            if duration is None:
                self.unresolved.add(f"Unknown lifetime: {eid.value}")
            else:
                self.effects[:] = [x for x in self.effects if not (x.effect == eid and x.owner == enemy)]
                self.effects.append(EffectInstance(eid, enemy, actor, 1, self.time + duration))
        self._events.append(eid.value)

    def register_character_hook(self, trigger, program):
        """CharacterData callbacks belong to their character, independent of a cast."""
        if program.actor not in self.characters or any(event.at != 0 for event in program.events):
            raise ValueError("Invalid immediate character event program")
        self.native_character_hooks.append((trigger, program))

    def dispatch_character_event(self, trigger, source_actor, target, buff_id):
        # Enemy buff events are visible to the squad's CharacterData conditions.
        # The callback source is its owning character; the triggering attacker
        # remains separate and must never steal the recipient's marker or BB.
        previous_action = self._executing_action
        try:
            for event_type, program in tuple(self.native_character_hooks):
                if event_type != trigger or not self.characters[program.actor].alive:
                    continue
                self._sequence += 1
                action_id = f"character_event:{self._sequence}:{program.actor}"
                self._action_inputs[action_id] = dict(program.parameters)
                self._action_targets[action_id] = {"current": (target,), "trigger": (target,)}
                enemy_target = target in self.enemies
                values = {
                    "target.trigger.count": 1.0,
                    "event.target_is_enemy": float(enemy_target),
                    "event.target_object_type": 16.0 if enemy_target else 8.0,
                    "event.source_is_owner": float(source_actor == program.actor),
                    f"event.buff_id.{buff_id}": 1.0,
                    **{f"event.buff_tag.{tag}": 1.0 for tag in self.native_buff_tags.get(buff_id, ())},
                }
                from dataclasses import replace

                for event in program.events:
                    self._sequence += 1
                    self._execute_event(action_id, self._sequence, program,
                                        replace(event, inputs=(*event.inputs, *values.items())))
        finally:
            self._executing_action = previous_action

    def dispatch_native(self, trigger, actor, payload=None, *, target=None):
        """Callbacks read the event's state before its producer consumes a pool."""
        previous_action = self._executing_action
        try:
            from src.data.native_buff_runtime import dispatch_buff_event

            dispatch_buff_event(self, trigger, actor, payload or {}, target)
            from src.data.native_passive_runtime import dispatch_passive_event

            dispatch_passive_event(self, trigger, actor, payload or {}, target)
            for owner, action_id, program, listener in tuple(self.native_listeners):
                if owner != actor or listener.trigger != trigger or (owner, listener.buff_id) not in self.native_buffs:
                    continue
                from dataclasses import replace

                from src.data.native_event_context import event_targets

                with event_targets(self, action_id, target):
                    for event in listener.events:
                        self._sequence += 1
                        invoked = replace(event, inputs=(*event.inputs, *(payload or {}).items()))
                        self._execute_event(action_id, self._sequence, program, invoked)
        finally:
            self._executing_action = previous_action

    def _resource_amount(self, actor, change, inputs):
        kind = change.kind
        if kind == ResourceChangeKind.FIXED:
            amount = change.amount
        else:
            units = inputs.get("hit_count") if kind == ResourceChangeKind.PER_HIT else None
            if change.source_effect_id is not None:
                subject = change.source_effect_id
                consumed = self._action_consumed.get(self._executing_action, {})
                units = consumed.get((actor, subject), 0) if change.count_basis == "consumed" else inputs.get(f"count.{subject.value}")
                if subject == EffectType.EVENT_SHRED_CONSUMED:
                    units = (inputs.get("trigger.EVENT_SHRED_CONSUMED") if change.count_basis == "trigger_event"
                             else consumed.get((actor, EffectType.STACK_SHRED), 0))
            if change.count_basis == "hit_targets":
                units = inputs.get("hit_targets")
            if kind == ResourceChangeKind.DYNAMIC:
                amount = inputs.get(f"resource.{change.resource.value}")
            elif units is None:
                amount = None
            elif kind == ResourceChangeKind.PIECEWISE_BY_COUNT:
                amount = change.values_by_count.get(int(units)) if units == int(units) else None
            else:
                if change.max_units is not None:
                    units = min(units, change.max_units)
                amount = units * change.per_unit if change.per_unit is not None else None
        if amount is None or not math.isfinite(amount):
            raise UnresolvedMechanic(f"Unbound {kind.value} {change.resource.value}: {change.formula or change.trigger}")
        return min(amount, change.max_amount) if change.max_amount is not None else amount

    def native_targets(self, selector, action_id, program):
        if selector.kind in {"source", "owner"}:
            return self._action_targets.get(action_id, {}).get(selector.kind, (program.actor,))
        if selector.kind == "main":
            return (self.main_control,) if self.main_control else ()
        if selector.kind == "squad":
            return tuple(self.characters)
        if selector.kind == "action_target":
            return self._action_targets.get(action_id, {}).get("current", (program.enemy,))
        if selector.kind in {"main_target", "enemy"}:
            return (program.enemy,)
        if selector.kind == "context":
            targets = self._action_targets.get(action_id, {})
            if selector.key not in targets:
                raise UnresolvedMechanic(f"Unbound native target group: {selector.key}")
            return targets[selector.key]
        raise UnresolvedMechanic(f"Unknown native target: {selector.kind}")

    def _credit_sp(self, actor, enemy, amount, *, returned=False):
        credited = min(amount, 300 - self.sp) if amount > 0 else 0
        self.sp_overflow += max(0, self.sp + amount - 300)
        self.sp = max(0, min(300, self.sp + amount))
        if returned:
            self.returned_sp += credited
        self.returned_sp = min(self.returned_sp, self.sp)
        state = self.characters[actor]
        recovery = state.attributes.get("sp_recovered", 0) + credited
        thresholds = [spec for spec in self.passive_modifiers.get(actor, ())
                      if spec.trigger == "own_skill_recovery_threshold_80"]
        if thresholds:
            while recovery >= 80:
                self.emit("own_skill_recovery_threshold_80", actor, enemy)
                recovery -= 80
        state.attributes["sp_recovered"] = recovery

    def _native_resource(self, action_id, program, change, inputs):
        sources = self.native_targets(change.source, action_id, program)
        if not sources or sources[0] not in self.characters:
            return
        source = sources[0]
        if change.only_main_source and source != self.main_control:
            return
        amount = change.amount.evaluate(inputs)
        coefficient = change.coefficient.evaluate(inputs)
        for recipient in self.native_targets(change.target, action_id, program):
            if recipient not in self.characters:
                continue  # Native ObtainCost accepts character recipients only.
            character = self.characters[recipient]
            value = amount
            if change.resource == CombatResourceType.SKILL_POINT:
                if change.percent:
                    raise UnresolvedMechanic("Native percent SP operation requires a proven policy")
                self._credit_sp(source, program.enemy, value * coefficient, returned=change.returned_sp)
                continue
            if change.default_energy is not None:
                value *= change.default_energy[0 if recipient == source else 1]
                value *= coefficient
            # CalculateUltimateSp ignores gain scalar for nonpositive base values.
            if value > 0 and not change.ignore_energy_gain:
                value *= 1 + character.attributes.get("energy_gain", 0)
            if change.percent:
                value *= character.energy_cap
            if change.default_energy is None:
                value *= coefficient
            character.energy = max(0, min(character.energy_cap, character.energy + value))

    def selected_native_skill(self, actor, slot):
        return self.native_skill_slots.get((actor, slot))

    def register_native_program(self, program, *, default=False):
        self.native_programs[program.actor, program.key] = program
        if default and program.native_slot is not None:
            self.native_skill_slots.setdefault((program.actor, program.native_slot), program.key)

    def selects_program(self, program):
        if program.native_slot is None:
            return True
        selected = self.selected_native_skill(program.actor, program.native_slot)
        return selected == program.key if selected is not None else not program.native_requires_override

    def end_native_scope(self, action_id, *, interrupted=False):
        for (actor, slot), override in tuple(self.native_skill_overrides.items()):
            if override.scope == action_id and override.ends_with_scope:
                self._revert_native_skill(actor, slot, override.uid)
        if interrupted:
            from src.data.native_buff_runtime import finish_action_buffs

            finish_action_buffs(self, action_id)

    def _native_cooldown_progress(self, actor, key):
        if key is None:
            return 1.0
        program = self.native_programs.get((actor, key))
        if program is None:
            raise UnresolvedMechanic(f"Unbound native replacement cooldown: {key}")
        remaining = max(0.0, self.cooldowns.get(program.cooldown_key or key, 0) - self.time)
        return max(0.0, min(1.0, 1 - remaining / program.cooldown)) if program.cooldown > 0 else 1.0

    def _set_native_cooldown_progress(self, actor, key, progress):
        program = self.native_programs.get((actor, key))
        if program is None:
            raise UnresolvedMechanic(f"Unbound native replacement cooldown: {key}")
        self.cooldowns[program.cooldown_key or key] = self.time + program.cooldown * (1 - progress)

    def _revert_native_skill(self, actor, slot, uid):
        identity = (actor, slot)
        override = self.native_skill_overrides.get(identity)
        if override is None or override.uid != uid:
            return
        current = self.native_skill_slots.get(identity)
        if current != override.reverted_skill:
            if override.inherit_cooldown and slot in {0, 1} and override.reverted_skill is not None:
                progress = self._native_cooldown_progress(actor, current)
                self._set_native_cooldown_progress(actor, override.reverted_skill, progress)
            if override.reverted_skill is None:
                self.native_skill_slots.pop(identity, None)
            else:
                self.native_skill_slots[identity] = override.reverted_skill
        del self.native_skill_overrides[identity]

    def _change_native_skill(self, action_id, program, change, inputs):
        if change.slot not in {0, 1, 2} or change.lifetime not in {0, 1, 2}:
            raise UnresolvedMechanic("Unknown native skill replacement slot/lifetime")
        sources = self.native_targets(change.source, action_id, program)
        if not sources or sources[0] not in self.characters:
            return
        actor = sources[0]
        duration = change.duration.evaluate(inputs) if change.lifetime == 0 else None
        identity = (actor, change.slot)
        reverted = change.reverted_skill or self.native_skill_slots.get(identity)
        if change.inherit_cooldown:
            if (actor, change.target_skill) not in self.native_programs or reverted is not None and (actor, reverted) not in self.native_programs:
                raise UnresolvedMechanic("Unbound native replacement cooldown programs")
        # Execute captures the requested reversion before ChangeSkill clears the
        # previous handle. Cooldown progress is read after that clear/reversion.
        previous = self.native_skill_overrides.get(identity)
        if previous is not None:
            self._revert_native_skill(actor, change.slot, previous.uid)
        progress = self._native_cooldown_progress(actor, self.native_skill_slots.get(identity)) if change.inherit_cooldown else None
        if progress is not None:
            self._set_native_cooldown_progress(actor, change.target_skill, progress)
        self.native_skill_slots[identity] = change.target_skill
        self._sequence += 1
        uid = f"skill:{self._sequence}"
        expires = self.time + duration if duration is not None and duration > 0 else None
        self.native_skill_overrides[identity] = NativeSkillOverride(uid, action_id, change.target_skill, expires,
                                                                  change.lifetime == 2, reverted, change.inherit_cooldown)
        if expires is not None:
            event = CombatEvent(0, "native_skill_reverted", skill_controls=(NativeSkillControl(actor, change.slot, uid),))
            heapq.heappush(self._queue, (expires, self._sequence, uid, program, event))

    def _apply_native_buff(self, owner, change, inputs, action_id, program):
        delta = change.count.evaluate(inputs)
        if delta != int(delta):
            raise UnresolvedMechanic(f"Non-integer native buff layers: {change.key}")
        if change.key.startswith("buff_common_energy_shard_attached_"):
            from src.data.native_spell_runtime import attachment_policies

            for key, element, _, _ in attachment_policies().values():
                if key != change.key:
                    continue
                if owner not in self.enemies:
                    raise UnresolvedMechanic("Native attachment buff requires enemy binding")
                if change.remove_all or delta < 0:
                    self.consume(program.actor, owner, element, None if change.remove_all else -int(delta))
                    return
                if delta == 0:
                    return
                raise UnresolvedMechanic("Native attachment creation must use spell transition producer")
        if delta > 0:
            self.native_buff_tags[change.key] = change.definition.tags if change.definition else change.tags
        identity = (owner, change.key)
        if change.definition is not None or any(v.owner == owner and v.key == change.key
                                                for v in self.native_buff_instances.values()):
            from src.data.native_buff_runtime import change_buff

            change_buff(self, owner, change, inputs, action_id, program, int(delta))
            return
        old_count, old_expiry = self.native_buffs.get(identity, (0, None))
        count = 0 if change.remove_all else max(0, old_count + int(delta))
        if change.maximum is not None:
            count = min(count, change.maximum)
        if count == 0:
            self.native_buffs.pop(identity, None)
            return
        if delta < 0:
            expires = old_expiry
        elif change.permanent:
            expires = None
        elif change.duration is not None:
            duration = change.duration.evaluate(inputs)
            if duration <= 0:
                raise UnresolvedMechanic(f"Invalid native buff lifetime: {change.key}")
            expires = self.time + duration
        else:
            raise UnresolvedMechanic(f"Missing native buff lifetime: {change.key}")
        self.native_buffs[identity] = (count, expires)

    def _execute_event(self, action_id, sequence, program, event):
        previous_action = self._executing_action
        try:
            self._execute_event_body(action_id, sequence, program, event)
        finally:
            self._executing_action = previous_action

    def _execute_event_body(self, action_id, sequence, program, event):
        if (action_id, sequence) in self._seen_events:
            return
        self._seen_events.add((action_id, sequence))
        if event.end_scope is not None:
            self.end_native_scope(event.end_scope)
            return
        if event.skill_controls:
            for control in event.skill_controls:
                self._revert_native_skill(control.actor, control.slot, control.uid)
            return
        if event.buff_controls:
            from src.data.native_buff_runtime import control_buff

            for control in event.buff_controls:
                control_buff(self, control)
            return
        actor, enemy = program.actor, program.enemy
        if not self.characters[actor].alive or not self.satisfies(actor, enemy, event.requires, event.any_requires):
            return
        self._executing_action = action_id
        needed = required_input_keys(self, event, program)
        inputs = {f"source.{key}": value for key, value in self.characters[actor].attributes.items()}
        inputs["source.is_main"] = float(actor == self.main_control)
        for key, effect in (("enemy.has_crystal", EffectType.STATUS_ORIGINIUM_CRYSTAL),
                            ("enemy.slowed", EffectType.STATUS_SLOW), ("enemy.staggered", EffectType.STATUS_STAGGER)):
            if needed is None or key in needed:
                inputs[key] = float(self.count(actor, enemy, effect) > 0)
        sources = self.native_targets(NativeTarget("source"), action_id, program)
        for key in tuple(inputs):
            if key.startswith("source."):
                del inputs[key]
        if len(sources) == 1 and sources[0] in self.characters:
            inputs.update({f"source.{key}": value for key, value in self.characters[sources[0]].attributes.items()})
            inputs["source.is_main"] = float(sources[0] == self.main_control)
        inputs.update(self._action_inputs.get(action_id, {}))
        inputs.update({"bb." + k: v for k, v in self.characters[actor].blackboard.items()})
        inputs.update(event.inputs)
        for group, targets in self._action_targets.get(action_id, {}).items():
            inputs[f"target.{group}.count"] = float(len(targets))
        for subject in EffectType:
            if needed is not None and f"count.{subject.value}" not in needed:
                continue
            inputs[f"count.{subject.value}"] = self.count(actor, enemy, subject)
        for buff_id in program.native_buff_ids:
            for owner, prefix in ((actor, "self"), (enemy, "enemy")):
                key = f"native.{prefix}.{buff_id}"
                if needed is None or key in needed:
                    inputs[key] = self.native_buff_count(owner, buff_id)
        for query in program.native_buff_queries:
            if needed is not None and query.key not in needed:
                continue
            try:
                owners = self.native_targets(query.target, action_id, program)
                if query.tag_mode is not None:
                    # Native tag count obtains one AbilitySystem. Multi-target
                    # selection precedence is not inferred by summing recipients.
                    if len(owners) > 1:
                        raise UnresolvedMechanic("Unbound native multi-target tag count selection")
                    from src.data.native_tags import matches_tags

                    ids = tuple(k for k, tags in self.native_buff_tags.items()
                                if matches_tags(tags, query.tag_mode, query.tags))
                else:
                    ids = query.buff_ids
                counts = [self.native_buff_count(owner, buff_id)
                          for owner in owners for buff_id in ids]
                inputs[query.key] = sum(counts) if query.count_type == 0 else sum(c > 0 for c in counts)
            except UnresolvedMechanic:
                # A later branch may not read this group; only an evaluated
                # expression should reject the branch for its missing input.
                inputs.pop(query.key, None)
        for query in program.native_attribute_queries:
            if needed is not None and query.key not in needed:
                continue
            # Resolve at callback time; Source and Owner may differ for a buff.
            # An unused branch may legitimately have no attribute recipient.
            inputs.pop(query.key, None)
            try:
                owners = self.native_targets(query.target, action_id, program)
                if len(owners) != 1 or owners[0] not in self.characters:
                    continue
                value = (float(owners[0] == self.main_control) if query.attribute == "is_main"
                         else self.characters[owners[0]].attributes.get(query.attribute))
                if isinstance(value, (int, float)) and math.isfinite(value):
                    inputs[query.key] = float(value)
            except UnresolvedMechanic:
                pass
        for timer_id in program.native_timer_ids:
            key = f"timer.{timer_id}.ready"
            if needed is None or key in needed:
                inputs[key] = float(self.native_timers.get((actor, timer_id), 0) <= self.time)
        inputs["consumed.STACK_SHRED"] = self._action_consumed.get(action_id, {}).get((actor, EffectType.STACK_SHRED), 0)
        try:
            if event.condition is not None and not event.condition.evaluate(inputs):
                self._executing_action = None
                return
            for key, expression in event.assignments:
                value = expression.evaluate(inputs)
                inputs[key] = value
                if key.startswith("bb.EntityBB_"):
                    self.characters[actor].blackboard[key[3:]] = value
                else:
                    self._action_inputs[action_id][key] = value
        except MissingCombatInput as error:
            # A failed producer must invalidate its previous/default output.
            # Otherwise a later DamageAction can read a stale native BB value.
            for key, _ in event.assignments:
                self._action_inputs[action_id].pop(key, None)
                if key.startswith("bb.EntityBB_"):
                    self.characters[actor].blackboard.pop(key[3:], None)
            self.unresolved.add(str(error))
            self._executing_action = None
            return
        self.unresolved.update(event.unresolved)
        for change in event.skill_changes:
            try:
                self._change_native_skill(action_id, program, change, inputs)
            except (UnresolvedMechanic, MissingCombatInput) as error:
                self.unresolved.add(str(error))
        for binding in event.target_bindings:
            try:
                targets = tuple(dict.fromkeys(target for selector in binding.selectors
                                               for target in self.native_targets(selector, action_id, program)))
                self._action_targets.setdefault(action_id, {})[binding.key] = targets
                self._action_inputs[action_id][f"target.{binding.key}.count"] = float(len(targets))
            except UnresolvedMechanic as error:
                self.unresolved.add(str(error))
        for iteration in event.iterations:
            try:
                targets = self.native_targets(iteration.target, action_id, program)
                context = self._action_targets.setdefault(action_id, {})
                previous = context.get("current")
                try:
                    for target in targets:
                        context["current"] = (target,)
                        for callback in iteration.events:
                            self._sequence += 1
                            self._execute_event(action_id, self._sequence, program, callback)
                finally:
                    if previous is None:
                        context.pop("current", None)
                    else:
                        context["current"] = previous
            except UnresolvedMechanic as error:
                self.unresolved.add(str(error))
        for key, expression in event.timers:
            try:
                duration = expression.evaluate(inputs)
                if duration < 0:
                    raise UnresolvedMechanic(f"Invalid native timer: {key}")
                self.native_timers[actor, key] = self.time + duration
            except MissingCombatInput as error:
                self.unresolved.add(str(error))
        for listener in event.listeners:
            identity = (actor, action_id, program, listener)
            self.native_listeners[:] = [old for old in self.native_listeners
                                       if (old[0], old[3].buff_id, old[3].trigger) != (actor, listener.buff_id, listener.trigger)]
            self.native_listeners.append(identity)
        if event.field_id and event.field_duration is not None:
            self.damage_state.spawn_field(event.field_id, source_actor=actor, now=self.time, duration=event.field_duration)
        if event.field_id and event.remove_field:
            self.damage_state.remove_field(event.field_id)
        for change in event.native_spells:
            try:
                from src.data.native_spell_runtime import apply_native_spell

                apply_native_spell(self, action_id, program, change, inputs)
            except UnresolvedMechanic as error:
                self.unresolved.add(str(error))
        for spell_type in event.native_spell_bursts:
            try:
                from src.data.native_spell_runtime import dispatch_native_burst

                dispatch_native_burst(self, action_id, program, spell_type, inputs)
            except UnresolvedMechanic as error:
                self.unresolved.add(str(error))
        for change in event.native_buffs:
            try:
                owners = self.native_targets(change.selector, action_id, program) if change.selector is not None else (
                    enemy if change.target == "enemy" else actor,)
                for owner in owners:
                    self._apply_native_buff(owner, change, inputs, action_id, program)
            except (UnresolvedMechanic, MissingCombatInput) as error:
                self.unresolved.add(str(error))
        # Conditions snapshot before mutation; explicit consumers precede the event's
        # damage, then producers follow it, enabling actual-consumption followups.
        consumers = [e for e in event.effects if (e.count is not None and e.count < 0) or EFFECT_SEMANTICS[e.effect_id].kind == EffectKind.OPERATION]
        for effect in consumers:
            try:
                self.apply_effect(actor, enemy, effect, inputs)
            except UnresolvedMechanic as error:
                self.unresolved.add(str(error))
        for (consumer, subject), amount in self._action_consumed.get(action_id, {}).items():
            if consumer == actor:
                inputs[f"consumed.{subject.value}"] = amount
        inputs["consumed.STACK_SHRED"] = self._action_consumed.get(action_id, {}).get((actor, EffectType.STACK_SHRED), 0)
        inputs["count.EVENT_SHRED_CONSUMED"] = inputs["consumed.STACK_SHRED"]
        if event.hit is not None:
            current = self._action_targets.get(action_id, {}).get("current", (enemy,))
            if len(current) != 1 or current[0] not in self.enemies:
                self.unresolved.add("Damage target requires enemy binding")
                return
            enemy = current[0]
            hit_actor = actor
            if event.hit_source is not None:
                try:
                    attackers = self.native_targets(event.hit_source, action_id, program)
                except UnresolvedMechanic as error:
                    self.unresolved.add(str(error))
                    return
                if len(attackers) != 1 or attackers[0] not in self.characters:
                    self.unresolved.add("Native damage attacker requires a single character")
                    return
                hit_actor = attackers[0]
            panel = self.characters[hit_actor].panel
            if panel is None:
                self.unresolved.add(f"Missing fixed panel: {hit_actor}")
            else:
                hit = event.hit
                if hit.enemy != enemy or event.hit_source is not None:
                    from dataclasses import replace

                    hit = replace(hit, actor=hit_actor, enemy=enemy,
                                  damage_bonus=panel.bonus_for(hit.element, hit.damage_tags)
                                  if event.hit_source is not None else hit.damage_bonus)
                if event.hit_source is not None:
                    for key in tuple(inputs):
                        if key.startswith("source."):
                            del inputs[key]
                    inputs.update({key: value for key, value in self.damage_inputs(hit_actor, enemy).items()
                                   if key.startswith("source.")})
                if event.hit_multiplier_formula is not None:
                    from dataclasses import replace

                    try:
                        hit = replace(hit, multiplier=event.hit_multiplier_formula.evaluate(inputs))
                    except MissingCombatInput as error:
                        self.unresolved.add(str(error))
                        hit = replace(hit, multiplier=0)
                if event.hit_multiplier_input:
                    from dataclasses import replace

                    multiplier = inputs.get(event.hit_multiplier_input)
                    if multiplier is None or not math.isfinite(multiplier):
                        self.unresolved.add(f"Unbound damage multiplier: {event.hit_multiplier_input}")
                        multiplier = 0
                    hit = replace(hit, multiplier=multiplier)
                from src.data.native_damage_processors import resolve_native_hit

                result = resolve_native_hit(self, panel, hit, inputs)
                if result.expected is None:
                    self.unresolved.update(result.unknown)
                else:
                    self.damage += result.expected
            if program.kind == "ult":
                self.emit("ultimate_hit", hit_actor, enemy, inputs)
            elif program.kind == "link" and (
                inputs.get("count.ATTACH_COLD", 0) > 0 or inputs.get("count.STATUS_FROZEN", 0) > 0
            ):
                self.emit("combo_hit_cold_attached_or_frozen", hit_actor, enemy, inputs)
        for effect in event.effects:
            if effect not in consumers:
                try:
                    self.apply_effect(actor, enemy, effect, inputs)
                except UnresolvedMechanic as error:
                    self.unresolved.add(str(error))
        for modifier in event.modifiers:
            self.damage_state.apply(modifier, source_actor=actor, enemy=enemy, now=self.time, event=event.name, inputs=inputs, field_id=event.field_id)
        for change in event.native_resources:
            try:
                self._native_resource(action_id, program, change, inputs)
            except (UnresolvedMechanic, MissingCombatInput) as error:
                self.unresolved.add(str(error))
        if event.resource_formulas and len(event.resource_formulas) != len(event.resources):
            raise ValueError("Each resource change needs one matching formula slot")
        for index, change in enumerate(event.resources):
            try:
                formula = event.resource_formulas[index] if event.resource_formulas else None
                amount = formula.evaluate(inputs) if formula is not None else self._resource_amount(actor, change, inputs)
                if change.max_amount is not None:
                    amount = min(amount, change.max_amount)
                if change.resource == CombatResourceType.SKILL_POINT:
                    self._credit_sp(actor, enemy, amount)
                else:
                    recipients = self.characters if change.target == "team" else (actor,)
                    for recipient in recipients:
                        character = self.characters[recipient]
                        value = amount * (1 + character.attributes.get("energy_gain", 0)) if amount > 0 and change.affected_by_energy_gain else amount
                        character.energy = max(0, min(character.energy_cap, character.energy + value))
            except (UnresolvedMechanic, MissingCombatInput) as error:
                self.unresolved.add(str(error))
        self._events.append(event.name)
        self._executing_action = None

    def start(self, program: ActionProgram, *, action_id: str):
        """Commit cast cost once; gameplay events can finish after the next actor starts."""
        if action_id in self._started_ids:
            return False
        actor = self.characters[program.actor]
        gate = program.sp_cost if program.gate is None else max(program.sp_cost, program.gate)
        if not all(math.isfinite(v) and v >= 0 for v in (program.sp_cost, program.energy_cost, program.duration, program.cooldown, gate)):
            raise ValueError("Invalid action parameters")
        if not actor.alive or self.sp < gate or actor.energy < program.energy_cost:
            return False
        if not self.selects_program(program):
            return False
        cooldown_key = program.cooldown_key or program.key
        if self.time < self.ready_at(program):
            return False
        if not self.satisfies(program.actor, program.enemy, program.requires, program.any_requires):
            return False
        if any(self.satisfies(program.actor, program.enemy, (r,)) for r in program.forbids):
            return False
        if any(not math.isfinite(e.at) or e.at < 0 for e in program.events):
            raise ValueError("Invalid event timestamp")
        self._seen_events.add((action_id, -1))
        self._started_ids.add(action_id)
        self._actions_started += 1
        self._action_inputs[action_id] = {
            f"trigger.{effect.value}": self.count(program.actor, program.enemy, effect)
            for effect in EffectType
        }
        self._action_inputs[action_id].update(program.parameters)
        returned_used = min(self.returned_sp, program.sp_cost)
        self.returned_sp -= returned_used
        self._action_inputs[action_id]["cast.non_returned_sp"] = program.sp_cost - returned_used
        self._action_targets[action_id] = {"current": (program.enemy,)}
        self._action_inputs[action_id]["event.skill_type"] = {"battle": 2.0, "link": 6.0, "ult": 7.0, "normal": 0.0}[program.kind]
        self._consumed = {key: v for key, v in self._consumed.items() if key[0] != program.actor}
        for enemy in self.enemies.values():
            enemy.clear_transient_events()
        self.sp -= program.sp_cost
        actor.energy -= program.energy_cost
        self.cooldowns[cooldown_key] = self.time + program.cooldown
        self.actor_ready[program.actor] = self.time + (program.duration if program.actor_lock is None else program.actor_lock)
        previous = self.active_actions.get(program.actor)
        if previous:
            self.end_native_scope(previous[0], interrupted=True)
            self._queue[:] = [row for row in self._queue if row[2] != previous[0] or row[4].persists_after_interrupt]
            heapq.heapify(self._queue)
        self.active_actions[program.actor] = (action_id, program, self.time)
        if program.kind == "battle":
            self.emit("battle_cast", program.actor, program.enemy)
        for event in program.events:
            self._sequence += 1
            heapq.heappush(self._queue, (self.time + event.at, self._sequence, action_id, program, event))
        self._sequence += 1
        heapq.heappush(self._queue, (self.time + program.duration, self._sequence, action_id, program,
                                   CombatEvent(0, "native_action_finished", end_scope=action_id)))
        self.advance(self.time)
        return True

    def ready_at(self, program):
        ready = self.actor_ready.get(program.actor, 0)
        previous = self.active_actions.get(program.actor)
        if previous:
            _, active, start = previous
            for begin, end, allowed in active.next_action_windows:
                if program.key in allowed and self.time <= start + end:
                    ready = min(ready, max(self.time, start + begin))
        return max(self.time, self.cooldowns.get(program.cooldown_key or program.key, 0), ready)

    def simulate(self, program: ActionProgram, *, strict=True) -> tuple[CombatWorldState, ActionOutcome] | None:
        """Counterfactual evaluation leaves the caller's state completely unchanged."""
        result = self.fork()
        before = result.snapshot()
        previous_damage, previous_overflow = result.damage, result.sp_overflow
        previous_events = len(result._events)
        action_id = f"simulate:{result._actions_started}:{program.key}"
        if not result.start(program, action_id=action_id):
            return None
        result.advance(before.time + program.duration)
        unknown = tuple(sorted(result.unresolved))
        if strict and unknown:
            return None
        outcome = ActionOutcome(
            program.key, before, result.snapshot(), result.damage - previous_damage,
            tuple(sorted((e.value, n) for (a, e), n in result._action_consumed.get(action_id, {}).items() if a == program.actor)),
            tuple(result._events[previous_events:]), result.sp_overflow - previous_overflow, unknown,
        )
        return result, outcome

    def observe_resources(self, *, sp: float | None = None, energy: dict[str, float] | None = None):
        """HUD observations replace predictions; they are not additional resource payouts."""
        if sp is not None and math.isfinite(sp) and 0 <= sp <= 300:
            self.sp = sp
            self.returned_sp = min(self.returned_sp, self.sp)
        for actor, amount in (energy or {}).items():
            if actor in self.characters and math.isfinite(amount) and amount >= 0:
                self.characters[actor].energy = min(self.characters[actor].energy_cap, amount)

    def disable_actor(self, actor):
        self.characters[actor].alive = False
        self._queue[:] = [row for row in self._queue if row[3].actor != actor or row[4].buff_controls
                         or row[4].skill_controls or row[4].end_scope is not None]
        if any(instance.source == actor or instance.owner == actor for instance in self.native_buff_instances.values()):
            self.unresolved.add("Native buff death policy needs binding")
        heapq.heapify(self._queue)


@dataclass(frozen=True)
class MechanismPlan(ImmutableCombatValue):
    actions: tuple[str, ...]
    damage: float
    seconds: float
    ending_sp: float
    overflow: float
    outcomes: tuple[ActionOutcome, ...]


def plan_action_sequence(world: CombatWorldState, programs: tuple[ActionProgram, ...], *, depth=4, horizon=20.0,
                         beam_width=128, max_expansions=None, timeout=None):
    """Compare complete resource/state transitions, including waits and producer value.

    Resource and unlock value are realized by subsequent legal actions, not assigned
    a fictitious SP-to-damage exchange rate. Unsupported outcomes cannot win a plan.
    """
    if not 1 <= depth <= 8 or not math.isfinite(horizon) or horizon <= 0 or beam_width < 1:
        raise ValueError("Invalid search bounds")
    if (max_expansions is not None and (not isinstance(max_expansions, int) or max_expansions < 1)
            or timeout is not None and (not math.isfinite(timeout) or timeout <= 0)):
        raise ValueError("Invalid runtime search budget")
    deadline = None if timeout is None else time.monotonic() + timeout
    expansions = 0

    def check_time():
        if deadline is not None and time.monotonic() >= deadline:
            raise CombatSearchLimit("Combat search time budget exceeded")

    initial = world.time
    frontier = [(world.fork(), ())]
    best = None
    for _ in range(depth):
        next_frontier = []
        for state, outcomes in frontier:
            for program in programs:
                check_time()
                expansions += 1
                if max_expansions is not None and expansions > max_expansions:
                    raise CombatSearchLimit("Combat search expansion budget exceeded")
                candidate = state.fork()
                wait = candidate.ready_at(program)
                gate = max(program.sp_cost, program.gate or 0)
                if candidate.sp < gate:
                    if candidate.regen <= 0:
                        continue
                    wait = max(wait, candidate.time + (gate - candidate.sp) / candidate.regen)
                if wait - initial >= horizon:
                    continue
                candidate.advance(wait)
                executed = candidate.simulate(program)
                check_time()
                if executed is None:
                    continue
                after, outcome = executed
                if after.time - initial > horizon:
                    continue
                path = (*outcomes, outcome)
                tail = after.fork()
                if any(v.expires is None and v.action_finish_at is None and v.period > 0 and v.remaining != 0
                       for v in tail.native_buff_instances.values()):
                    tail.advance(initial + horizon)
                elif tail._queue:
                    tail.advance(min(initial + horizon, max(row[0] for row in tail._queue)))
                check_time()
                if tail.unresolved:
                    continue
                plan = MechanismPlan(tuple(o.program for o in path), tail.damage - world.damage, tail.time - initial, tail.sp,
                                     sum(o.sp_overflow for o in path), path)
                if best is None or (plan.damage, -plan.seconds, plan.ending_sp) > (best.damage, -best.seconds, best.ending_sp):
                    best = plan
                next_frontier.append((after, path))
        # A state key includes resources, pending hits, buffs and unlocks. Keep one
        # best path per identical state before the explicitly bounded beam search.
        distinct = {}
        for state, path in next_frontier:
            snapshot = state.snapshot()
            identity = (snapshot.time, snapshot.sp, snapshot.energy, snapshot.effects, snapshot.cooldowns,
                        tuple(sorted(state.actor_ready.items())), tuple((a, s.alive) for a, s in state.characters.items()),
                        tuple(sorted((a, p.key, at) for a, (_, p, at) in state.active_actions.items())),
                        state.damage_state.phase_signature(state.time),
                        state.main_control, state.returned_sp, tuple(sorted(state.native_buffs.items())),
                        tuple(sorted(state.native_skill_slots.items())), tuple(sorted(state.native_skill_overrides.items())),
                        tuple((uid, repr(passive), tuple(sorted(state._action_inputs[uid].items())))
                              for uid, passive in state.native_passives.items()),
                        tuple((v.owner, v.key, v.source, v.expires, v.period, v.remaining,
                               v.action_scope, v.action_finish_at, repr(v.definition),
                               tuple(sorted(state._action_inputs[v.uid].items()))) for v in state.native_buff_instances.values()),
                        tuple(sorted(state.native_timers.items())),
                        tuple(sorted((a, tuple(sorted(s.attributes.items()))) for a, s in state.characters.items())),
                        tuple(sorted((a, tuple(sorted(s.blackboard.items()))) for a, s in state.characters.items())),
                        tuple((a, action, p.key, repr(listener), tuple(sorted(state._action_inputs.get(action, {}).items())))
                              for a, action, p, listener in state.native_listeners),
                        tuple((row[0], row[3].key, repr(row[4]), tuple(sorted(state._action_inputs.get(row[2], {}).items())),
                               tuple(sorted(state._action_targets.get(row[2], {}).items())),
                               tuple(sorted((a, e.value, n) for (a, e), n in state._action_consumed.get(row[2], {}).items())))
                              for row in state._queue))
            previous = distinct.get(identity)
            if previous is None or state.damage > previous[0].damage:
                distinct[identity] = (state, path)
        # Reserve one path per first action so a low-damage producer survives the
        # first cut until the search can realize its unlocked consumer's value.
        grouped = {}
        for state, path in distinct.values():
            first = path[0].program
            grouped.setdefault(first, []).append((state, path))
        for group in grouped.values():
            group.sort(key=lambda item: (-item[0].damage, -item[0].sp, item[0].time))
        frontier = []
        while grouped and len(frontier) < beam_width:
            for first in tuple(grouped):
                frontier.append(grouped[first].pop(0))
                if not grouped[first]:
                    del grouped[first]
                if len(frontier) == beam_width:
                    break
    check_time()
    return best
