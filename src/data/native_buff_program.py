"""Compile BuffData lifecycle actions into their own blackboard scope."""

from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import NativeBuffProgram, UnresolvedMechanic
from src.data.native_gameplay import native_enums
from src.data.native_tags import expand_tags

POGRANI_MORALE = "buff_chr_0029_pograni_talent1"
LAEVAT_STANCE = "buff_chr_0016_laevat_show_weapon"
LAEVAT_ULT_END = "buff_chr_0016_laevat_ult_end"
LAEVAT_DURATION_BUFFS = frozenset((LAEVAT_STANCE, LAEVAT_ULT_END))
YVONNE_CRIT_LAYERS = "buff_chr_0017_yvonne_ultimate_skill_layer"


def reviewed_enhance_refresh(buff_id, data):
    """Approve Yvonne's one-object layer container, not crit attribute evaluation."""
    return (buff_id == YVONNE_CRIT_LAYERS and data["id"] == buff_id
            and data["stackingSettings"] == {
                "identifierType": 0, "isNeedStackEffect": True, "maxStackCnt": 10,
                "maxStackCntKey": "", "negatePriority": False, "priority": 0.0,
                "priorityKey": "", "stackEffects": [], "stackingKey": "", "stackingType": 8,
                "useMaxStackCntKey": False, "usePriorityKey": False}
            and data["lifeType"] == 0 and not data["hasAddingCooldown"]
            and data["duration"] == {"blackboardKey": "recycle_time", "useBlackboardKey": True, "value": 2.0}
            and data["triggerInterval"] == {"blackboardKey": "", "useBlackboardKey": False, "value": -1.0}
            and not data["useTimeDilationDt"] and not data["onlyUseSelfTimeDilation"]
            and not data["timelineActions"] and not data["igniteEventAction"] and not data["abilityEventAction"]
            and [event["buffEvent"] for event in data["buffEventAction"]] == [6])


def reviewed_duration_extend(buff_id, data):
    """Approve this container's type5 arithmetic, not its full stance producer."""
    return (buff_id in LAEVAT_DURATION_BUFFS and data["id"] == buff_id
            and data["stackingSettings"] == {
                "identifierType": 0, "isNeedStackEffect": False, "maxStackCnt": 1,
                "maxStackCntKey": "", "negatePriority": False, "priority": 0.0,
                "priorityKey": "", "stackEffects": [], "stackingKey": "", "stackingType": 5,
                "useMaxStackCntKey": False, "usePriorityKey": False}
            and data["lifeType"] == 0 and not data["hasAddingCooldown"]
            and not data["useTimeDilationDt"] and not data["onlyUseSelfTimeDilation"]
            and not data["timelineActions"] and not data["igniteEventAction"])


def reviewed_retained_layers(buff_id, data):
    """Only the reviewed nonperiodic, constant-priority morale container.

    This approves retention and selection, not its attribute modifiers or SP
    producer. In particular, priority-key updates and callbacks remain unbound.
    """
    return (buff_id == POGRANI_MORALE and data["id"] == buff_id
            and data["stackingSettings"] == {
                "identifierType": 0, "isNeedStackEffect": False, "maxStackCnt": -1,
                "maxStackCntKey": "max_stack", "negatePriority": False, "priority": 0.0,
                "priorityKey": "", "stackEffects": [], "stackingKey": "", "stackingType": 11,
                "useMaxStackCntKey": True, "usePriorityKey": False}
            and data["lifeType"] == 0 and not data["hasAddingCooldown"]
            and not data["useTimeDilationDt"] and not data["onlyUseSelfTimeDilation"]
            and data["duration"] == {"blackboardKey": "duration", "useBlackboardKey": True, "value": 20.0}
            and data["triggerInterval"] == {"blackboardKey": "", "useBlackboardKey": False, "value": -1.0}
            and not any(data[key] for key in ("buffEventAction", "abilityEventAction", "timelineActions",
                                             "igniteEventAction", "applyTags", "tagsAfterTriggerExtendBuffAction")))


