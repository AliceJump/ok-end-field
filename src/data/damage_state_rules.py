"""Reviewed fixed weapon clauses and current-target predicates, without procs."""

import re

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_release_rules import ROOT, _read_json


def _selected_perk(character, row, weapon_name, perk, root):
    if row["build"]["weapon"] != weapon_name:
        return None
    build = _read_json(root / f"assets/data/character_builds/{row['key']}.json")
    selected = character.progression.baseline.weapon_name or build["weapon"]["name"]
    if selected != weapon_name:
        raise ValueError("State rules must use the selected fixed weapon")
    weapon = _read_json(root / "assets/data/weapons.json")[weapon_name]
    clauses = [(index, group["ranks"]["Rank 9"][perk])
               for index, group in enumerate(weapon["weapon_skills"])
               if perk in group.get("ranks", {}).get("Rank 9", {})]
    if len(clauses) != 1:
        raise ValueError(f"Missing/ambiguous reviewed weapon perk: {weapon_name}/{perk}")
    index, text = clauses[0]
    return weapon, text, f"weapons/{weapon_name}/weapon_skills/{index}/ranks/Rank 9/{perk}"


def _amount(text, pattern):
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError("Changed reviewed weapon clause")
    return float(matches[0]["amount"]) / 100


def fixed_weapon_bonuses(character, row, *, root=ROOT):
    """Element AND skill-tag filters belong to fixed gear, not dynamic buffs."""
    selected = _selected_perk(character, row, "扶摇", "夜幕·青云", root)
    if selected is None:
        return {}
    _, text, _ = selected
    amount = _amount(text, r"战技和终结技造成的物理伤害\+(?P<amount>[\d.]+)%。")
    return {"物理:skill": amount, "物理:ultimate": amount}


def state_bonus_rules(character, row, *, root=ROOT):
    result = []
    reviewed = (
        ("扶摇", "夜幕·青云", r"对处于失衡状态的敌人造成的伤害\+(?P<amount>[\d.]+)%。", "enemy.staggered"),
        ("负山", "效益·山川在我", r"装备者对处于破防状态的敌人造成的伤害\+(?P<amount>[\d.]+)%。", "enemy.shredded"),
    )
    for name, perk, pattern, predicate in reviewed:
        selected = _selected_perk(character, row, name, perk, root)
        if selected is None:
            continue
        weapon, text, source = selected
        result.append(DamageModifierSpec(
            f"weapon:{weapon['item_id']}:{perk}:{predicate}", DamageBucket.DAMAGE_BONUS,
            ("all",), "self", ModifierMagnitude(_amount(text, pattern)), "battle_start",
            permanent=True, condition_inputs=(predicate,), evaluation="hit", sources=(source,),
        ))
    return tuple(result)
