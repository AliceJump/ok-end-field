"""Verified NormalCalcZone processors, evaluated on their actual holder's side."""

from dataclasses import dataclass

from src.data.combat_expressions import CombatExpression, MissingCombatInput, combat_input
from src.data.combat_simulation import UnresolvedMechanic
from src.data.damage_resolution import DamageResult
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.native_gameplay import native_asset, native_enums

ELEMENTS = {0: "物理", 2: "灼热", 3: "电磁", 4: "寒冷", 6: "自然"}


@dataclass(frozen=True)
class NativeNormalDamageScale(ImmutableCombatValue):
    element: str | None
    addition: CombatExpression
    side: int


def compile_damage_processors(data, buff_id):
    """Only matching attacker/defender sides in the reviewed additive zone."""
    scales, unresolved = [], []
    zones = {r["name"]: r for r in native_asset("DamageScaleProcessorConfig")["allZones"]}
    enums = native_enums()
    enabled = enums["Beyond.Gameplay.Core.DamageModifier+Data+EnableSide"]
    sides = enums["Beyond.Gameplay.Core.DamageScaleProcessor+DamageScaleSide"]
    paired = {enabled[name]["value"]: sides[name]["value"] for name in ("Attacker", "Defender")}
    for index, modifier in enumerate(data["damageModifier"]):
        try:
            enable_side = modifier["enableSide"]
            if type(enable_side) is not int or enable_side not in paired:
                raise UnresolvedMechanic("unreviewed modifier enable side")
            side = paired[enable_side]
            condition = modifier["condition"]
            if condition["onlyExecuteWhenSourceIsMainChar"] or condition["onlyExecuteWhenSourceIsGuard"]:
                raise UnresolvedMechanic("source role condition")
            nodes = condition["actionData"]
            element = None
            if nodes:
                if len(nodes) != 1 or nodes[0]["$type"] != "Beyond.Gameplay.Core.Conditions.CheckDamageType+Data":
                    raise UnresolvedMechanic("damage condition expression")
                body = nodes[0]["$value"]
                if not body["isEnable"] or body["damageType"] not in ELEMENTS:
                    raise UnresolvedMechanic("disabled/unknown damage type condition")
                element = ELEMENTS[body["damageType"]]
            row = []
            for processor in modifier["damageProcessors"]:
                if processor["$type"] != "Beyond.Gameplay.Core.DamageScaleProcessor":
                    raise UnresolvedMechanic("damage processor type")
                body = processor["$value"]
                zone = zones.get(body["zoneName"])
                if type(body["side"]) is not int or body["side"] != side or body["zoneName"] != "NormalCalcZone":
                    raise UnresolvedMechanic("damage processor side/zone")
                if zone is None or zone["isMultiplyZone"] or zone["mergeAttackerAndDefender"] or zone["isDamageTypeZone"]:
                    raise UnresolvedMechanic("unsupported native zone configuration")
                value = body["addition"]
                expression = (combat_input("bb." + value["blackboardKey"]) if value["useBlackboardKey"]
                              else CombatExpression("literal", (float(value["value"]),)))
                # Native GetFloat precedes double-precision zone accumulation.
                row.append(NativeNormalDamageScale(element, CombatExpression("float32", (expression,)), side))
            scales.extend(row)
        except UnresolvedMechanic as error:
            unresolved.append(f"Native damage modifier needs binding: {buff_id}/{index}/{error}")
    return tuple(scales), tuple(unresolved)


def resolve_native_hit(world, panel, hit, inputs):
    from src.data.native_attribute_modifiers import attack_addition

    additions = {0: 0.0, 1: 0.0}
    unknown = []
    for instance in world.native_buff_instances.values():
        if not instance.enabled:
            continue
        # Read the modifier's own blackboard, never the attack's or another buff's.
        for scale in instance.definition.damage_scales:
            holder = hit.actor if scale.side == 0 else hit.enemy
            if instance.owner != holder:
                continue
            if scale.element is not None and scale.element != hit.element:
                continue
            try:
                additions[scale.side] += scale.addition.evaluate(world._action_inputs[instance.uid])
            except MissingCombatInput as error:
                unknown.append(f"Native damage modifier input: {instance.key}/{error}")
    if unknown:
        return DamageResult(None, None, {}, tuple(sorted(set(unknown))))
    try:
        native_attack = attack_addition(world, hit.actor)
    except MissingCombatInput as error:
        return DamageResult(None, None, {}, (str(error),))
    return world.damage_state.resolve_hit(panel, hit, now=world.time, inputs=inputs,
                                         native_damage_bonus=additions[0], native_damage_taken=additions[1],
                                         native_attack_percent=native_attack)
