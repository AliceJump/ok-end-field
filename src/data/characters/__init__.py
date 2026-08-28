"""角色数据包。从JSON文件加载所有角色数据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.data.skill_types import (
    Character,
    Skill,
    SkillType,
    ElementType,
    SkillEnhancement,
    SkillEffect,
)
from src.data.effects import EffectType


# 类型映射
_ELEMENT_MAP: dict[str, ElementType] = {
    "寒冷": ElementType.COLD,
    "灼热": ElementType.BURN,
    "电磁": ElementType.ELECTROMAGNETIC,
    "自然": ElementType.NATURAL,
    "物理": ElementType.PHYSICAL,
}

_SKILL_TYPE_MAP: dict[str, SkillType] = {
    "普通攻击": SkillType.NORMAL_ATTACK,
    "战技": SkillType.SKILL,
    "连携技": SkillType.LINK_SKILL,
    "终结技": SkillType.ULTIMATE,
    "天赋": SkillType.TALENT,
    "潜能": SkillType.POTENTIAL,
}


def _load_skill_effects(effects_data: list[dict]) -> list[SkillEffect]:
    """加载技能效果列表。"""
    effects = []
    for effect_data in effects_data:
        effect = SkillEffect(
            effect_id=EffectType(effect_data["effect_id"]),
            value=effect_data.get("value", 0),
            duration=effect_data.get("duration", ""),
            target=effect_data.get("target", "enemy"),
        )
        effects.append(effect)
    return effects


def _load_character_from_json(file_path: Path) -> Character:
    """从JSON文件加载角色数据。"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 解析技能列表
    skills: list[Skill] = []
    for skill_data in data.get("skills", []):
        enhancement = None
        if skill_data.get("enhancement"):
            enh_data = skill_data["enhancement"]
            enhancement = SkillEnhancement(
                name=enh_data["name"],
                trigger_condition=enh_data["trigger_condition"],
                effects=_load_skill_effects(enh_data.get("effects", [])),
                enhancement_visible_pulse=enh_data.get("enhancement_visible_pulse", False),
            )

        # 加载技能基础效果
        effects = _load_skill_effects(skill_data.get("effects", []))

        skill = Skill(
            skill_id=skill_data["skill_id"],
            name=skill_data["name"],
            skill_type=_SKILL_TYPE_MAP[skill_data["skill_type"]],
            element=_ELEMENT_MAP[skill_data["element"]],
            has_enhancement=skill_data["has_enhancement"],
            enhancement=enhancement,
            effects=effects,
            description=skill_data.get("description", ""),
            damage_multiplier=skill_data.get("damage_multiplier", ""),
            stagger_value=skill_data.get("stagger_value", 0),
            cooldown=skill_data.get("cooldown", ""),
            spirit_cost=skill_data.get("spirit_cost", 0),
        )
        skills.append(skill)

    return Character(
        character_id=data["character_id"],
        name=data["name"],
        star=data["star"],
        element=_ELEMENT_MAP[data["element"]],
        profession=data["profession"],
        weapon_type=data["weapon_type"],
        skills=skills,
    )


def load_all_characters() -> dict[str, Character]:
    """加载所有角色数据。"""
    characters: dict[str, Character] = {}
    characters_dir = Path(__file__).parent

    for json_file in sorted(characters_dir.glob("*.json")):
        character = _load_character_from_json(json_file)
        characters[character.character_id] = character

    return characters


def get_character(character_id: str) -> Optional[Character]:
    """获取指定角色。"""
    all_chars = load_all_characters()
    return all_chars.get(character_id)


__all__ = [
    "load_all_characters",
    "get_character",
]
