"""Activate persistent producers without spending a cast or advancing phases."""

import heapq
from dataclasses import replace

from src.data.native_event_context import event_targets


def activate_passive(world, passive):
    actor = passive.program.actor
    uid = f"passive:{actor}:{passive.key}"
    if uid in world.native_passives:
        return False
    world.native_passives[uid] = passive
    world._action_inputs[uid] = dict(passive.program.parameters)
    world._action_inputs[uid]["cast.non_returned_sp"] = 0.0
    world._action_targets[uid] = {"current": (actor,), "source": (actor,), "owner": (actor,)}
    if passive.program.key.startswith("chr_"):
        from src.data.native_ability_runtime import bind_ability_scope

        bind_ability_scope(world, uid, passive.program)
    for event in passive.program.events:
        world._sequence += 1
        heapq.heappush(world._queue, (world.time + event.at, world._sequence, uid, passive.program, event))
    world.advance(world.time)
    return True


def dispatch_passive_event(world, trigger, actor, payload, target=None):
    for uid, passive in tuple(world.native_passives.items()):
        if passive.program.actor != actor or not world.characters[actor].alive:
            continue
        identity = world._native_action_abilities.get(uid)
        ability = world.native_abilities.get(identity)
        if ability is not None and not ability.enabled:
            continue
        for event_type, program in passive.subscriptions:
            if trigger != event_type:
                continue
            for key, value in program.parameters:
                world._action_inputs[uid].setdefault(key, value)
            with event_targets(world, uid, target):
                for event in program.events:
                    world._sequence += 1
                    world._execute_event(uid, world._sequence, program, replace(event, inputs=(*event.inputs, *payload.items())))
