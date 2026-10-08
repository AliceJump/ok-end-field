"""Post-add notifications for two proven plain self markers, not generic AddBuff."""

from functools import lru_cache

from src.data.combat_expressions import CombatExpression
from src.data.native_zhuangfy_evidence import MARKER, read_snapshot

MARKERS = {MARKER, MARKER + "_mark"}
PRODUCERS = {
    MARKER: {"chr_0030_zhuangfy_normal_skill", "chr_0030_zhuangfy_normal_skill_ult"},
    MARKER + "_mark": {"chr_0030_zhuangfy_normal_skill_ult_abilityrange"},
}
PRE_ADD = {"OnBeforeAddedBuff", "OnBeforeOutputBuff"}


def notification_binding():
    return {"scope": "confirmed_raw_plain_self_marker_fragment_only",
            "producers": {key: sorted(values) for key, values in PRODUCERS.items()},
            "events": ["OnAddedBuff", "OnOutputBuff"],
            "full_producer": "not_bound",
            "excluded": ["pre-add callbacks", "Unique/Refresh/null-result notifications",
                         "full cast-info identity", "EnhancedAction/mark count/attribute propagation"]}


def _has_pre_add_listener(world, actor):
    # These hooks can change an AddBuff request before the reviewed result.
    # Do not silently skip them and then publish an apparently confirmed add.
    return (any(row.owner == actor and any(kind in PRE_ADD for kind, _ in row.definition.subscriptions)
                for row in world.native_buff_instances.values())
            or any(row.program.actor == actor and any(kind in PRE_ADD for kind, _ in row.subscriptions)
                   for row in world.native_passives.values())
            or any(owner == actor and listener.trigger in PRE_ADD and (owner, listener.buff_id) in world.native_buffs
                   for owner, _, _, listener in world.native_listeners))


@lru_cache(maxsize=1)
def _durations():
    records = read_snapshot()
    result = {}
    for key in MARKERS:
        data = records[key]["data"]
        if (data["lifeType"] != 0 or data["duration"]["useBlackboardKey"]
                or data["blackboard"] or data["applyTags"] or data["buffEventAction"] or data["abilityEventAction"]
                or data["stackingSettings"]["stackingType"] != 0):
            raise ValueError("Reviewed native marker notification shape changed")
        result[key] = CombatExpression("literal", (data["duration"]["value"],))
    return result


def publish_marker_added(world, instance, action_id, inputs):
    if instance.key not in MARKERS:
        return
    definition = instance.definition
    if (instance.program.key not in PRODUCERS[instance.key]
            or instance.source != instance.owner or instance.parent_scope is not None or instance.action_scope is not None
            or definition.duration != _durations()[instance.key] or definition.parameters or definition.inherited
            or definition.stacking != 0 or definition.stacking_key is not None
            or definition.callbacks or definition.subscriptions or definition.tags
            or definition.damage_scales or definition.attack_additions or instance.period > 0):
        world.unresolved.add(f"Native marker add notification needs reviewed plain self shape: {instance.key}")
        return
    if _has_pre_add_listener(world, instance.owner):
        world.unresolved.add(f"Native marker add notification needs pre-add callback execution: {instance.key}")
        return
    payload = {"event.buff_context": 1.0, "event.buff_data_available": 1.0,
               f"event.buff_id.{instance.key}": 1.0}
    skill_type = inputs.get("event.skill_type", world._action_inputs.get(action_id, {}).get("event.skill_type"))
    if skill_type is not None:
        payload["event.skill_type"] = skill_type
    # Current CreateBuff publishes recipient before source. These Unlimited
    # plain markers always have a new object; Unique/Refresh/null-result and
    # mutable pre-add processing are deliberately outside this binding.
    world.dispatch_native("OnAddedBuff", instance.owner, payload, target=instance.owner)
    world.dispatch_native("OnOutputBuff", instance.source, payload, target=instance.owner)
