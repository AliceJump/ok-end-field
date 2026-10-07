"""Persistent Ability roots outlive casts and slot mapping changes."""

import heapq
from dataclasses import dataclass, field

from src.data.combat_simulation import CombatEvent, UnresolvedMechanic


@dataclass
class NativeAbilityInstance:
    uid: str
    actor: str
    skill: str
    enabled: bool = True
    passive_scope: str | None = None
    passive_buffs: list[str] = field(default_factory=list)
    producer_enabled_once: bool = False


def _attach_passive_buffs(world, instance):
    if instance.passive_scope is None:
        return
    passive = world.native_passives[instance.passive_scope]
    if instance.producer_enabled_once and passive.program.events:
        world.unresolved.add(f"Native passive timeline restart needs binding: {instance.skill}")
    instance.producer_enabled_once = True
    if passive.ability_buffs:
        world._sequence += 1
        world._execute_event(instance.passive_scope, world._sequence, passive.program,
                             CombatEvent(0, "native_passive_skill_buffs", native_buffs=passive.ability_buffs))


def enable_ability(world, actor, skill):
    identity = (actor, skill)
    instance = world.native_abilities.get(identity)
    if instance is None:
        instance = NativeAbilityInstance(f"ability:{actor}:{skill}", actor, skill, enabled=False)
        world.native_abilities[identity] = instance
    if not instance.enabled:
        instance.enabled = True
        _attach_passive_buffs(world, instance)
    return instance


def bind_ability_scope(world, scope, program):
    from src.data.native_skill_runtime import bind_skill_object

    bind_skill_object(world, program.actor, program.key)
    world._native_skill_casts[program.actor, program.key] = scope
    world._native_action_abilities[scope] = (program.actor, program.key)
    enable_ability(world, program.actor, program.key)


def bind_passive_ability(world, scope, passive):
    identity = (passive.program.actor, passive.ability_skill)
    instance = world.native_abilities.get(identity)
    if instance is None:
        instance = NativeAbilityInstance(f"ability:{identity[0]}:{identity[1]}", *identity, enabled=False)
        world.native_abilities[identity] = instance
    if instance.passive_scope is not None and instance.passive_scope != scope:
        world.unresolved.add(f"Multiple native passive Skill producers need binding: {identity[1]}")
        return False
    world._native_action_abilities[scope] = identity
    instance.passive_scope = scope
    if instance.enabled:
        # A catalog root may be enabled before its selected producer is bound.
        # Attach that producer once; future casts/Enable calls stay idempotent.
        _attach_passive_buffs(world, instance)
    else:
        enable_ability(world, *identity)
    return True


def executing_ability(world, scope):
    identity = world._native_action_abilities.get(scope)
    instance = world.native_abilities.get(identity)
    if instance is None or not instance.enabled:
        raise UnresolvedMechanic("Child buff lacks its enabled Ability root")
    return instance


def disable_ability(world, actor, skill):
    """Apply a confirmed Disable, never infer it from CastEnd or a slot change."""
    instance = world.native_abilities.get((actor, skill))
    if instance is None or not instance.enabled:
        return False
    instance.enabled = False
    # UnRegisterActions removes this Ability from the action container. Old
    # timeline work cannot resume if the same object is enabled again later.
    identity = (actor, skill)
    world._queue[:] = [row for row in world._queue if world._native_action_abilities.get(row[2]) != identity]
    heapq.heapify(world._queue)
    from src.data.native_buff_runtime import finish_buff_instances, finish_parent_buffs

    finish_buff_instances(world, tuple(instance.passive_buffs))
    instance.passive_buffs.clear()
    finish_parent_buffs(world, instance.uid)
    return True
