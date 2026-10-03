"""Apply selected native talent/potential operations to ranked skill parameters."""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.data.character_progression import CharacterProgression
from src.data.combat_simulation import UnresolvedMechanic
from src.data.skill_timing import SkillTimingStore


@dataclass(frozen=True)
class NativeCombatParameters:
    blackboard: dict[str, float | str]
    cost_type: int
    cost: float
    cooldown: float
    sources: tuple[str, ...]
    cooldown_display: float | None = None


def _modify(original, operation, value):
    if operation == 1:
        return original + value
    if operation == 2:
        return original * value
    if operation == 3:
        return value
    raise UnresolvedMechanic(f"Unknown native parameter operation {operation}")


def bind_native_parameters(store: SkillTimingStore, skill_id: str, progression: CharacterProgression, rank=None, *, conditions=None):
    record = store.record(skill_id)["data"]
    bb = {r["key"]: r["valueStr"] if r["valueStr"] else r["valueDouble"] for r in record["blackboard"]}
    try:
        patch = store.ranked_skill(skill_id, rank)
    except KeyError:
        patch = None  # Child skills inherit parameters explicitly from their producer.
    if patch is None:
        cast = record.get("castData")
        if cast is None:
            raise UnresolvedMechanic(f"No native cast parameters: {skill_id}")
        cost_type = cast["costData"]["costType"]
        cost = cast["costData"]["costValue"]
        cooldown = cast["cooldownTime"]
    else:
        for item in patch["blackboard"]:
            bb[item["key"]] = item["valueStr"] if item["valueStr"] else item["value"]
        cost_type, cost, cooldown = patch["costType"], patch["costValue"], patch["coolDown"]
    sources = [f"SkillPatchTable/{skill_id}/rank={rank or 'max'}"]
    cooldown_display = cooldown
    conditions = conditions or {}
    for passive in (*progression.talents, *progression.active_potentials):
        for modifier in passive.modifiers:
            value = modifier.get("skillBbModifier")
            if value is not None and value["skillId"] == skill_id:
                names = modifier["activeCondition"]
                if any(name not in conditions for name in names):
                    raise UnresolvedMechanic(f"Unknown parameter condition: {passive.effect_id}/{names}")
                if not all(conditions[name] for name in names):
                    continue
                key = value["bbKey"]
                if value["stringValue"]:
                    if value["modifyType"] != 3:
                        raise UnresolvedMechanic("Cannot add/multiply string parameters")
                    bb[key] = value["stringValue"]
                else:
                    original = bb.get(key)
                    if original is None and value["modifyType"] == 3:
                        original = 0  # Overwrite does not read the previous value.
                    if not isinstance(original, (int, float)):
                        raise UnresolvedMechanic(f"Missing/non-numeric blackboard: {key}")
                    bb[key] = _modify(original, value["modifyType"], value["floatValue"])
                sources.append(passive.source)
            parameter = modifier.get("skillParamModifier")
            if parameter is not None and parameter["skillId"] == skill_id:
                names = modifier["activeCondition"]
                if any(name not in conditions for name in names):
                    raise UnresolvedMechanic(f"Unknown cast condition: {passive.effect_id}/{names}")
                if not all(conditions[name] for name in names):
                    continue
                kind = parameter["paramType"]
                if kind == 1:
                    cost = _modify(cost, parameter["modifyType"], parameter["paramValue"])
                elif kind == 2:
                    cooldown = _modify(cooldown, parameter["modifyType"], parameter["paramValue"])
                elif kind == 4:
                    cooldown_display = _modify(cooldown if cooldown_display is None else cooldown_display,
                                               parameter["modifyType"], parameter["paramValue"])
                else:
                    raise UnresolvedMechanic(f"Unknown cast parameter: {passive.effect_id}/{parameter}")
                # Beyond.GEnums.ModifiableSkillParam: 4 is CoolDownDisplay.
                # It is independent of the gameplay CoolDown parameter (2).
                sources.append(passive.source)
    if not all(math.isfinite(v) and v >= 0 for v in (cost, cooldown)):
        raise UnresolvedMechanic("Invalid native cast parameters")
    return NativeCombatParameters(bb, cost_type, cost, cooldown, tuple(dict.fromkeys(sources)), cooldown_display)
