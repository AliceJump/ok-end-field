"""Bind immediate CharacterData conditions without inventing combo eligibility."""

from src.data.native_action_program import compile_native_action
from src.data.native_gameplay import native_asset, native_enums, native_record


def _dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dictionaries(child)


def bind_character_events(world, store, character, profile, actor):
    """Only fully bound immediate sequences may produce a character's markers.

    These hooks preserve the native pre-consumption snapshot. Combo UI priority,
    pending-state acceptance and cast availability remain separate concerns.
    """
    native_id = profile.skill_id.split("_combo_skill", 1)[0]
    asset = native_asset("data_" + native_id)
    abilities = next(v for v in _dictionaries(asset) if "comboSkillConditions" in v)
    events = {row["value"]: key for key, row in native_enums()["Beyond.Gameplay.Core.AbilitySystem+Event"].items()}
    diagnostics = []
    for condition in abilities["comboSkillConditions"]:
        trigger = events.get(condition["comboSkillEvent"])
        if trigger is None or condition["comboSkillConditionImmediately"]:
            diagnostics.append(f"Unbound immediate combo check scheduling: {native_id}/{trigger}")
            continue
        bb = {row["key"]: row["valueDouble"] for row in abilities["comboSkillBlackboard"]
              if not row["valueStr"]}
        program = compile_native_action(store, character, profile, actor, "link",
                                        attributes=world.characters[actor].attributes,
                                        panel=world.characters[actor].panel,
                                        event_sequence=condition["comboSkillCheckAction"], event_blackboard=bb)
        errors = tuple(error for event in program.events for error in event.unresolved)
        if errors:
            diagnostics.extend(errors)
            continue
        world.register_character_hook(trigger, program)
    for buff_id in ("buff_physical_crushed", "buff_physical_do_fracture"):
        data = native_record(store, buff_id)["data"]
        world.native_buff_tags[buff_id] = tuple(int.from_bytes(bytes.fromhex(t["raw"]), "little", signed=True)
                                               for t in data["applyTags"])
    return tuple(diagnostics)
