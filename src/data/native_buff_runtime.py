"""Scoped native buff instances, independent deadlines and explicit callbacks."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, replace

from src.data.combat_simulation import CombatEvent, NativeBuffControl, NativeBuffProgram, UnresolvedMechanic
from src.data.native_event_context import event_targets


@dataclass
class NativeBuffInstance:
    uid: str
    key: str
    owner: str
    source: str
    definition: NativeBuffProgram
    program: object
    expires: float | None
    period: float
    remaining: int
    next_trigger: float | None = None
    action_scope: str | None = None
    action_finish_at: float | None = None
    parent_scope: str | None = None
    finishing: bool = False
    inherit_skill_ids: tuple[str, ...] = ()
    finish_with_next_skill: bool = False


def _instances(world, owner, key):
    return [v for v in world.native_buff_instances.values() if v.owner == owner and v.key == key]


def _stacking_group(world, owner, key, definition):
    identity = definition.stacking_key if definition.stacking_key is not None else key
    return [v for v in world.native_buff_instances.values() if v.owner == owner
            and (v.definition.stacking_key if v.definition.stacking_key is not None else v.key) == identity]


def _sync(world, owner, key):
    instances = _instances(world, owner, key)
    if not instances:
        world.native_buffs.pop((owner, key), None)
    else:
        expires = None if any(v.expires is None for v in instances) else max(v.expires for v in instances)
        world.native_buffs[owner, key] = (len(instances), expires)


def _schedule(world, instance, at, kind):
    if not math.isfinite(at) or at < world.time:
        raise UnresolvedMechanic("Invalid native buff event time")
    world._sequence += 1
    if kind == "trigger":
        instance.next_trigger = at
    event = CombatEvent(0, "native_buff_" + kind, buff_controls=(NativeBuffControl(instance.uid, kind, at),))
    heapq.heappush(world._queue, (at, world._sequence, instance.uid, instance.program, event))


def _execute(world, instance, program, payload=None, target=None):
    for key, value in program.parameters:
        world._action_inputs[instance.uid].setdefault(key, value)
    previous = world._native_buff_context
    world._native_buff_context = instance
    try:
        with event_targets(world, instance.uid, target):
            for event in program.events:
                world._sequence += 1
                world._execute_event(instance.uid, world._sequence, program,
                                     replace(event, inputs=(*event.inputs, *(payload or {}).items())))
    finally:
        world._native_buff_context = previous


def _callbacks(world, instance, event_type):
    for kind, program in instance.definition.callbacks:
        if kind == event_type:
            _execute(world, instance, program)


def _finish(world, instance):
    if instance.uid not in world.native_buff_instances:
        return
    # Mark as finished before callbacks so recursive removal is idempotent.
    del world.native_buff_instances[instance.uid]
    instance.finishing = True
    world.end_native_scope(instance.uid)
    _sync(world, instance.owner, instance.key)
    try:
        # MarkFinish runs OnFinish before removing the parent's children. The
        # callback still has its root, including for children created on finish.
        _callbacks(world, instance, 2)
    finally:
        try:
            for child in tuple(world.native_buff_instances.values()):
                if child.parent_scope == instance.uid:
                    _finish(world, child)
        finally:
            instance.finishing = False


def finish_action_buffs(world, action_id, *, reason=None, next_skill=None):
    for instance in tuple(world.native_buff_instances.values()):
        if instance.action_scope == action_id:
            _end_action_buff(world, instance, reason=reason, next_skill=next_skill)


def _end_action_buff(world, instance, *, reason=None, next_skill=None):
    if reason == 7 and next_skill in instance.inherit_skill_ids:
        from src.data.native_skill_runtime import attach_skill_buffs

        if not instance.finish_with_next_skill or attach_skill_buffs(world, instance.source, next_skill, (instance.uid,)):
            instance.action_scope = None
            instance.action_finish_at = None
            return
    _finish(world, instance)


def finish_parent_buffs(world, parent_uid):
    for instance in tuple(world.native_buff_instances.values()):
        if instance.parent_scope == parent_uid:
            _finish(world, instance)


def finish_buff_instances(world, instance_ids):
    for uid in instance_ids:
        instance = world.native_buff_instances.get(uid)
        if instance is not None:
            _finish(world, instance)


def _trigger(world, instance):
    if instance.remaining == 0:
        return
    instance.remaining -= 1
    _callbacks(world, instance, 1)


def change_buff(world, owner, change, inputs, action_id, program, delta):
    existing = _instances(world, owner, change.key)
    if change.remove_all or delta < 0:
        for instance in existing if change.remove_all else existing[: -delta]:
            _finish(world, instance)
        return
    definition = change.definition
    if definition is None:
        raise UnresolvedMechanic(f"Buff addition lacks instance definition: {change.key}")
    parent = world._native_buff_context if change.child_of_buff else None
    if change.child_of_buff and (parent is None or parent.uid != action_id
                                or parent.uid not in world.native_buff_instances and not parent.finishing):
        raise UnresolvedMechanic(f"Child buff lacks its executing Buff root: {change.key}")
    if change.child_of_ability:
        from src.data.native_ability_runtime import executing_ability

        if change.child_of_buff or world._native_buff_context is not None:
            raise UnresolvedMechanic(f"Conflicting child buff root: {change.key}")
        parent = executing_ability(world, action_id)
    passive_root = None
    if change.passive_of_ability:
        from src.data.native_ability_runtime import executing_ability

        if parent is not None or world._native_buff_context is not None:
            raise UnresolvedMechanic(f"Conflicting Ability passive buff root: {change.key}")
        passive_root = executing_ability(world, action_id)
        if owner != passive_root.actor or program.actor != passive_root.actor or delta != 1:
            raise UnresolvedMechanic(f"Invalid Ability passive buff recipient/count: {change.key}")
    if change.action_finish_after is not None and (
        not math.isfinite(change.action_finish_after) or change.action_finish_after < 0
    ):
        raise UnresolvedMechanic(f"Invalid native buff action deadline: {change.key}")
    if change.inherit_skill_ids and (change.action_finish_after is None or parent is not None or passive_root is not None):
        raise UnresolvedMechanic(f"Skill inheritance lacks an independent action-bound instance: {change.key}")
    values = dict(definition.parameters)
    values.update({key: expression.evaluate(inputs) for key, expression in definition.inherited})
    duration = definition.duration.evaluate(values) if definition.duration is not None else None
    period = definition.period.evaluate(values)
    if parent is not None and (not math.isfinite(period) or period > 0):
        raise UnresolvedMechanic(f"Parent-bound periodic buff needs tick/end ordering: {change.key}")
    limit = definition.trigger_limit.evaluate(values)
    # Unique and Unlimited do not read maxStackCnt; stale keys may be absent.
    maximum = definition.maximum.evaluate(values) if definition.stacking == 2 else 0
    if duration is not None and duration < 0 or limit != int(limit) or maximum != int(maximum):
        raise UnresolvedMechanic(f"Invalid native buff parameters: {change.key}")
    if definition.stacking not in {0, 2, 7}:
        raise UnresolvedMechanic(f"Native buff stacking policy not yet bound: {change.key}/{definition.stacking}")
    group = _stacking_group(world, owner, change.key, definition)
    if any(v.definition.stacking != definition.stacking or v.definition.maximum != definition.maximum for v in group):
        raise UnresolvedMechanic(f"Native shared stacking group has conflicting policies: {change.key}")
    world.unresolved.update(definition.unresolved)
    world.native_buff_tags[change.key] = definition.tags
    for _ in range(delta):
        existing = _stacking_group(world, owner, change.key, definition)
        if definition.stacking == 7 and existing:
            # Native Unique returns no created instance on reapplication, so
            # CreateBuff does not call SetBuffParent again for the old instance.
            continue
        if definition.stacking == 2 and maximum > 0 and len(existing) >= maximum:
            _finish(world, existing[0])
        world._sequence += 1
        uid = f"buff:{world._sequence}:{owner}:{change.key}"
        expires = world.time + duration if duration is not None else None
        instance = NativeBuffInstance(uid, change.key, owner, program.actor, definition, program, expires, period, int(limit))
        if parent is not None:
            instance.parent_scope = parent.uid
        if change.action_finish_after is not None:
            instance.action_scope = action_id
            instance.action_finish_at = world.time + change.action_finish_after
            instance.inherit_skill_ids = change.inherit_skill_ids
            instance.finish_with_next_skill = change.finish_with_next_skill
        world.native_buff_instances[uid] = instance
        world._action_inputs[uid] = dict(values)
        world._action_inputs[uid]["cast.non_returned_sp"] = world._action_inputs.get(action_id, {}).get("cast.non_returned_sp", 0)
        world._action_inputs[uid]["event.skill_type"] = inputs.get("event.skill_type", 0)
        world._action_targets[uid] = {"current": (owner,), "owner": (owner,), "source": (program.actor,)}
        _sync(world, owner, change.key)
        _callbacks(world, instance, 0)
        _callbacks(world, instance, 3)
        _callbacks(world, instance, 5)
        if uid not in world.native_buff_instances:
            continue
        if passive_root is not None:
            # _AddPassiveBuff stores only valid AddBuff results after creation.
            # Unique reapplication creates no wrapper and never joins this list.
            passive_root.passive_buffs.append(uid)
        if period > 0 and limit != 0:
            if not definition.wait_first:
                _trigger(world, instance)
            if instance.remaining != 0 and uid in world.native_buff_instances and (expires is None or world.time + period <= expires):
                _schedule(world, instance, world.time + period, "trigger")
        if expires is not None:
            _schedule(world, instance, expires, "finish")
        if instance.action_finish_at is not None:
            _schedule(world, instance, instance.action_finish_at, "action_finish")


def control_buff(world, control):
    instance = world.native_buff_instances.get(control.instance)
    if instance is None:
        return
    if control.kind == "action_finish":
        if instance.action_finish_at == control.expected_at:
            _finish(world, instance)
    elif control.kind == "finish":
        if instance.expires == control.expected_at:
            # Native OnTick updates its trigger timer before lifetime expiry.
            # A trigger exactly at the deadline precedes OnBuffFinish once.
            if instance.next_trigger == world.time and instance.remaining != 0:
                _trigger(world, instance)
            _finish(world, instance)
    elif control.kind == "trigger":
        if instance.expires is not None and world.time > instance.expires:
            return
        _trigger(world, instance)
        if instance.uid in world.native_buff_instances and instance.remaining != 0:
            at = world.time + instance.period
            if instance.expires is None or at <= instance.expires:
                _schedule(world, instance, at, "trigger")
    else:
        raise UnresolvedMechanic(f"Unknown native buff clock event: {control.kind}")


def dispatch_buff_event(world, trigger, actor, payload, target=None):
    for instance in tuple(world.native_buff_instances.values()):
        if instance.owner != actor:
            continue
        for kind, program in instance.definition.subscriptions:
            if kind == trigger:
                _execute(world, instance, program, payload, target)
