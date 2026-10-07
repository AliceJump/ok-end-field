"""Persistent Ability roots outlive casts and slot mapping changes."""

import heapq
from dataclasses import dataclass

from src.data.combat_simulation import UnresolvedMechanic


@dataclass
class NativeAbilityInstance:
    uid: str
    actor: str
    skill: str
    enabled: bool = True


def enable_ability(world, actor, skill):
    identity = (actor, skill)
    instance = world.native_abilities.get(identity)
    if instance is None:
        instance = NativeAbilityInstance(f"ability:{actor}:{skill}", actor, skill)
        world.native_abilities[identity] = instance
    else:
        instance.enabled = True
    return instance


def bind_ability_scope(world, scope, program):
    enable_ability(world, program.actor, program.key)
    world._native_action_abilities[scope] = (program.actor, program.key)


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
    for passive in world.native_passives.values():
        if (passive.program.actor, passive.program.key) == identity and any(
            event.name == "native_passive_skill_buffs" for event in passive.program.events
        ):
            world.unresolved.add(f"Native Ability passive buff cleanup not yet bound: {skill}")
    from src.data.native_buff_runtime import finish_parent_buffs

    finish_parent_buffs(world, instance.uid)
    return True
