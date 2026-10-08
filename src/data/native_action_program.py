"""Compile proven native timeline primitives without flattening conditional arms.

Unsupported gameplay remains in ActionOutcome.unresolved. Such programs can track
known transitions after a confirmed cast but cannot win a damage plan.
"""

from __future__ import annotations

import math
from dataclasses import replace

from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    NativeAttributeQuery,
    NativeBuffChange,
    NativeBuffQuery,
    NativeIteration,
    NativeListener,
    NativeResourceChange,
    NativeSkillChange,
    NativeSpellInfliction,
    NativeTarget,
    NativeTargetBinding,
    UnresolvedMechanic,
)
from src.data.damage_resolution import DamageHit
from src.data.effects import EffectType
from src.data.native_combat_parameters import bind_native_parameters
from src.data.native_gameplay import native_asset, native_enums, native_number, native_record
from src.data.native_tags import expand_tags, tag_query
from src.data.skill_types import CombatResourceType, SkillEffect

_DAMAGE_ELEMENTS = {0: "物理", 2: "灼热", 3: "电磁", 4: "寒冷", 6: "自然"}
_PHYSICAL = {
    "KnockDownAction+Data": EffectType.STATUS_KNOCKDOWN,
    "AirborneAction+AirborneActionData": EffectType.STATUS_HEAVY_HIT,
    "CrushAction+Data": EffectType.STATUS_HEAVY_STRIKE,
    "BreachAction+Data": EffectType.STATUS_SHATTER,
    "FractureAction+Data": EffectType.STATUS_SHATTER,
}
_BUFF_EFFECTS = {
    "buff_chr_0031_mifu_normalskill_2": EffectType.STATUS_MIFU_ZHUIXING_READY,
    "buff_chr_0031_mifu_normalskill_3": EffectType.STATUS_MIFU_KAITIAN_READY,
}
_PRESENTATION = {
    "EffectAction+EffectActionData", "PlayAnimationAction+PlayAnimationActionData",
    "PlaySoundAction+PlaySoundActionData", "VoiceTriggerAction+VoiceTriggerActionData",
    "EnemyHurtAnimAction+Data", "CharWeaponVisibleAction+CharWeaponVisibleActionData",
    "CameraImpulseAction+CameraImpulseActionData", "CameraRotateAction+CameraRotationActionData",
    "AddDynamicCcsAction+AddDynamicCcsActionData", "AddCameraControlStateAction+AddCameraControlStateActionData",
    "OverrideCameraFollowAction+OverrideCameraFollowActionData", "AnimatedCameraAction+AnimatedCameraActionData",
    "LockCameraAimAction+LockCameraAimActionData", "HideUIAction+Data", "DebugPrintAction+Data",
    "UltimateShowAction+Data", "CharWeaponAnimationAction+CharWeaponAnimationActionData",
    "ShowHideActorAction+ShowHideActorData", "ModifyWeaponMountPoint+Data", "HitStopAction+Data",
}
_SCENE_GEOMETRY = {
    # This compiler's explicit scenario is one stationary enemy within each hit
    # box. These nodes do not create extra hits or choose an additional enemy.
    "SelfRotateAction+Data", "TeleportAction+Data",
    "CustomRootMotionAction+Data", "TeleportPosSelectAction+Data", "SaveTwoDirectionAngle+Data",
    "MoveToAction+Data", "SnapToTargetWithRangeAction+Data", "AllowNextSkillAction+Data",
}
_SCENARIO_IGNORED = {
    "SetSuperArmorAction+Data",  # No incoming attacks in this stationary-target damage scenario.
    "TimeDilationAction+Data",  # Absolute authored event times and SkillTiming windows are fixed inputs.
    "SetIgnoreGlobalTimeScaleAction+Data",  # Fixed timeline seconds are not rescaled by presentation playback.
    "CheckComboSkillCameraAlphaSetting+Data",  # Explicit camera option 1; preserve boolean/inversion below.
    "IgnoreModelIntervalCheck+Data",  # Fixed observed cast windows exclude model-playback interval arbitration.
    "SaveTargetDistanceAction+Data",  # Ignore only an unused numeric output; used distances stay unresolved.
    "MarkCanInterrupt+Data",  # Legal handoffs use SkillTiming windows, with no external hit interrupts.
    "UltimateTimeAction+Data",  # Fixed absolute timeline excludes cutscene playback clock changes.
}


def _reads_blackboard(value, key):
    if isinstance(value, dict):
        if value.get("useBlackboardKey") and value.get("blackboardKey") == key:
            return True
        return any(_reads_blackboard(child, key) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_reads_blackboard(child, key) for child in value)
    return False
_GAMEPLAY = {"DamageAction+DamageActionData", "ObtainCostAction+Data", "CreateBuffAction+Data",
             "SpellInfliction+Data", "LaunchProjectile+Data", "SpawnAbilityEntity+Data",
             "HealAction+Data", "CastSkill+Data", "FinishBuffAction+Data", "FinishBuffAdvanced+Data",
             "ModifyDynamicBlackboard+Data", "SimpleCalcBBAction+Data", *_PHYSICAL}


def _has_gameplay(value):
    for node in _nodes(value):
        name = node["$type"].rsplit(".", 1)[-1]
        if name in _GAMEPLAY:
            return True
        if name in _PRESENTATION or name in _SCENE_GEOMETRY or name in _SCENARIO_IGNORED - {"SaveTargetDistanceAction+Data"} or name.startswith("Selector+"):
            continue
        if name.startswith("Check") or name.startswith("Compare") or name.endswith("Calculation"):
            continue
        if name == "IfElseAction+IfElseActionData":
            continue
        # Unknown actions may alter gameplay; absence from the implemented set
        # never proves a conditional arm is cosmetic.
        return True
    return False


