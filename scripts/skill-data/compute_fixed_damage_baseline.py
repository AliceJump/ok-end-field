"""Precompute fixed build/progression panels; no assumed rotations or temporary buffs."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
from pathlib import Path

import compute_damage_baseline as damage

from src.data.character_progression import SNAPSHOT, load_character_progression
from src.data.character_skills import get_character
from src.data.damage_attributes import DamageAttributeBasis
from src.data.damage_resolution import FixedDamagePanel
from src.data.damage_state_rules import fixed_weapon_bonuses
from src.data.fixed_skill_modifiers import fixed_skill_crit
from src.data.native_damage_scalars import reaction_scalars

ROOT = Path(__file__).resolve().parents[2]
ATTRIBUTES = {39: "力量", 40: "敏捷", 41: "智识", 42: "意志"}
SKILL_TAGS = {"普通攻击": "normal", "战技": "skill", "连携技": "combo", "终结技": "ultimate"}


def source_hashes(key):
    paths = [
        ROOT / "assets/data/character_skills" / f"{key}.json",
        ROOT / "assets/data/character_builds" / f"{key}.json",
        ROOT / "assets/data/weapons.json", ROOT / "assets/data/equipments.json",
        SNAPSHOT / "characters.json", SNAPSHOT / "tables.json.gz", SNAPSHOT / "index.json",
        ROOT / "assets/data/reaction_attributes/20261004/index.json",
        ROOT / "assets/data/reaction_attributes/20261004/scalars.json.gz",
        ROOT / "assets/data/reaction_attributes/20261004/CharacterTable.bytes.gz",
        Path(__file__).resolve(), ROOT / "scripts/skill-data/compute_damage_baseline.py",
        ROOT / "src/data/character_progression.py", ROOT / "src/data/native_damage_scalars.py",
        ROOT / "assets/data/skill_damage_row_semantics.json",
        ROOT / "src/data/damage_attributes.py",
        ROOT / "src/data/damage_state_rules.py",
        ROOT / "src/data/fixed_skill_modifiers.py",
    ]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def compute(key: str, tables: dict) -> dict:
    progression = load_character_progression(key)
    profile = progression.baseline
    char = damage._load(ROOT / "assets/data/character_skills" / (key + ".json"))
    build = copy.deepcopy(damage._load(ROOT / "assets/data/character_builds" / (key + ".json")))
    level = profile.character_level or 90
    rank = profile.skill_rank or len(char["skills"][0]["rank_stats"]["levels"])
    column = char["base_stats"]["levels"].index(f"{level}级")
    char["base_stats"] = {
        "levels": [f"{level}级"],
        "rows": {k: [v[column]] for k, v in char["base_stats"]["rows"].items()},
    }
    for node in progression.attribute_nodes:
        for modifier in node["attributeModifiers"]:
            if modifier["modifierType"] != 5 or modifier["attrType"] not in ATTRIBUTES:
                raise ValueError(f"Unsupported fixed attribute node: {node['node_id']}")
            stat = ATTRIBUTES[modifier["attrType"]]
            char["base_stats"]["rows"][stat][0] = float(char["base_stats"]["rows"][stat][0]) + modifier["attrValue"]
    mods = []
    constant_sources = []
    for potential in progression.active_potentials:
        for modifier in potential.modifiers:
            if "attrModifier" not in modifier:
                continue
            attr = modifier["attrModifier"]
            if modifier["activeCondition"] or attr["modifyAttributeType"] != 0:
                raise ValueError(f"Conditional/converted fixed attribute: {potential.effect_id}")
            attr_type, operation, value = attr["attrType"], attr["modifierType"], attr["attrValue"]
            if operation == 5 and attr_type in ATTRIBUTES:
                mods.append({"kind": "flat", "stat": ATTRIBUTES[attr_type], "value": value})
            elif operation == 5 and attr_type == 3:
                mods.append({"kind": "flat", "stat": "防御力", "value": value})
            elif operation == 6 and attr_type == 1:
                mods.append({"kind": "pct", "stat": "hp_pct", "value": value * 100})
            elif operation == 5 and attr_type in (50, 52, 29):
                stat = {50: "elem_物理", 52: "elem_电磁", 29: "heal_eff"}[attr_type]
                mods.append({"kind": "pct", "stat": stat, "value": value * 100})
            else:
                raise ValueError(f"Unsupported fixed potential modifier: {potential.effect_id}/{attr}")
            constant_sources.append(potential.effect_id)
    for skill in char["skills"]:
        stats = skill["rank_stats"]
        stats["levels"] = [stats["levels"][rank - 1]]
        for row in stats["rows"]:
            row["values"] = [row["values"][rank - 1]]
    if profile.weapon_name:
        build["weapon"] = {"name": profile.weapon_name}
    growth = tables["CharGrowthTable"][progression.native_id]
    args = (
        key,
        char,
        build,
        damage._load(ROOT / "assets/data/weapons.json"),
        damage._load(ROOT / "assets/data/equipments.json"),
        {"native": ATTRIBUTES[growth["mainAttrType"]]},
        {char["name"]: ["native"]},
        {"native": ATTRIBUTES[growth["subAttrType"]]},
    )
    result = damage.compute_character(*args, full_overrides={}, additional_mods=mods)
    # The verified Lifeng constant talent reads the completed fixed WIS/WILL panel.
    for talent in progression.talents:
        if talent.effect_id == "chr_0015_lifeng_talent_1_2":
            rate = talent.parameters["atk_up"]
            mods.append(
                {
                    "kind": "pct",
                    "stat": "atk_pct",
                    "value": rate * (result["panel"]["智识"] + result["panel"]["意志"]) * 100,
                }
            )
            constant_sources.append(talent.effect_id)
            result = damage.compute_character(*args, full_overrides={}, additional_mods=mods)
    result["panel"].update(reaction_scalars(progression.native_id, level))
    if key == "lifeng":
        # The fixed talent was folded into attack_percent. Its native converted
        # attribute/snapshot refresh is not yet established for dynamic changes.
        result["attribute_basis"]["unverified_attack_dependencies"] = ["智识", "意志"]
    # Keep the legacy generator/artifact isolated. Fixed quotes and runtime
    # deltas share the reviewed native conversion, retaining raw four-stat totals.
    result["attribute_basis"].update(schema_version=2, attack_conversion="floor_final_main_sub")
    attributes = DamageAttributeBasis.from_dict(result["attribute_basis"])
    result["panel"]["damage_basis"]["attribute_factor"] = attributes.factor()
    fixed_panel = FixedDamagePanel(**result["panel"]["damage_basis"])
    result["panel"]["ATK"] = round(fixed_panel.attack(), 1)
    values = dict(attributes.totals)
    result["trace"] = [line for line in result["trace"] if not line.strip().startswith("攻击力 =")]
    result["trace"].append(
        f"  攻击力 = ({fixed_panel.attack_white} × {1 + fixed_panel.attack_percent}"
        f" + {fixed_panel.attack_flat}) × 原生属性系数 {attributes.factor()} = {fixed_panel.attack():.1f};"
        f" 主属性floor={math.floor(values[attributes.primary])};"
        f" 副属性floor={math.floor(values[attributes.secondary]) if attributes.secondary else 0}"
    )
    for field in ("cycle_expect", "cycle_expect_link4"):
        result.pop(field, None)
    result["trace"] = [
        line for line in result["trace"] if not line.strip().startswith(("循环期望:", "满连击循环期望:"))
    ]
    character = get_character(key, skill_rank=rank, potential=profile.potential)
    fixed_crit, crit_passives = fixed_skill_crit(character)
    constant_sources.extend(passive.effect_id for passive in crit_passives)
    if fixed_crit:
        result["panel"]["damage_basis"]["crit_rate_bonus"] = fixed_crit
        result["trace"].append(f"  [固定潜能技能类型暴击] {fixed_crit}")
    result["profile"] = {
        "character_level": level,
        "skill_rank": rank,
        "potential": profile.potential,
        "potential_basis": profile.potential_basis,
        "all_attribute_nodes": True,
        "constant_passive_sources": sorted(set(constant_sources)),
        "conditional_talent_state": "untriggered",
        "scope": "固定配装与常驻属性；余烬按专用档案，其余沿用原90级及最高技能列口径。技能触发的天赋/潜能、武器/套装增益由战斗状态处理，不预先施加。",
    }
    inputs = {skill["skill_id"]: skill for skill in char["skills"]}
    fixed_bonuses = fixed_weapon_bonuses(character,
                                       {"key": key, "build": {"weapon": build["weapon"]["name"]}})
    result["panel"]["damage_basis"]["damage_bonus"].update(fixed_bonuses)
    if fixed_bonuses:
        result["trace"].append(f"  [固定武器元素/类型过滤] {build['weapon']['name']}: {fixed_bonuses}")
    semantics = damage._load(ROOT / "assets/data/skill_damage_row_semantics.json")
    if semantics["schema_version"] != 1:
        raise ValueError("Unsupported damage row semantics")
    panel = FixedDamagePanel(**result["panel"]["damage_basis"])
    for quote in result["skills"]:
        skill = inputs[quote["skill_id"]]
        multiplier, _, _ = damage._skill_multiplier(skill)
        reviewed = semantics["skills"].get(quote["skill_id"])
        if reviewed:
            if reviewed["character_id"] != key:
                raise ValueError("Damage row semantics character mismatch")
            selected = []
            for label in reviewed["base_rows"]:
                matches = [row for row in skill["rank_stats"]["rows"] if row["label"] == label]
                if len(matches) != 1:
                    raise ValueError(f"Ambiguous/missing reviewed damage row: {quote['skill_id']}/{label}")
                selected.extend(matches)
            multiplier, _, _ = damage._skill_multiplier({"rank_stats": {"rows": selected}})
            quote["row_semantics"] = copy.deepcopy(reviewed)
            for component in quote["row_semantics"]["components"]:
                component["rank_values"] = {}
                for label in component["rows"]:
                    matches = [row for row in skill["rank_stats"]["rows"] if row["label"] == label]
                    if len(matches) != 1:
                        raise ValueError(f"Ambiguous/missing component damage row: {quote['skill_id']}/{label}")
                    component["rank_values"][label] = matches[0]["values"][0]
        tag, element = SKILL_TAGS[skill["skill_type"]], skill["element"]
        non_crit = panel.attack() * multiplier / 100 * (1 + panel.bonus_for(element, (tag,)))
        non_crit *= 1 + panel.amplification[element]
        quote.update(multiplier_pct=round(multiplier, 1), non_crit=round(non_crit, 1),
                     bonus_pct=round(panel.bonus_for(element, (tag,)) * 100, 1),
                     crit_expect=round(non_crit * (1 + min(1, max(0, panel.crit_rate_for((tag,)))) * panel.crit_damage), 1))
        quote["quote_basis"] = {
            "skill_rank": rank,
            "element": skill["element"],
            "damage_tags": [SKILL_TAGS[skill["skill_type"]]],
            "multiplier": multiplier / 100,
            "crit_policy": "baseline_expectation",
            "scope": "reviewed_base_rows" if reviewed else "rank_row_sum_without_conditional_rows",
        }
    result["data_flow"] = {
        "schema_version": 1,
        "sources": source_hashes(key),
        "fixed_passive_sources": result["profile"]["constant_passive_sources"],
        "runtime_modifiers": "not_applied",
        "enemy_basis": "standard_dummy_def0_res0_no_stagger",
    }
    return result


def main():
    tables = json.loads(gzip.decompress((SNAPSHOT / "tables.json.gz").read_bytes()))
    characters = sorted(damage._load(SNAPSHOT / "characters.json"))
    rows = [compute(key, tables) for key in characters]
    output = ROOT / "assets/data/fixed_damage_baseline.json"
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Fixed panels: {len(rows)}; conditional buffs untriggered; no synthetic cycle bundles")


if __name__ == "__main__":
    main()
