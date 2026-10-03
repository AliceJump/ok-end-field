"""Scoped native buff instances, independent deadlines and explicit callbacks."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, replace

from src.data.combat_simulation import CombatEvent, NativeBuffControl, NativeBuffProgram, UnresolvedMechanic


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


def _instances(world, owner, key):
    return [v for v in world.native_buff_instances.values() if v.owner == owner and v.key == key]


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


def _execute(world, instance, program, payload=None):
    for key, value in program.parameters:
        world._action_inputs[instance.uid].setdefault(key, value)
    for event in program.events:
        world._sequence += 1
        world._execute_event(instance.uid, world._sequence, program,
                             replace(event, inputs=(*event.inputs, *(payload or {}).items())))


def _callbacks(world, instance, event_type):
    for kind, program in instance.definition.callbacks:
        if kind == event_type:
            _execute(world, instance, program)


def _finish(world, instance):
    if instance.uid not in world.native_buff_instances:
        return
    # Mark as finished before callbacks so recursive removal is idempotent.
    del world.native_buff_instances[instance.uid]
    world.end_native_scope(instance.uid)
    _sync(world, instance.owner, instance.key)
    _callbacks(world, instance, 2)


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
    values = dict(definition.parameters)
    values.update({key: expression.evaluate(inputs) for key, expression in definition.inherited})
    duration = definition.duration.evaluate(values) if definition.duration is not None else None
    period = definition.period.evaluate(values)
    limit = definition.trigger_limit.evaluate(values)
    maximum = definition.maximum.evaluate(values)
    if duration is not None and duration < 0 or limit != int(limit) or maximum != int(maximum):
        raise UnresolvedMechanic(f"Invalid native buff parameters: {change.key}")
    if definition.stacking not in {0, 2, 7}:
        raise UnresolvedMechanic(f"Native buff stacking policy not yet bound: {change.key}/{definition.stacking}")
    world.unresolved.update(definition.unresolved)
    for _ in range(delta):
        existing = _instances(world, owner, change.key)
        if definition.stacking == 7 and existing:
            continue
        if definition.stacking == 2 and maximum > 0 and len(existing) >= maximum:
            _finish(world, existing[0])
        world._sequence += 1
        uid = f"buff:{world._sequence}:{owner}:{change.key}"
        expires = world.time + duration if duration is not None else None
        instance = NativeBuffInstance(uid, change.key, owner, program.actor, definition, program, expires, period, int(limit))
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
        if period > 0 and limit != 0:
            if not definition.wait_first:
                _trigger(world, instance)
            if instance.remaining != 0 and uid in world.native_buff_instances and (expires is None or world.time + period <= expires):
                _schedule(world, instance, world.time + period, "trigger")
        if expires is not None:
            _schedule(world, instance, expires, "finish")


def control_buff(world, control):
    instance = world.native_buff_instances.get(control.instance)
    if instance is None:
        return
    if control.kind == "finish":
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


def dispatch_buff_event(world, trigger, actor, payload):
    for instance in tuple(world.native_buff_instances.values()):
        if instance.owner != actor:
            continue
        for kind, program in instance.definition.subscriptions:
            if kind == trigger:
                _execute(world, instance, program, payload)