def _nodes(value):
    if isinstance(value, dict):
        if "$type" in value:
            yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def compile_native_action(store, character, profile, actor, kind, *, damage_bonus=0.0, attributes=None, panel=None,
                          event_sequence=None, event_blackboard=None, initial_blackboard=None,
                          isolated_blackboard=False, buff_path=()):
    """A single enemy in range is an explicit simulation scenario, not a hit proof."""
    attributes = attributes or {}
    conditions = {
        "lizhiyan_wisd": attributes.get("智识", 0) >= attributes.get("意志", 0),
        "lizhiyan_will": attributes.get("智识", 0) < attributes.get("意志", 0),
    }
    native = bind_native_parameters(store, profile.skill_id, character.progression,
                                    character.progression.baseline.skill_rank, conditions=conditions,
                                    initial_blackboard=initial_blackboard)
    record = native_record(store, profile.skill_id)["data"]
    scenario_nodes = set()
    bb = {} if isolated_blackboard else dict(native.blackboard)
    bb.update(event_blackboard or {})
    events = []
    buff_ids = set()
    buff_queries = {}
    attribute_queries = {}
    timer_ids = set()
    event_defaults = {}
    spell_buff_definitions = {}
    ability_events = {v["value"]: k for k, v in native_enums()["Beyond.Gameplay.Core.AbilitySystem+Event"].items()}
    damage_masks = {k: v["value"] for k, v in native_enums()["Beyond.Gameplay.DamageDecorateMask"].items()}

    def resource_target(value):
        kinds = {0: "action_target", 1: "source", 2: "context", 4: "owner", 5: "main", 6: "main_target"}
        source = value["targetSource"]
        if source == 3:
            selector = value["selectorData"]
            finder = selector["finderData"]
            if finder is None or selector["postProcessorData"]:
                raise UnresolvedMechanic(f"Native target filters require binding: {profile.skill_id}")
            name = finder["$type"].rsplit(".", 1)[-1]
            validators = selector["validatorData"]
            if validators:
                if (name == "Selector+CharacterTeamFinder+Data" and len(validators) == 1
                        and validators[0]["$type"].endswith(".Selector+MainCharacterValidator+Data")
                        and not validators[0]["$value"]):
                    return NativeTarget("squad_main")
                raise UnresolvedMechanic(f"Native target filters require binding: {profile.skill_id}")
            choices = {"Selector+CharacterTeamFinder+Data": "living_squad", "Selector+MainTargetFinder+Data": "main_target",
                       "Selector+SourceFinder+Data": "source", "Selector+AllEnemyFinder+Data": "enemy",
                       "Selector+HitBoxFinder+Data": "enemy"}
            if name not in choices:
                raise UnresolvedMechanic(f"Native finder requires binding: {profile.skill_id}/{name}")
            return NativeTarget(choices[name])
        if source not in kinds:
            raise UnresolvedMechanic(f"Native instant target search requires selector binding: {profile.skill_id}")
        return NativeTarget(kinds[source], value["targetGroupKey"] if source == 2 else "")

    def target_count(target):
        if target["targetSource"] in {0, 1, 4, 5, 6}:
            return CombatExpression("literal", (1.0,))
        if target["targetSource"] == 2:
            return combat_input("target." + target["targetGroupKey"] + ".count")
        raise UnresolvedMechanic(f"Unbound selector count: {profile.skill_id}/{target['targetSource']}")

    def buff_count(settings, target, count_type=0):
        if count_type not in {0, 1}:
            raise UnresolvedMechanic(f"Unknown native buff count type: {count_type}")
        selector = resource_target(target)
        mode, tags = None, ()
        if settings["checkType"] == 1:
            mode, tags = tag_query(settings["tagQuery"])
            ids = ()
        elif settings["checkType"] == 0:
            ids = tuple(settings["buffIdList"])
        else:
            raise UnresolvedMechanic(f"Unknown native buff query type: {settings['checkType']}")
        identity = (selector, ids, mode, tags, count_type)
        if identity not in buff_queries:
            key = f"native.query.{len(buff_queries)}"
            buff_queries[identity] = NativeBuffQuery(key, selector, ids, mode, tags, count_type)
        buff_ids.update(ids)
        return combat_input(buff_queries[identity].key)

    def number(value):
        if value["useBlackboardKey"]:
            return combat_input("bb." + value["blackboardKey"])
        result = native_number(value, bb)
        if result is None or not math.isfinite(result):
            raise UnresolvedMechanic(f"Unbound native number: {profile.skill_id}/{value}")
        return CombatExpression("literal", (result,))

    def attribute(selector, name):
        identity = (selector, name)
        if identity not in attribute_queries:
            key = f"native.attribute.{len(attribute_queries)}"
            attribute_queries[identity] = NativeAttributeQuery(key, selector, name)
        return combat_input(attribute_queries[identity].key)

    def condition(sequence):
        result = []
        invert = False
        for node in sequence["actionData"]:
            name = node["$type"].rsplit(".", 1)[-1]
            value = node["$value"]
            if not value.get("isEnable", True):
                continue
            if name == "NotNextCheckAction+Data":
                invert = not invert
                continue
            before = len(result)
            if name == "CheckMainCharacterCondition+Data":
                test = combat_input("source.is_main")
            elif name == "CheckGlobalCDTimerAction+Data":
                timer_ids.add(value["buffId"])
                test = combat_input(f"timer.{value['buffId']}.ready")
            elif name == "CheckPhysicalInflictionType+Data":
                test = CombatExpression("any", tuple(CombatExpression("eq", (combat_input("event.physical_type"), float(i)))
                                                     for i in range(4) if value["mask"] & (1 << i)))
            elif name == "CheckSpellInflictionType+Data":
                # Native ExecuteInternal tests bit event.inflictionType. A
                # nonempty savedKey also writes the blackboard on success;
                # that side effect needs short-circuit sequence binding.
                if value["savedKey"] or value["mask"] < 0 or value["mask"] & ~15:
                    raise UnresolvedMechanic("Native spell condition output/mask needs binding")
                test = CombatExpression("any", tuple(CombatExpression("eq", (combat_input("event.spell_type"), float(i)))
                                                     for i in range(4) if value["mask"] & (1 << i)))
            elif name == "CheckOriginSkillType+Data":
                test = CombatExpression("any", tuple(CombatExpression("eq", (combat_input("event.skill_type"), float(i)))
                                                     for i in value["skillTypeList"]))
            elif name == "CheckEntityNum+Data":
                tests = {0: "lt", 1: "le", 2: "gt", 3: "ge", 4: "eq"}
                test = CombatExpression(tests[value["compareType"]],
                                               (target_count(value["checkTarget"]), float(value["minNum"])))
            elif name == "CheckBuffStackNumAdvanced+Data":
                tests = {0: "lt", 1: "le", 2: "gt", 3: "ge", 4: "eq"}
                test = CombatExpression(tests[value["compareType"]],
                                               (buff_count(value["buffSettings"], value["checkTarget"], value["buffStackNumType"]), number(value["value"])))
            elif name == "CheckBuffStackNumByTag+Data":
                tests = {0: "lt", 1: "le", 2: "gt", 3: "ge", 4: "eq"}
                count = buff_count({"checkType": 1, "tagQuery": value["tagQuery"]}, value["checkTarget"], value["buffStackNumType"])
                test = CombatExpression(tests[value["compareType"]], (count, number(value["value"])))
            elif name == "CheckBuffStackNum+Data":
                bid = value["buffId"]["buffId"]
                tests = {0: "lt", 1: "le", 2: "gt", 3: "ge", 4: "eq"}
                count = buff_count({"checkType": 0, "buffIdList": [bid]}, value["checkTarget"])
                test = CombatExpression(tests[value["compareType"]], (count, number(value["value"])))
            elif name in {"CheckBuffIdInContext+Data", "CheckBuffIdInContextAdvanced+Data"}:
                advanced = name == "CheckBuffIdInContextAdvanced+Data"
                # Advanced ID entries are StringBlackboardRefs, and its output
                # key writes the matching ID. Only the proven tag-only, no-write
                # arm is bound here; neither is interchangeable with plain IDs.
                if advanced and (value["checkType"] != 1 or value["blackboardKey"]):
                    raise UnresolvedMechanic("Native advanced event buff identity/output needs binding")
                if value["blackboardKey"]:
                    raise UnresolvedMechanic("Dynamic native event buff identity")
                if value["checkType"] == 1:
                    mode, tags = tag_query(value["query"])
                    keys = [f"event.buff_tag.{tag}" for tag in tags]
                elif value["checkType"] == 0:
                    keys = [f"event.buff_id.{bid['buffId'] if isinstance(bid, dict) else bid}" for bid in value["buffIdList"]]
                else:
                    raise UnresolvedMechanic("Unknown native buff identity query")
                event_defaults.update(dict.fromkeys(keys, 0.0))
                test = CombatExpression("all" if value["checkType"] == 1 and mode in {"HasAll", "ExceptAll"} else "any",
                                        tuple(combat_input(k) for k in keys))
                if value["checkType"] == 1 and mode.startswith("Except"):
                    test = CombatExpression("not", (test,))
                if advanced:
                    # Native first requires a Buff event context, then loads
                    # that ID's BuffData. Metadata absence in this world is
                    # unknown, not an empty tag list that could match Except.
                    event_defaults["event.buff_context"] = 0.0
                    test = CombatExpression("all", (combat_input("event.buff_context"),
                                                   combat_input("event.buff_data_available"), test))
            elif name == "CheckDamageDecorateMask+Data":
                if value["checkType"] not in {0, 1, 2}:
                    raise UnresolvedMechanic(f"Unreviewed native damage mask check mode: {profile.skill_id}/{value['checkType']}")
                if type(value["mask"]) != int or abs(value["mask"]) >= 2**53:
                    raise UnresolvedMechanic("Native damage mask exceeds exact numeric input domain")
                # No default and no alias from skill type/expected critical.
                # The real damage event's context must supply its own mask.
                test = CombatExpression("native_damage_mask", (combat_input("event.damage.decorate_mask"),
                                        float(value["mask"]), float(value["checkType"])))
            elif name == "CheckObjectTypeMatch+Data":
                target = value["target"]
                if target["targetSource"] != 2 or target["targetGroupKey"] != "trigger":
                    raise UnresolvedMechanic("Unbound native event object target")
                test = CombatExpression("mask_any", (combat_input("event.target_object_type"), float(value["objectTypeMask"])))
            elif name == "CompareFloat+Data":
                a, b = number(value["valueA"]), number(value["valueB"])
                tests = {0: "lt", 1: "le", 2: "gt", 3: "ge", 4: "eq"}
                if value["compare"] not in tests:
                    raise UnresolvedMechanic("Unknown comparison enum")
                test = CombatExpression(tests[value["compare"]], (a, b))
            elif name == "CheckComboSkillCameraAlphaSetting+Data" and name in _SCENARIO_IGNORED:
                if value["desiredAlphaSetting"] not in {0, 1}:
                    raise UnresolvedMechanic("Unknown native camera alpha option")
                scenario_nodes.add(name)
                test = CombatExpression("literal", (float(value["desiredAlphaSetting"] == 1),))
            else:
                raise UnresolvedMechanic(f"Runtime condition: {profile.skill_id}/{name}")
            result.append(CombatExpression("not", (test,)) if invert else test)
            invert = False
            assert len(result) == before + 1
        return CombatExpression("all", tuple(result))

    def sequence(data, at, end, guard=None):
        if data.get("onlyExecuteWhenSourceIsMainChar"):
            guard = CombatExpression("all", (guard, combat_input("source.is_main"))) if guard else combat_input("source.is_main")
        if data.get("onlyExecuteWhenSourceIsGuard"):
            # Guard is a distinct native role. It cannot be inferred from being
            # off field; an observer/scenario must establish it explicitly.
            guard = CombatExpression("all", (guard, combat_input("source.is_guard"))) if guard else combat_input("source.is_guard")
        invert_next = False
        for node in data.get("actionData", ()):
            body = node["$value"]
            name = node["$type"].rsplit(".", 1)[-1]
            if not body.get("isEnable", True) or name in _PRESENTATION or name in _SCENE_GEOMETRY:
                continue
            if name in _SCENARIO_IGNORED and not name.startswith("Check"):
                if name != "SaveTargetDistanceAction+Data" or not (
                    _reads_blackboard(record, body["bbKey"]) or _reads_blackboard(event_sequence, body["bbKey"])
                ):
                    scenario_nodes.add(name)
                    continue
            if name == "NotNextCheckAction+Data":
                invert_next = not invert_next
                continue
            try:
                if name.startswith(("Check", "Compare")):
                    test = condition({"actionData": [node]})
                    if invert_next:
                        test = CombatExpression("not", (test,))
                    invert_next = False
                    decision = f"check.{at}.{body['serverActionIndex']}.{len(events)}"
                    events.append(CombatEvent(at, "native_check", condition=guard, assignments=((decision, test),)))
                    guard = CombatExpression("all", (guard, combat_input(decision))) if guard else combat_input(decision)
                    continue
                action(name, body, at, end, guard)
            except UnresolvedMechanic as error:
                events.append(CombatEvent(at, "unresolved_native", condition=guard, unresolved=(str(error),)))
                if name.startswith(("Check", "Compare")):
                    # A failed-to-bind check does not authorize the remaining
                    # sequence. In particular it must not create a ready state.
                    return

    def action(name, body, at, end, guard):
        def emit(event):
            events.append(replace(event, condition=guard))

        if name == "FindTargetAction+FindTargetActionData":
            try:
                choice = resource_target({"targetSource": 3, "selectorData": body["selectorData"]})
            except UnresolvedMechanic as error:
                raise UnresolvedMechanic(f"Native target filtering: {profile.skill_id}/{body['targetGroupKey']}") from error
            # The declared scenario has one live stationary enemy inside these
            # hit boxes. Entity/team/buff-filtered selectors need separate counts.
            emit(CombatEvent(at, "native_target", target_bindings=(NativeTargetBinding(body["targetGroupKey"], (choice,)),)))
        elif name == "ConvertToTargetContext+Data":
            if body["operationType"] != 0 or body["excludeTarget"] != 0:
                raise UnresolvedMechanic(f"Native target conversion needs geometry/exclusion binding: {profile.skill_id}")
            emit(CombatEvent(at, "native_target", target_bindings=(NativeTargetBinding(
                body["targetGroupKey"], (resource_target(body["convertFrom"]),)),)))
        elif name == "MergeTargetAction+Data":
            if body["mergeHittableTargets"]:
                raise UnresolvedMechanic(f"Native hittable-target merge requires binding: {profile.skill_id}")
            emit(CombatEvent(at, "native_target", target_bindings=(NativeTargetBinding(
                body["targetGroupKey"], tuple(resource_target(v) for v in body["targets"])),)))
        elif name == "IfElseAction+IfElseActionData":
            arms = (body["succeedActions"], body["failActions"])
            if not any(_has_gameplay(arm) for arm in arms):
                return
            test = condition(body["conditionAction"])
            # A native branch decision is sampled once, before either arm
            # mutates the blackboard or consumes a predicate's source pool.
            decision = f"branch.{at}.{body['serverActionIndex']}.{len(events)}"
            emit(CombatEvent(at, "native_branch", assignments=((decision, test),)))
            yes = CombatExpression("all", (guard, combat_input(decision))) if guard else combat_input(decision)
            no = CombatExpression("not", (combat_input(decision),))
            no = CombatExpression("all", (guard, no)) if guard else no
            sequence(arms[0], at, end, yes)
            sequence(arms[1], at, end, no)
        elif name == "ChannelingAction+Data":
            maximum = body["maxCountPerTarget"]
            if maximum != 1:
                raise UnresolvedMechanic(f"Unbound repeated channel: {profile.skill_id}/{body['serverActionIndex']}")
            sequence(body["actionOnTick"], at, end, guard)
        elif name == "ForEachAction+Data":
            selector = resource_target(body["target"])
            begin = len(events)
            sequence(body["action"], at, end)
            callbacks = tuple(events[begin:])
            del events[begin:]
            emit(CombatEvent(at, "native_iteration", iterations=(NativeIteration(selector, callbacks),)))
        elif name == "DamageAction+DamageActionData":
            # ActionTargetType is independent of TargetSettings.targetSource.
            attacker = {0: "source", 1: "owner", 5: "main"}.get(body["attacker"])
            if attacker is None:
                raise UnresolvedMechanic(f"Native attacker target needs binding: {body['attacker']}")
            hit_source = NativeTarget(attacker)
            for unit in body["damageUnits"]:
                if unit["damageAttributeType"] != 0:
                    continue  # Poise-only unit; damage never inherits the stale literal.
                if unit["simpleCalculation"]:
                    multiplier = number(unit["atkScale"])
                else:
                    calculation = unit["atkCalculation"]
                    if calculation is None or not calculation["$type"].endswith(".AtkScaleCalculation"):
                        raise UnresolvedMechanic(f"Native attack calculation: {profile.skill_id}")
                    multiplier = number(calculation["$value"]["atkScale"])
                if unit["damageProcessors"]:
                    raise UnresolvedMechanic(f"Native damage processors: {profile.skill_id}")
                if unit["takeAtkSnapshot"]:
                    emit(CombatEvent(at, "unresolved_native", unresolved=("Native attacker attribute snapshot not yet bound",)))
                element = _DAMAGE_ELEMENTS.get(unit["damageType"])
                if element is None:
                    raise UnresolvedMechanic(f"Unbound damage type: {unit['damageType']}")
                mask = unit["damageDecorateMask"]
                # BattleFormula.CalculateDamage multiplies these branches by
                # the source's reaction scalar, after the normal damage buckets.
                if mask & 0xfd00038:
                    scalar = attribute(hit_source, "ignite_damage_scalar")
                    multiplier = CombatExpression("multiply", (multiplier, scalar))
                elif mask & damage_masks["PhysicalInfliction"]:
                    scalar = attribute(hit_source, "physical_infliction_damage_scalar")
                    multiplier = CombatExpression("multiply", (multiplier, scalar))
                tags = tuple(tag for native_tag, tag in (
                    ("NormalAttack", "normal"), ("NormalSkill", "skill"),
                    ("UltimateSkill", "ultimate"), ("ComboSkill", "combo"),
                    ("PhysicalInfliction", "physical_anomaly"), ("Dot", "dot"),
                    ("TalentDamage", "talent"),
                ) if mask & damage_masks[native_tag])
                hit_guard = guard
                if unit["onlyEnableForMainChar"]:
                    main = attribute(hit_source, "is_main")
                    hit_guard = CombatExpression("all", (guard, main)) if guard else main
                hit_event = CombatEvent(at, "on_hit", hit=DamageHit(
                    actor, "target", element, 0, panel.bonus_for(element, tags) if panel else damage_bonus,
                    tags[0] if tags else "unclassified", can_crit=True,
                    damage_tags=tags,
                ), hit_multiplier_formula=multiplier, hit_source=hit_source)
                events.append(CombatEvent(at, "native_damage_targets", condition=hit_guard,
                                          iterations=(NativeIteration(resource_target(body["targetSettings"]), (hit_event,)),)))
        elif name in _PHYSICAL:
            eid = _PHYSICAL[name]
            duration = native_number(body["duration"], bb) if "duration" in body else None
            emit(CombatEvent(at, "physical_anomaly", effects=(SkillEffect(eid, count=1, duration=duration, target="enemy"),)))
        elif name == "ModifyDynamicBlackboard+Data":
            if not body["directValue"]:
                raise UnresolvedMechanic(f"Runtime native calculation: {profile.skill_id}/{body['key']}")
            key = "bb." + body["key"]
            operand = number(body["value"])
            operation = body["operation"]
            if operation == 0:
                formula = operand
            elif operation in {1, 2, 3}:
                formula = CombatExpression({1: "add", 2: "multiply", 3: "divide"}[operation], (combat_input(key), operand))
            elif operation in {4, 5, 6}:
                formula = CombatExpression({4: "floor", 5: "ceil", 6: "round"}[operation], (combat_input(key),))
            else:
                raise UnresolvedMechanic(f"Unknown blackboard operation: {operation}")
            emit(CombatEvent(at, "native_formula", assignments=((key, formula),)))
        elif name == "SimpleCalcBBAction+Data":
            operation = {1: "add", 2: "multiply", 3: "divide"}.get(body["operation"])
            if operation is None:
                raise UnresolvedMechanic(f"Unbound binary operation: {body['operation']}")
            formula = CombatExpression(operation, (number(body["value1"]), number(body["value2"])))
            emit(CombatEvent(at, "native_formula", assignments=(("bb." + body["key"], formula),)))
        elif name == "SaveBuffStackNumAdvanced+Data":
            emit(CombatEvent(at, "native_snapshot", assignments=(("bb." + body["key"],
                              buff_count(body["buffSettings"], body["checkTarget"])),)))
        elif name == "StoreAttributeValue+Data":
            enums = native_enums()
            if body["primaryAttributeType"] != enums["Beyond.GEnums.ModifyAttributeType"]["Specific"]["value"]:
                raise UnresolvedMechanic("Native primary/sub/all attribute selection not yet bound")
            attributes_by_id = {v["value"]: k for k, v in enums["Beyond.GEnums.AttributeType"].items()}
            attribute_id = body["attributeType"]
            if attribute_id not in attributes_by_id:
                raise UnresolvedMechanic(f"Unknown native stored attribute: {attribute_id}")
            stores = enums["Beyond.Gameplay.Core.StoreAttributeValue+StoreAttributeType"]
            if body["storeAttributeType"] == stores["BaseNonConverted"]["value"]:
                mode = "armed_nonconverted"
            elif body["storeAttributeType"] == stores["FinalNonConverted"]["value"]:
                mode = "final_nonconverted"
            else:
                raise UnresolvedMechanic(f"Unknown native attribute store mode: {body['storeAttributeType']}")
            # Do not alias the non-converted native value to a converted panel
            # stat. Missing inputs remain unknown until their producer is bound.
            stored = attribute(resource_target(body["targetSettings"]), f"native.{mode}.{attribute_id}")
            single = lambda value: CombatExpression("float32", (number(value),))
            if body["useFloor"]:
                stored = CombatExpression("floor", (CombatExpression("divide", (stored, single(body["divisorValue"]))),))
            formula = CombatExpression("add", (single(body["baseValue"]),
                                        CombatExpression("multiply", (stored, single(body["multiplierValue"])))))
            emit(CombatEvent(at, "native_attribute_snapshot", assignments=(("bb." + body["key"], formula),)))
        elif name == "StoreBuffCount+Data":
            target = body["buffOwners"]
            if (body["useCurrentBuff"] or body["buffId"] != "buff_physical_no_guard"
                    or target["targetSource"] != 2 or target["targetGroupKey"] != "trigger"):
                raise UnresolvedMechanic("Native buff count snapshot needs owner binding")
            guarded = CombatExpression("all", (guard, combat_input("event.target_is_enemy"))) if guard else combat_input("event.target_is_enemy")
            events.append(CombatEvent(at, "native_snapshot", condition=guarded,
                                     assignments=(("bb." + body["blackboardKey"], combat_input("count.STACK_SHRED")),)))
        elif name == "ReadSkillSettingData+Data":
            settings = native_asset("SkillSetting")
            for request in body["dataList"]:
                row = next((r for r in settings["spellInflictionDataList"] if r["key"] == request["dataKey"]), None)
                if row is None:
                    raise UnresolvedMechanic(f"Missing native setting row: {request['dataKey']}")
                formula = CombatExpression("table", (number(request["column"]), *row["values"]))
                if row["enhanceFormulaKey"]:
                    enhance = next((r for r in settings["physicalAndSpellInflictionEnhanceFormulaList"]
                                    if r["key"] == row["enhanceFormulaKey"]), None)
                    if enhance is None:
                        raise UnresolvedMechanic(f"Missing native enhancement: {row['enhanceFormulaKey']}")
                    selector = resource_target(request["enhanceAttributeSource"])
                    strength = attribute(selector, "arts_strength")
                    numerator = CombatExpression("multiply", (enhance["paramA"], strength))
                    if enhance["formulaType"] == 1:
                        enhancement = numerator
                    elif enhance["formulaType"] == 2:
                        enhancement = CombatExpression("divide", (numerator, CombatExpression("add", (enhance["paramB"], strength))))
                    elif enhance["formulaType"] == 0:
                        enhancement = CombatExpression("literal", (0.0,))
                    else:
                        raise UnresolvedMechanic("Unknown native enhancement formula")
                    formula = CombatExpression("multiply", (formula, CombatExpression("add", (1.0, enhancement))))
                emit(CombatEvent(at, "native_setting", assignments=(("bb." + request["storeKey"], formula),)))
        elif name == "AddGlobalCDTimer+Data":
            timer_ids.add(body["buffId"])
            emit(CombatEvent(at, "native_timer", timers=((body["buffId"], number(body["cdTime"])),)))
        elif name == "ChangeSkillAction+Data":
            if body["overrideCacheTime"]:
                raise UnresolvedMechanic("Native replacement input cache override needs binding")
            emit(CombatEvent(at, "native_skill_changed", skill_changes=(NativeSkillChange(
                body["skillSlot"], body["targetSkillId"], resource_target(body["skillSource"]), body["lifeTimeType"],
                number(body["duration"]), inherit_cooldown=body["inheritOriginSkillCdProgress"],
                reverted_skill=body["revertedSkillId"] if body["specificRevertedSkillId"] else None,
            ),)))
        elif name == "SpellInfliction+Data":
            from src.data.native_buff_program import compile_buff_definition
            from src.data.native_spell_runtime import SPELL_ELEMENTS

            if body["inflictionType"] not in SPELL_ELEMENTS:
                raise UnresolvedMechanic(f"Unknown native spell type: {body['inflictionType']}")
            element_name, _ = SPELL_ELEMENTS[body["inflictionType"]]

            def spell_buff(key, inherited=()):
                if key not in spell_buff_definitions:
                    data = native_record(store, key)["data"]
                    reference = {"assignBlackboard": bool(inherited), "assignItems": [
                        {"directValueType": 0, "useDirectValue": False, "targetKey": name,
                         "inputValueKey": "spell." + name} for name in inherited]}
                    definition = compile_buff_definition(store, character, profile, actor, key, data,
                                                         reference, attributes=attributes, panel=panel, path=buff_path)
                    spell_buff_definitions[key] = NativeBuffChange(key, CombatExpression("literal", (1.0,)), definition=definition)
                return spell_buff_definitions[key]

            burst = spell_buff(f"buff_common_{element_name}_{element_name}_triggered")
            cross = tuple((old_type, spell_buff(f"buff_common_try_{element_name}_{old_name}_triggered",
                                               ("consumed_type", "consumed_layer", "count")))
                          for old_type, (old_name, _) in SPELL_ELEMENTS.items() if old_type != body["inflictionType"])
            emit(CombatEvent(at, "native_spell_infliction", native_spells=(NativeSpellInfliction(
                body["inflictionType"], resource_target(body["source"]), resource_target(body["target"]), body["isExtra"],
                burst_buff=burst, cross_buffs=cross,
            ),)))
        elif name == "TriggerSpellBurstEventAction+Data":
            if body["spellBurstType"] not in {0, 1, 2, 3}:
                raise UnresolvedMechanic(f"Unknown native spell burst type: {body['spellBurstType']}")
            emit(CombatEvent(at, "native_spell_burst", native_spell_bursts=(body["spellBurstType"],)))
        elif name == "ObtainUspInNormalSkill+Data":
            settings = native_asset("SkillSetting")
            emit(CombatEvent(at, "battle_energy", native_resources=(NativeResourceChange(
                CombatResourceType.ULTIMATE_ENERGY, combat_input("cast.non_returned_sp"), number(body["coefficient"]),
                resource_target(body["source"]), NativeTarget("squad"),
                default_energy=(settings["atbConsumedDefaultUspGainSelf"], settings["atbConsumedDefaultUspGainOther"]),
            ),)))
        elif name == "ObtainCostAction+Data":
            resource = {0: CombatResourceType.ULTIMATE_ENERGY, 1: CombatResourceType.SKILL_POINT}.get(body["costType"])
            if resource is None:
                raise UnresolvedMechanic(f"Unknown resource type: {body['costType']}")
            if body["atbGainMethod"] not in {0, 1}:
                raise UnresolvedMechanic(f"Unknown native SP gain method: {body['atbGainMethod']}")
            emit(CombatEvent(at, "native_resource", native_resources=(NativeResourceChange(
                resource, number(body["costValue"]), number(body["coefficient"]),
                resource_target(body["source"]), resource_target(body["target"]), percent=body["isPercentValue"],
                ignore_energy_gain=body["ignoreUspGainScalar"], returned_sp=body["atbGainMethod"] == 1,
                only_main_source=resource == CombatResourceType.SKILL_POINT and body["atbOnlyMainChar"],
            ),)))
        elif name == "CreateBuffAction+Data":
            # Empty inheritSkillIdList cannot match the next-skill transfer arm.
            # Only root timeline actions have an authored OnEnd deadline here.
            action_bound = (body.get("autoFinishByAction") and not body.get("asChildBuff")
                            and not body.get("inheritSkillIdList") and not buff_path
                            and event_sequence is None and end > at)
            inherited_bound = (body.get("autoFinishByAction") and not body.get("asChildBuff")
                               and bool(body.get("inheritSkillIdList")) and not buff_path
                               and event_sequence is None and end > at)
            if inherited_bound:
                emit(CombatEvent(at, "unresolved_native", unresolved=("Native cross-skill CastEnd context not yet bound",)))
            child_bound = (body.get("asChildBuff") and bool(buff_path)
                           and not body.get("autoFinishByAction") and not body.get("inheritSkillIdList"))
            ability_bound = (body.get("asChildBuff") and not buff_path and event_sequence is None
                             and not body.get("autoFinishByAction") and not body.get("inheritSkillIdList"))
            if body.get("asChildBuff") and not (child_bound or ability_bound) or body.get("autoFinishByAction") and not action_bound:
                emit(CombatEvent(at, "unresolved_native", unresolved=("Native child/action-bound buff lifetime not yet bound",)))
            for reference in body["buffs"]:
                buff_id = reference["buffId"]
                eid = _BUFF_EFFECTS.get(buff_id)
                data = native_record(store, buff_id)["data"]
                parameters = {r["key"]: r["valueStr"] or r["valueDouble"] for r in data["blackboard"]}
                if reference["assignBlackboard"] and reference["assignItems"]:
                    for item in reference["assignItems"]:
                        if item["directValueType"] != 0:
                            raise UnresolvedMechanic(f"Non-numeric buff inheritance: {buff_id}/{item['targetKey']}")
                        parameters[item["targetKey"]] = item["numericValue"] if item["useDirectValue"] else bb.get(item["inputValueKey"])
                duration = native_number(data["duration"], parameters)
                count = native_number(body["count"], bb)
                if eid is not None and (count is None or count != int(count)):
                    raise UnresolvedMechanic(f"Dynamic native buff count: {buff_id}")
                if eid is not None:
                    if action_bound:
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Canonical buff lacks action-bound instance identity",)))
                    if child_bound or ability_bound:
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Canonical buff lacks parent-bound instance identity",)))
                    emit(CombatEvent(at, "buff_created", effects=(SkillEffect(eid, count=int(count), duration=duration, target="self"),)))
                else:
                    buff_ids.add(buff_id)
                    stacking = data["stackingSettings"]
                    maximum = stacking["maxStackCnt"] if stacking["maxStackCnt"] > 0 else None
                    definition = None
                    if stacking["stackingType"] in {0, 2, 7} or (
                        stacking["stackingType"] == 4 and buff_id == "buff_chr_0024_deepfin_potential_3"
                    ):
                        from src.data.native_buff_program import compile_buff_definition

                        definition = compile_buff_definition(store, character, profile, actor, buff_id, data, reference,
                                                             attributes=attributes, panel=panel, path=buff_path)
                    bound_instance = (action_bound or inherited_bound) and definition is not None
                    parent_instance = (child_bound or ability_bound) and definition is not None
                    if (child_bound or ability_bound) and not parent_instance:
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Parent-bound buff lacks independent executable instance",)))
                    period = native_number(data["triggerInterval"], parameters)
                    if bound_instance and (period is None or not math.isfinite(period) or period > 0):
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Action-bound periodic buff needs tick/end ordering",)))
                        bound_instance = False
                    if parent_instance and (period is None or not math.isfinite(period) or period > 0):
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Parent-bound periodic buff needs tick/end ordering",)))
                        parent_instance = False
                    target = "enemy" if body["targetSettings"]["targetSource"] in {0, 2, 6} else "self"
                    change = NativeBuffChange(buff_id, number(body["count"]),
                                              CombatExpression("literal", (duration,)) if duration is not None else None,
                                              permanent=data["lifeType"] == 1, maximum=maximum, target=target,
                                              selector=resource_target(body["targetSettings"]), definition=definition,
                                              tags=expand_tags(data["applyTags"]),
                                              action_finish_after=end - at if bound_instance else None,
                                              child_of_buff=bool(parent_instance and child_bound),
                                              child_of_ability=bool(parent_instance and ability_bound),
                                              inherit_skill_ids=tuple(body["inheritSkillIdList"]) if bound_instance and inherited_bound else (),
                                              finish_with_next_skill=bool(body["finishWithNextSkillIfNotInherited"]) if inherited_bound else False)
                    if action_bound and definition is None:
                        emit(CombatEvent(at, "unresolved_native", unresolved=("Action-bound buff lacks executable instance definition",)))
                    emit(CombatEvent(at, "native_buff_created", native_buffs=(change,)))
                    # Preserve the presence/count even when another part of the
                    # buff still needs an interpreter; it is not zero damage proof.
                    listeners = []
                    for subscription in (() if definition is not None else data["abilityEventAction"]):
                        trigger = ability_events.get(subscription["abilityEvent"])
                        if trigger != "OnBeforeOutputPhysicalInfliction":
                            raise UnresolvedMechanic(f"Native event subscription: {buff_id}/{trigger}")
                        start = len(events)
                        for block in subscription["actions"]:
                            sequence(block, 0, 0)
                        callbacks = tuple(events[start:])
                        del events[start:]
                        listeners.append(NativeListener(buff_id, trigger, callbacks))
                    if listeners:
                        emit(CombatEvent(at, "native_listener", listeners=tuple(listeners)))
                    bound = {"abilityEventAction"}
                    if definition is not None:
                        bound.update({"buffEventAction", "damageModifier", "attributeModifier", "healModifier",
                                      "globalModifier", "poiseModifier", "shieldConfigs"})
                    remaining = {k: v for k, v in data.items() if k not in bound}
                    has_modifiers = definition is None and bool(data["attributeModifier"]["attributeModifiers"] or data["damageModifier"]
                                         or data["healModifier"] or data["globalModifier"] or data["poiseModifier"]
                                         or data["shieldConfigs"])
                    if _has_gameplay(remaining) or has_modifiers:
                        raise UnresolvedMechanic(f"Native buff execution: {profile.skill_id}/{buff_id}")
        elif name in {"FinishBuffAction+Data", "FinishBuffAdvanced+Data"}:
            if name == "FinishBuffAdvanced+Data":
                if body["buffSettings"]["checkType"] != 0:
                    raise UnresolvedMechanic(f"Native buff removal tag query: {profile.skill_id}")
                references = body["buffSettings"]["buffIdList"]
            else:
                references = [r["buffId"] for r in body["buffIds"]]
            for buff_id in references:
                eid = _BUFF_EFFECTS.get(buff_id)
                amount = native_number(body["finishLayerCnt"], bb)
                if amount is None or amount != int(amount):
                    raise UnresolvedMechanic(f"Dynamic buff removal count: {buff_id}")
                if eid is not None:
                    emit(CombatEvent(at, "buff_removed", effects=(SkillEffect(eid, count=-int(amount),
                                                                              target="self", consumes_all=body["finishAll"]),)))
                else:
                    buff_ids.add(buff_id)
                    target = "enemy" if body["buffOwner"]["targetSource"] in {0, 2, 6} else "self"
                    emit(CombatEvent(at, "native_buff_removed", native_buffs=(NativeBuffChange(
                        buff_id, CombatExpression("literal", (-float(amount),)), remove_all=body["finishAll"], target=target,
                        selector=resource_target(body["buffOwner"])),)))
        else:
            # Movement, target selection, projectile/entity hits, listeners and
            # formulas are not presentation. Keep them explicit until interpreted.
            raise UnresolvedMechanic(f"Native action execution: {profile.skill_id}/{name}")

    if event_sequence is not None:
        sequence(event_sequence, 0, 0)
    else:
        for window in record["actionGroupData"]["timelineActions"]:
            sequence(window["_sequenceActionData"], window["_startFrame"] / 30, window["_endFrame"] / 30)
    if event_sequence is None and record["actionGroupData"]["passiveEventActions"]:
        events.append(CombatEvent(0, "unresolved_native", unresolved=(f"Native passive timeline events: {profile.skill_id}",)))
    events = [replace(event, persists_after_interrupt=False) for event in events]
    cost = native.cost if native.cost_type == 1 else 0
    energy = native.cost if native.cost_type == 0 else 0
    if native.cost_type not in {0, 1}:
        raise UnresolvedMechanic(f"Unknown cast resource: {native.cost_type}")
    program = ActionProgram(profile.skill_id, actor, kind, cost, profile.handoff, native.cooldown, tuple(events),
                            energy_cost=energy, actor_lock=profile.actionable,
                            scenario_ignored_nodes=tuple(sorted(scenario_nodes)),
                            parameters=tuple(("bb." + k, float(v)) for k, v in bb.items() if isinstance(v, (float, int)))
                            + tuple(event_defaults.items())
                            + (("target.smart_target.count", 1.0),),
                            next_action_windows=profile.allow_next, native_buff_ids=tuple(sorted(buff_ids)),
                            native_timer_ids=tuple(sorted(timer_ids)))
    return replace(program, gate=cost, native_buff_queries=tuple(buff_queries.values()),
                   native_attribute_queries=tuple(attribute_queries.values()))
