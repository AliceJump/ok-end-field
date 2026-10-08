"""Reviewed release-only bonuses, separate from hit/field/outcome inference.

The model's successful action start is its release event. This does not prove
the native animation frame. Only reviewed 'on release' rules are bound here;
Prose 'after' needs native event evidence; hits and auras need their own producer.
"""

import json
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude

ROOT = Path(__file__).resolve().parents[2]
KINDS = {"战技": "battle", "连携技": "link", "终结技": "ult"}
CAST_EVENTS = {"battle": "battle_cast", "link": "combo_cast", "ult": "ultimate_cast"}
SPELL_ELEMENTS = ("寒冷", "灼热", "电磁", "自然")

# These exact canonical skills have unconditional team amplification on release.
# The general on_cast label alone is not sufficient evidence for other skills.
REVIEWED_SKILLS = {"antal_ultimate", "xaihi_ultimate"}
WEAPON_PATTERNS = {
    ("J.E.T.", "压制·太空物理学"): (
        ("battle", r"装备者施放战技时，获得法术伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。", SPELL_ELEMENTS, ()),
        ("link", r"装备者施放连携技时，获得法术伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。", SPELL_ELEMENTS, ()),
    ),
    ("熔铸火焰", "夜幕·嘶鸣烈火"): (
        ("ult", r"装备者施放终结技时，获得普通攻击伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。", ("all",), ("normal",)),
    ),
    ("遗忘", "夜幕·耻辱"): (
        ("ult", r"装备者施放终结技时，获得法术伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒", SPELL_ELEMENTS, ()),
        ("link", r"装备者施放连携技时，获得法术伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。", SPELL_ELEMENTS, ()),
    ),
}


@lru_cache(maxsize=8)
def _read_json_revision(path, mtime_ns, size):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json(path):
    # Catalogs/audits select many actors from the same large source files.
    # A changed source revision is re-read rather than hidden by a path cache.
    stat = path.stat()
    return _read_json_revision(path, stat.st_mtime_ns, stat.st_size)


def release_rules(character, row, *, root=ROOT):
    """Return selected, independently sourced bindings; never apply any buff."""
    result = {kind: [] for kind in CAST_EVENTS}
    for skill in character.skills:
        if skill.skill_id not in REVIEWED_SKILLS:
            continue
        kind = KINDS[skill.skill_type.value]
        for effect in skill.effects:
            spec = effect.damage_modifier
            if spec is not None and spec.trigger == "on_cast":
                result[kind].append(replace(spec, trigger=CAST_EVENTS[kind]))
    # Read the actual fixed build, including Ember's baseline weapon override.
    build = _read_json(root / f"assets/data/character_builds/{row['key']}.json")
    name = character.progression.baseline.weapon_name or build["weapon"]["name"]
    if row["build"]["weapon"] != name:
        raise ValueError("Release rules must use the selected fixed weapon")
    weapons = _read_json(root / "assets/data/weapons.json")
    weapon = weapons[name]
    if name == "孤舟":
        from src.data.reviewed_weapon_release import guzhou_ultimate_rule

        result["ult"].append(guzhou_ultimate_rule(weapon, build, root=root))
    for (reviewed_name, perk), patterns in WEAPON_PATTERNS.items():
        if name != reviewed_name:
            continue
        entries = [(index, group["ranks"]["Rank 9"][perk])
                   for index, group in enumerate(weapon["weapon_skills"])
                   if perk in group.get("ranks", {}).get("Rank 9", {})]
        if len(entries) != 1:
            raise ValueError(f"Missing/ambiguous reviewed weapon perk: {name}/{perk}")
        index, text = entries[0]
        if "两种效果独立生效，且均无法叠加" not in text and "同名效果无法叠加" not in text:
            raise ValueError(f"Unverified release bonus stacking: {name}/{perk}")
        for kind, pattern, elements, tags in patterns:
            matches = list(re.finditer(pattern, text))
            if len(matches) != 1:
                raise ValueError(f"Changed reviewed release clause: {name}/{perk}/{kind}")
            match = matches[0]
            source = f"weapons/{name}/weapon_skills/{index}/ranks/Rank 9/{perk}"
            result[kind].append(DamageModifierSpec(
                f"weapon:{weapon['item_id']}:{perk}:{kind}", DamageBucket.DAMAGE_BONUS,
                elements, "self", ModifierMagnitude(float(match["amount"]) / 100), CAST_EVENTS[kind],
                damage_tags=tags, duration=float(match["duration"]), sources=(source,),
            ))
    gear = _read_json(root / "assets/data/equipments.json")
    pieces = [name for name in row["build"]["pieces"] if name in gear and gear[name]["set"] == "清波装备组"]
    if len(pieces) >= 3:
        clauses = {gear[name]["set_effect"] for name in pieces}
        if len(clauses) != 1:
            raise ValueError("Ambiguous reviewed Clearwave set effect")
        text = clauses.pop()
        pattern = (r"当装备者施放连携技时，所有技能伤害\+(?P<amount>[\d.]+)%，持续(?P<duration>[\d.]+)秒。"
                   r"该效果最多叠加(?P<stacks>\d+)层，每层单独计算持续时间。")
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise ValueError("Changed reviewed Clearwave release bonus")
        match = matches[0]
        result["link"].append(DamageModifierSpec(
            "set:清波:release_damage", DamageBucket.DAMAGE_BONUS, ("all",), "self",
            ModifierMagnitude(float(match["amount"]) / 100), CAST_EVENTS["link"],
            damage_tags=("normal", "skill", "combo", "ultimate"), duration=float(match["duration"]),
            max_stacks=int(match["stacks"]), stack_policy="independent",
            sources=(f"equipments/{pieces[0]}/set_effect",),
        ))
    return {kind: tuple(specs) for kind, specs in result.items()}
