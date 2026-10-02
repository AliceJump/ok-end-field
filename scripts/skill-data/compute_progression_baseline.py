"""Compute the explicit progression comparison without replacing rotation data.

This is damage against an unbuffed DEF/RES=0 target. Conditional combat talents
are retained in the output, with their triggers; their rewards need phase two.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data.character_progression import SNAPSHOT, load_character_progression  # noqa: E402

spec = importlib.util.spec_from_file_location("damage_baseline", Path(__file__).with_name("compute_damage_baseline.py"))
damage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(damage)
ATTRIBUTES = {39: "力量", 40: "敏捷", 41: "智识", 42: "意志"}


def comparison(key: str) -> dict:
    progression = load_character_progression(key)
    if progression is None:
        raise ValueError(f"No progression data: {key}")
    profile = progression.baseline
    if profile.character_level is None or profile.skill_rank is None or profile.weapon_name is None:
        raise ValueError("Comparison requires an explicit character level, skill rank and weapon")
    char = damage._load(ROOT / "assets/data/character_skills" / (key + ".json"))
    build = damage._load(ROOT / "assets/data/character_builds" / (key + ".json"))
    tables = json.loads(gzip.decompress((SNAPSHOT / "tables.json.gz").read_bytes()))
    growth = tables["CharGrowthTable"][progression.native_id]
    char, build = copy.deepcopy(char), copy.deepcopy(build)
    base = char["base_stats"]
    column = base["levels"].index(f"{profile.character_level}级")
    base["levels"] = [base["levels"][column]]
    base["rows"] = {k: [v[column]] for k, v in base["rows"].items()}
    for node in progression.attribute_nodes:
        for modifier in node["attributeModifiers"]:
            if modifier["modifierType"] != 5 or modifier["attrType"] not in ATTRIBUTES:
                raise ValueError(f"Unsupported attribute node: {node['node_id']}")
            attr = ATTRIBUTES[modifier["attrType"]]
            base["rows"][attr][0] = float(base["rows"][attr][0]) + modifier["attrValue"]
    for skill in char["skills"]:
        stats = skill["rank_stats"]
        stats["levels"] = [stats["levels"][profile.skill_rank - 1]]
        for row in stats["rows"]:
            row["values"] = [row["values"][profile.skill_rank - 1]]
    build["weapon"] = {
        "name": profile.weapon_name,
        "level_max": 90,
        "skill_rank": 9,
        "source": "local CharWpnRecommendTable / WeaponBasicTable",
    }
    build["matrix"] = {"name": None, "source": "weapon skills explicitly fixed at Rank9"}
    result = damage.compute_character(
        key,
        char,
        build,
        damage._load(ROOT / "assets/data/weapons.json"),
        damage._load(ROOT / "assets/data/equipments.json"),
        {"native": ATTRIBUTES[growth["mainAttrType"]]},
        {char["name"]: ["native"]},
        {"native": ATTRIBUTES[growth["subAttrType"]]},
    )
    result["profile"] = {
        "character_level": profile.character_level,
        "skill_rank": profile.skill_rank,
        "potential": profile.potential,
        "weapon_level": 90,
        "weapon_skill_rank": 9,
        "all_attribute_nodes": True,
        "all_combat_talents_max_rank": True,
        "combat_talents": [
            {"id": t.effect_id, "level": t.level, "description": t.description} for t in progression.talents
        ],
        "conditional_talent_state": "untriggered",
        "note": "沿用现有推荐装备及精锻口径；这只是静态直接伤害，不含破防链、后续解锁、动画时间或SP机会成本。受击天赋保留触发规则，标准假人未受击时不提前施加攻击增益。",
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--char", default="ember", choices=sorted(damage._load(SNAPSHOT / "characters.json")))
    parser.add_argument("--out", type=Path, default=ROOT / "assets/data/ember_progression_baseline.json")
    args = parser.parse_args()
    if args.out.resolve().parent != (ROOT / "assets/data").resolve():
        parser.error("--out must be directly inside assets/data")
    result = comparison(args.char)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"{result['character']}: ATK={result['panel']['ATK']}; level={result['profile']['character_level']}, rank={result['profile']['skill_rank']}, P{result['profile']['potential']}; {result['build']['weapon']}"
    )


if __name__ == "__main__":
    main()