def compile_buff_definition(store, character, profile, actor, buff_id, data, reference, *, attributes, panel, path):
    from src.data.native_action_program import compile_native_action
    from src.data.native_attribute_modifiers import compile_attack_additions
    from src.data.native_damage_processors import compile_damage_processors

    if buff_id in path or len(path) >= 8:
        raise UnresolvedMechanic(f"Native recursive buff producer needs event binding: {buff_id}")
    parameters = {row["key"]: row["valueDouble"] for row in data["blackboard"] if not row["valueStr"]}
    inherited = []
    if reference["assignBlackboard"]:
        for item in reference["assignItems"]:
            if item["directValueType"] != 0:
                raise UnresolvedMechanic(f"Non-numeric buff inheritance: {buff_id}/{item['targetKey']}")
            value = (CombatExpression("literal", (float(item["numericValue"]),)) if item["useDirectValue"]
                     else combat_input("bb." + item["inputValueKey"]))
            inherited.append(("bb." + item["targetKey"], value))

    def number(value):
        if value["useBlackboardKey"]:
            return combat_input("bb." + value["blackboardKey"])
        return CombatExpression("literal", (float(value["value"]),))

    def compile_blocks(blocks):
        # Each authored block retains its own source guard and query namespace.
        programs = [compile_native_action(store, character, profile, actor, "normal", attributes=attributes, panel=panel,
                                         event_sequence=block, event_blackboard=parameters, isolated_blackboard=True,
                                         buff_path=(*path, buff_id)) for block in blocks]
        return programs

    callbacks, subscriptions, unresolved = [], [], []
    if (buff_id == LAEVAT_STANCE and reviewed_duration_extend(buff_id, data) and data["duration"]["useBlackboardKey"]
            and not any(key == "bb." + data["duration"]["blackboardKey"] for key, _ in inherited)):
        # The raw fallback is 16; the ranked ultimate input is 15. The implicit
        # cast-info BB bridge is not proved by matching these two key names.
        unresolved.append(f"Native stance duration blackboard transfer needs binding: {buff_id}")
    stacking = data["stackingSettings"]
    if stacking["identifierType"] not in {0, 1}:
        raise UnresolvedMechanic(f"Unknown native buff stacking identity: {buff_id}/{stacking['identifierType']}")
    stacking_key = stacking["stackingKey"] if stacking["identifierType"] == 1 else None
    if stacking_key == "":
        raise UnresolvedMechanic(f"Empty native shared buff stacking key: {buff_id}")
    if stacking["usePriorityKey"] or stacking["priority"] != 0 or stacking["negatePriority"]:
        unresolved.append(f"Native buff stacking priority not yet bound: {buff_id}")
    for event in data["buffEventAction"]:
        programs = compile_blocks(event["actions"]) or ()
        if event["buffEvent"] not in ({0, 1, 2, 3, 5, 6} if reviewed_enhance_refresh(buff_id, data) else {0, 1, 2, 3, 5}):
            if any(program.events for program in programs):
                unresolved.append(f"Native buff lifecycle event not yet bound: {buff_id}/{event['buffEvent']}")
            continue
        callbacks.extend((event["buffEvent"], program) for program in programs)
    event_names = {v["value"]: k for k, v in native_enums()["Beyond.Gameplay.Core.AbilitySystem+Event"].items()}
    for event in data["abilityEventAction"]:
        trigger = event_names.get(event["abilityEvent"])
        if trigger is None:
            unresolved.append(f"Unknown native buff subscription: {buff_id}/{event['abilityEvent']}")
            continue
        subscriptions.extend((trigger, program) for program in (compile_blocks(event["actions"]) or ()))
    if data["timelineActions"] or data["igniteEventAction"]:
        unresolved.append(f"Native buff timeline/ignite scheduling not yet bound: {buff_id}")
    damage_scales, diagnostics = compile_damage_processors(data, buff_id)
    unresolved.extend(diagnostics)
    attack_additions, attributes_bound = compile_attack_additions(buff_id, data)
    for key in ("attributeModifier", "healModifier", "globalModifier", "poiseModifier", "shieldConfigs"):
        value = data[key]["attributeModifiers"] if key == "attributeModifier" else data[key]
        if value and not (key == "attributeModifier" and attributes_bound):
            unresolved.append(f"Native buff modifier needs binding: {buff_id}/{key}")
    return NativeBuffProgram(tuple(("bb." + k, float(v)) for k, v in parameters.items()), tuple(inherited),
                             None if data["lifeType"] == 1 else number(data["duration"]), number(data["triggerInterval"]),
                             number(data["maxTriggerCnt"]), data["waitFirstTriggerInterval"],
                             data["stackingSettings"]["stackingType"],
                             combat_input("bb." + data["stackingSettings"]["maxStackCntKey"])
                             if data["stackingSettings"]["useMaxStackCntKey"] else
                             CombatExpression("literal", (float(data["stackingSettings"]["maxStackCnt"]),)),
                             tuple(callbacks), tuple(subscriptions), tuple(unresolved), expand_tags(data["applyTags"]),
                             stacking_key, damage_scales, attack_additions, reviewed_retained_layers(buff_id, data),
                             reviewed_duration_extend(buff_id, data), reviewed_enhance_refresh(buff_id, data))
