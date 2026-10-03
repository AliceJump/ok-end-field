"""Precompute fixed build/progression panels; no assumed rotations or temporary buffs."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import compute_damage_baseline as damage

from src.data.character_progression import SNAPSHOT, load_character_progression

ROOT = Path(__file__).resolve().parents[2]
ATTRIBUTES = {39: "力量", 40: "敏捷", 41: "智识", 42: "意志"}


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
    for field in ("cycle_expect", "cycle_expect_link4"):
        result.pop(field, None)
    result["trace"] = [
        line for line in result["trace"] if not line.strip().startswith(("循环期望:", "满连击循环期望:"))
    ]
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
