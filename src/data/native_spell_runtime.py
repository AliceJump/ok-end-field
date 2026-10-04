"""Enemy spell attachment producers with native target and buff identities."""

from functools import lru_cache

from src.data.combat_simulation import UnresolvedMechanic
from src.data.effects import EffectType
from src.data.native_gameplay import native_number, native_record
from src.data.native_tags import expand_tags, tag_hash

# SpellInfliction.ExecuteInternal and AbilitySystemUtils.GetSpellStatusBuff
# use these four integer branches. Buff pair names are NEW then CONSUMED.
SPELL_ELEMENTS = {
    0: ("fire", EffectType.ATTACH_BURN),
    1: ("pulse", EffectType.ATTACH_ELECTROMAGNETIC),
    2: ("cryst", EffectType.ATTACH_COLD),
    3: ("natural", EffectType.ATTACH_NATURAL),
}


@lru_cache(maxsize=1)
def attachment_policies():
    from src.data.skill_timing import load_skill_timings

    store = load_skill_timings()
    result = {}
    for number, (name, effect) in SPELL_ELEMENTS.items():
        key = f"buff_common_energy_shard_attached_{name}"
        data = native_record(store, key)["data"]
        values = {r["key"]: r["valueDouble"] for r in data["blackboard"] if not r["valueStr"]}
        duration = native_number(data["duration"], values)
        stacking = data["stackingSettings"]
        if duration is None or duration <= 0 or stacking["stackingType"] != 8 or stacking["maxStackCnt"] != 4:
            raise ValueError(f"Unknown native attachment policy: {key}")
        tags = expand_tags(data["applyTags"])
        tag_element = {0: "Fire", 1: "Pulse", 2: "Cryst", 3: "Natural"}[number]
        if tag_hash(f"Skill/Character/Common/SpellInflict/{tag_element}Inflict") not in tags:
            raise ValueError(f"Native attachment recipient/tag mismatch: {key}")
        result[number] = (key, effect, duration, tags)
    return result


def attachment_count(world, owner, key):
    """Read the canonical pool, including consumption and expiry, without a copy."""
    for buff_id, element, _, _ in attachment_policies().values():
        if key == buff_id:
            state = world.enemies.get(owner)
            return state.infliction_stacks if state is not None and state.infliction_element == element else 0
    return None


def apply_native_spell(world, action_id, program, change, inputs):
    sources = world.native_targets(change.source, action_id, program)
    targets = world.native_targets(change.target, action_id, program)
    if len(sources) != 1 or sources[0] not in world.characters:
        raise UnresolvedMechanic("Native spell source requires a single character")
    if any(target not in world.enemies for target in targets):
        raise UnresolvedMechanic("Native enemy spell recipient requires enemy binding")
    if change.infliction_type not in SPELL_ELEMENTS:
        raise UnresolvedMechanic(f"Unknown native spell infliction type: {change.infliction_type}")
    source = sources[0]
    policies = attachment_policies()
    for key, _, _, tags in policies.values():
        world.native_buff_tags[key] = tags
    key, element, duration, _ = policies[change.infliction_type]
    for target in targets:
        state = world.enemies[target]
        payload = {"event.spell_type": float(change.infliction_type), "event.is_extra": float(change.is_extra),
                   "event.skill_type": inputs.get("event.skill_type", 0)}
        # The native action sends both before events before its buff transition,
        # then both after events. Listeners receive this action's actual target.
        world.dispatch_native("OnCharBeforeOutputSpellInfliction", source, payload, target=target)
        world.dispatch_native("OnEnemyBeforeTakeSpellInfliction", target, payload, target=target)
        if state.infliction_element is None or state.infliction_element == element:
            world.dispatch_character_event("OnBeforeAddedBuff", source, target, key)
        reaction = state.apply_infliction(element, duration=duration)
        world.unresolved.add("Native spell attachment enhancement callbacks not yet bound")
        if reaction is not None:
            world._events.append(reaction.value)
            # Native reaction callbacks, their immunity gates and damage policy
            # are separate producers. Missing damage must keep plans unresolved.
            world.unresolved.add(f"Unbound spell reaction: {reaction.value}")
        world.dispatch_native("OnCharAfterOutputSpellInfliction", source, payload, target=target)
        world.dispatch_native("OnEnemyAfterTakeSpellInfliction", target, payload, target=target)
