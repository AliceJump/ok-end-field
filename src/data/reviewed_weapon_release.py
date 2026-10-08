"""One native-evidenced weapon clause, bound to successful model releases only."""

import hashlib
import json
import math
import re

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.native_gameplay import native_enums, native_number


def guzhou_ultimate_rule(weapon, build, *, root):
    folder = root / "assets/data/equipment_mechanics/20261008"
    manifest = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    raw = (folder / "guzhou.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["files"]["guzhou.json"]:
        raise ValueError("Native Guzhou evidence hash mismatch")
    evidence = json.loads(raw)
    inputs = json.loads((root / "assets/data/skill_timings/20261002/index.json").read_text(encoding="utf-8"))["native_inputs"]
    if (manifest["schema_version"] != 1 or not manifest["evidence_only"] or evidence["schema_version"] != 1
            or evidence["scope"] != "reviewed_model_release_only; not_full_native_execution"
            or evidence["native_inputs"] != inputs or manifest["native_inputs"] != inputs
            or evidence["weapon"] != "孤舟" or evidence["weapon_id"] != "wpn_funnel_0015"):
        raise ValueError("Unreviewed Guzhou evidence domain/build")
    skill_id, buff_id = "sk_wpn_funnel_0015", "buff_wpn_funnel_0015_ultimate"
    patch = evidence["selected_rank_patch"]
    if (evidence["weapon_basic"]["weaponPotentialSkill"] != skill_id
            or patch["skillId"] != skill_id or patch["level"] != 9 or build["weapon"]["skill_rank"] != 9):
        raise ValueError("Unreviewed Guzhou weapon/rank")
    skill = evidence["records"][skill_id]["data"]
    bb = {entry["key"]: entry["valueDouble"] for entry in skill["blackboard"]}
    bb.update({entry["key"]: entry["value"] for entry in patch["blackboard"]})
    events = native_enums()["Beyond.Gameplay.Core.AbilitySystem+Event"]
    blocks = [event for event in skill["actionGroupData"]["passiveEventActions"]
              if event["abilityEvent"] == events["OnBeforeCastSkill"]["value"]]
    if len(blocks) != 1 or len(blocks[0]["actions"]) != 1:
        raise ValueError("Changed Guzhou release event")
    block = blocks[0]["actions"][0]
    nodes = block["actionData"]
    if (block["onlyExecuteWhenSourceIsGuard"] or block["onlyExecuteWhenSourceIsMainChar"] or len(nodes) != 2
            or not nodes[0]["$type"].endswith("CheckSkillType+Data")
            or not nodes[1]["$type"].endswith("CreateBuffAction+Data")):
        raise ValueError("Changed Guzhou release guards")
    condition, create = (node["$value"] for node in nodes)
    if (not condition["isEnable"] or not create["isEnable"]
            or condition["skillTypeList"] != [native_enums()["Beyond.Gameplay.SkillType"]["UltimateSkill"]["value"]]
            or condition["checkTargetCurSkill"] or condition["mustBeforeExclusiveTime"]
            or condition["attackTypeMask"] != -1 or condition["skillOwner"]["targetSource"] != 0
            or not create["asChildBuff"] or create["autoFinishByAction"] or create["buffSource"] != 0
            or create["targetSettings"]["targetSource"] != 1 or len(create["buffs"]) != 1
            or native_number(create["count"], bb) != 1 or create["isExtra"]
            or create["targetSettings"]["selectorData"] != {"finderData": None, "postProcessorData": [], "validatorData": []}):
        raise ValueError("Unreviewed Guzhou release ownership/phase")
    attachment = create["buffs"][0]
    assignments = {item["targetKey"]: item["inputValueKey"] for item in attachment["assignItems"]
                   if not item["useDirectValue"]}
    if (attachment["buffId"] != buff_id or not attachment["assignBlackboard"]
            or attachment["readIdFromBlackboard"] or len(attachment["assignItems"]) != 2
            or assignments != {"pulse_dmg_up": "pulse_dmg_up3", "duration": "duration2"}):
        raise ValueError("Changed Guzhou release BB assignment")
    buff = evidence["records"][buff_id]["data"]
    inherited = {key: bb[value] for key, value in assignments.items()}
    if (buff["lifeType"] != 0 or buff["stackingSettings"]["stackingType"] != 4
            or buff["stackingSettings"]["isNeedStackEffect"]
            or any(buff[key] for key in ("buffEventAction", "abilityEventAction", "timelineActions", "igniteEventAction",
                                         "healModifier", "poiseModifier", "globalModifier", "shieldConfigs"))
            or buff["attributeModifier"]["attributeModifiers"] or len(buff["damageModifier"]) != 1):
        raise ValueError("Unreviewed Guzhou release lifetime/side effects")
    damage = buff["damageModifier"][0]
    guards = damage["condition"]["actionData"]
    processors = damage["damageProcessors"]
    if (damage["enableSide"] != 0 or len(guards) != 2 or len(processors) != 1
            or damage["condition"]["onlyExecuteWhenSourceIsGuard"]
            or damage["condition"]["onlyExecuteWhenSourceIsMainChar"]
            or not guards[0]["$type"].endswith("CheckDamageDecorateMask+Data")
            or not guards[1]["$type"].endswith("CheckDamageTypeMask+Data")
            or not all(guard["$value"]["isEnable"] for guard in guards)
            or guards[0]["$value"]["checkType"] != 2 or guards[0]["$value"]["mask"] != 256
            or guards[1]["$value"]["damageTypeMask"] != 1 << native_enums()["Beyond.GEnums.DamageType"]["Pulse"]["value"]
            or not processors[0]["$type"].endswith("DamageScaleProcessor")
            or processors[0]["$value"]["side"] != 0 or processors[0]["$value"]["zoneName"] != "NormalCalcZone"):
        raise ValueError("Unreviewed Guzhou damage filter")
    amount = native_number(processors[0]["$value"]["addition"], inherited)
    duration = native_number(buff["duration"], inherited)
    clauses = [(index, group["ranks"]["Rank 9"]["压制·流霆"])
               for index, group in enumerate(weapon["weapon_skills"])
               if "压制·流霆" in group.get("ranks", {}).get("Rank 9", {})]
    if len(clauses) != 1:
        raise ValueError("Missing Guzhou canonical clause")
    index, text = clauses[0]
    matches = list(re.finditer(r"装备者施放终结技后，战技造成的电磁伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。 / 该效果无法叠加。", text))
    if (len(matches) != 1 or amount is None or duration is None
            or not math.isclose(amount, float(matches[0]["amount"]) / 100, rel_tol=1e-6)
            or duration != float(matches[0]["duration"])):
        raise ValueError("Changed Guzhou native/canonical strength or duration")
    return DamageModifierSpec(
        f"weapon:{weapon['item_id']}:压制·流霆:ult", DamageBucket.DAMAGE_BONUS, ("电磁",), "self",
        ModifierMagnitude(amount), "ultimate_cast", damage_tags=("skill",), duration=duration,
        sources=(f"weapons/孤舟/weapon_skills/{index}/ranks/Rank 9/压制·流霆",
                 "equipment_mechanics/20261008/guzhou.json/selected_rank_patch",
                 f"equipment_mechanics/20261008/guzhou.json/records/{skill_id}/OnBeforeCastSkill",
                 f"equipment_mechanics/20261008/guzhou.json/records/{buff_id}"),
    )
