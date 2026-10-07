"""Selected native talent/potential producers have persistent, separate scopes."""

from dataclasses import dataclass, replace

from src.data.combat_expressions import CombatExpression
from src.data.combat_simulation import ActionProgram, CombatEvent, NativeBuffChange, NativeTarget, UnresolvedMechanic
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.native_action_program import compile_native_action
from src.data.native_buff_program import compile_buff_definition
from src.data.native_gameplay import native_enums, native_record


@dataclass(frozen=True)
class NativePassiveProgram(ImmutableCombatValue):
    key: str
    source: str
    program: ActionProgram
    subscriptions: tuple[tuple[str, ActionProgram], ...] = ()


def _reference(key, values):
    items = []
    for row in values:
        if row["valueStr"]:
            raise UnresolvedMechanic(f"Non-numeric attached producer blackboard: {key}/{row['key']}")
        items.append({"directValueType": 0, "targetKey": row["key"], "numericValue": row["value"],
                      "useDirectValue": True, "inputValueKey": "", "stringValue": ""})
    return {"buffId": key, "assignBlackboard": bool(items), "assignItems": items}


def _buff(store, character, profile, actor, reference, *, attributes, panel):
    key = reference["buffId"]
    data = native_record(store, key)["data"]
    definition = compile_buff_definition(store, character, profile, actor, key, data, reference,
                                         attributes=attributes, panel=panel, path=())
    return NativeBuffChange(key, CombatExpression("literal", (1.0,)), selector=NativeTarget("owner"), definition=definition)


def compile_passive_producers(store, character, actor, profile, *, attributes, panel):
    """Only the selected highest talent ranks and actual potential levels attach."""
    programs, diagnostics = [], []
    event_names = {v["value"]: k for k, v in native_enums()["Beyond.Gameplay.Core.AbilitySystem+Event"].items()}
    modify_types = native_enums()["Beyond.GEnums.PotentialModifyType"]
    for passive in (*character.progression.talents, *character.progression.active_potentials):
        for index, modifier in enumerate(passive.modifiers):
            buff, skill = modifier.get("attachBuff"), modifier.get("attachSkill")
            if buff is None and skill is None:
                continue
            try:
                if modifier["activeCondition"]:
                    raise UnresolvedMechanic(f"Unbound attached producer condition: {passive.effect_id}/{modifier['activeCondition']}")
                identity = f"{passive.effect_id}:{index}"
                if buff is not None:
                    if modifier["modifyType"] != modify_types["AddBuff"]["value"] or skill is not None:
                        raise UnresolvedMechanic(f"Invalid native AddBuff modifier: {passive.effect_id}")
                    change = _buff(store, character, profile, actor, _reference(buff["buffId"], buff["blackboard"]),
                                   attributes=attributes, panel=panel)
                    program = ActionProgram(buff["buffId"], actor, "normal", 0, 0, 0,
                                            (CombatEvent(0, "native_passive_attached", native_buffs=(change,)),))
                    programs.append(NativePassiveProgram(identity, passive.source, program))
                else:
                    if modifier["modifyType"] != modify_types["AddPassiveSkill"]["value"] or skill["skillPath"]:
                        raise UnresolvedMechanic(f"Unbound native AddPassiveSkill modifier: {passive.effect_id}")
                    key = skill["skillId"]
                    data = native_record(store, key)["data"]
                    timing = store.profile(key)
                    values = {r["key"]: r["value"] for r in skill["blackboard"] if not r["valueStr"]}
                    if len(values) != len(skill["blackboard"]):
                        raise UnresolvedMechanic(f"Non-numeric attached skill blackboard: {key}")
                    # Native SkillData creation, source blackboard assignment
                    # and runtime refresh have separate precedence rules. Do
                    # not choose an order for overlapping producer/modifier keys
                    # until that complete chain has been verified.
                    overlaps = {m["skillBbModifier"]["bbKey"]
                                for p in (*character.progression.talents, *character.progression.active_potentials)
                                for m in p.modifiers if m.get("skillBbModifier", {}).get("skillId") == key} & values.keys()
                    if overlaps:
                        raise UnresolvedMechanic(f"Unverified attached skill blackboard precedence: {key}/{sorted(overlaps)}")
                    program = compile_native_action(store, character, timing, actor, "normal", attributes=attributes,
                                                    panel=panel, initial_blackboard=values)
                    # These are Ability.Enable subscriptions, not an unresolved
                    # time-zero action and not a second player-triggered cast.
                    events = tuple(e for e in program.events if e.name != "unresolved_native" or
                                   e.unresolved != (f"Native passive timeline events: {key}",))
                    subscriptions = []
                    for entry in data["actionGroupData"]["passiveEventActions"]:
                        trigger = event_names.get(entry["abilityEvent"])
                        if trigger is None:
                            raise UnresolvedMechanic(f"Unknown passive skill event: {key}/{entry['abilityEvent']}")
                        for sequence in entry["actions"]:
                            callback = compile_native_action(store, character, timing, actor, "normal", attributes=attributes,
                                                             panel=panel, event_sequence=sequence, initial_blackboard=values)
                            subscriptions.append((trigger, callback))
                    if data["passiveSkillType"] != 0:
                        raise UnresolvedMechanic(f"Native toggle passive needs binding: {key}")
                    # Skill.Create selects ToggleBuffPassiveSkill only for type
                    # 1. A type-0 record may retain inactive toggleBuffs defaults.
                    buffs = tuple(_buff(store, character, timing, actor, reference, attributes=attributes, panel=panel)
                                  for reference in data["buffs"])
                    if buffs:
                        events = (CombatEvent(0, "native_passive_skill_buffs", native_buffs=buffs), *events)
                    programs.append(NativePassiveProgram(identity, passive.source,
                                                         replace(program, events=events), tuple(subscriptions)))
            except (KeyError, ValueError) as error:
                diagnostics.append(f"{character.name}/{passive.effect_id}: {error}")
    return tuple(programs), tuple(diagnostics)
