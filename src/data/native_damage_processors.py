"""Verified defender NormalCalcZone processors, evaluated in each buff's scope."""

from dataclasses import dataclass

from src.data.combat_expressions import CombatExpression, MissingCombatInput, combat_input
from src.data.combat_simulation import UnresolvedMechanic
from src.data.damage_resolution import DamageResult
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.native_gameplay import native_asset, native_enums

ELEMENTS = {0: "物理", 2: "灼热", 3: "电磁", 4: "寒冷", 6: "自然"}


@dataclass(frozen=True)
class NativeDefenderDamageScale(ImmutableCombatValue):
    element: str | None
    addition: CombatExpression


def compile_damage_processors(data, buff_id):
    """Other sides/zones/conditions remain unpriced until their runtime is bound."""
    scales, unresolved = [], []
    zones = {r["name"]: r for r in native_asset("DamageScaleProcessorConfig")["allZones"]}
    enums = native_enums()
    enabled = enums["Beyond.Gameplay.Core.DamageModifier+Data+EnableSide"]["Defender"]["value"]
    side = enums["Beyond.Gameplay.Core.DamageScaleProcessor+DamageScaleSide"]["Defender"]["value"]
    for index, modifier in enumerate(data["damageModifier"]):
        try:
            if modifier["enableSide"] != enabled:
                raise UnresolvedMechanic("attacker modifier side")
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
                if body["side"] != side or body["zoneName"] != "NormalCalcZone":
                    raise UnresolvedMechanic("damage processor side/zone")
                if zone is None or zone["isMultiplyZone"] or zone["mergeAttackerAndDefender"] or zone["isDamageTypeZone"]:
                    raise UnresolvedMechanic("unsupported native zone configuration")
                value = body["addition"]
                expression = (combat_input("bb." + value["blackboardKey"]) if value["useBlackboardKey"]
                              else CombatExpression("literal", (float(value["value"]),)))
                # Native GetFloat precedes double-precision zone accumulation.
                row.append(NativeDefenderDamageScale(element, CombatExpression("float32", (expression,))))
            scales.extend(row)
        except UnresolvedMechanic as error:
            unresolved.append(f"Native damage modifier needs binding: {buff_id}/{index}/{error}")
    return tuple(scales), tuple(unresolved)


def resolve_native_hit(world, panel, hit, inputs):
    addition = 0.0
    unknown = []
    for instance in world.native_buff_instances.values():
        if instance.owner != hit.enemy:
            continue
        # Read the modifier's own blackboard, never the attack's or another buff's.
        for scale in instance.definition.damage_scales:
            if scale.element is not None and scale.element != hit.element:
                continue
            try:
                addition += scale.addition.evaluate(world._action_inputs[instance.uid])
            except MissingCombatInput as error:
                unknown.append(f"Native damage modifier input: {instance.key}/{error}")
    if unknown:
        return DamageResult(None, None, {}, tuple(sorted(set(unknown))))
    return world.damage_state.resolve_hit(panel, hit, now=world.time, inputs=inputs, native_damage_taken=addition)
