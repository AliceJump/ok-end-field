"""Skill.AttachBuff and cleanup for an explicitly confirmed native CastEnd.

Scheduler handoff and starting another program are not CastEnd evidence. The
compiler keeps inheritance unresolved until a producer supplies that context.
"""

import heapq


def bind_skill_object(world, actor, skill):
    """Record a resolved object in this actor's activeSkillMap, without Enable."""
    if actor not in world.characters or not skill:
        raise ValueError("Invalid native Skill object identity")
    world.native_active_skills.add((actor, skill))


def attach_skill_buffs(world, actor, skill, instance_ids):
    # A known active Skill object is required; a slot ID or matching name alone
    # does not authorize creating/enabling the target during transfer.
    identity = (actor, skill)
    if not skill or identity not in world.native_active_skills:
        return False
    attached = world.native_skill_buffs.setdefault(identity, [])
    attached.extend(uid for uid in instance_ids if uid in world.native_buff_instances)
    return True


def finish_skill_cast(world, action_id, *, reason, next_skill=None):
    """Apply the buff portion of a confirmed CastEnd, preserving Ability roots."""
    identity = world._native_action_abilities.get(action_id)
    if identity is None or action_id not in world._started_ids:
        world.unresolved.add(f"Native CastEnd lacks its started Skill scope: {action_id}")
        return False
    if action_id in world._native_confirmed_cast_ends:
        return False
    if world._native_skill_casts.get(identity) != action_id:
        world.unresolved.add(f"Native CastEnd refers to a superseded Skill cast: {action_id}")
        return False
    if not isinstance(reason, int) or isinstance(reason, bool) or reason < 0:
        raise ValueError("Invalid native CastEnd reason")
    world._native_confirmed_cast_ends.add(action_id)
    # Skill.CastEnd snapshots its attached list before ending timeline actions;
    # newly attached instances (including self-transfer) are not in this batch.
    batch = tuple(world.native_skill_buffs.get(identity, ()))
    from src.data.native_buff_runtime import finish_action_buffs, finish_buff_instances

    finish_action_buffs(world, action_id, reason=reason, next_skill=next_skill)
    world.end_native_scope(action_id)
    world._queue[:] = [row for row in world._queue if row[2] != action_id or row[4].persists_after_interrupt]
    heapq.heapify(world._queue)
    finish_buff_instances(world, batch)
    if identity in world.native_skill_buffs:
        world.native_skill_buffs[identity][:] = [uid for uid in world.native_skill_buffs[identity] if uid not in batch]
    if world.active_actions.get(identity[0], (None,))[0] == action_id:
        del world.active_actions[identity[0]]
    return True
