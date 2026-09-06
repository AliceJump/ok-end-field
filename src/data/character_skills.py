"""角色数据模块。从JSON文件加载所有角色技能数据。"""

from __future__ import annotations

import json
from pathlib import Path

from src.data.effects import EffectType, match_effect_terms
from src.data.skill_types import (
    Character,
    ElementType,
    Skill,
    SkillEffect,
    SkillEnhancement,
    SkillType,
    TriggerEffectGroup,
)

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
            count=effect_data.get("count", 1),
        )
        effects.append(effect)
    return effects


def _skill_type_of(skill_type: str) -> SkillType:
    """将技能类型字符串映射为 SkillType；未知时抛 KeyError。"""
    return _SKILL_TYPE_MAP[skill_type]


def _element_of(element: str, fallback: ElementType = ElementType.PHYSICAL) -> ElementType:
    """将元素字符串映射为 ElementType；缺失/未知时返回 fallback。"""
    try:
        return _ELEMENT_MAP[element]
    except (KeyError, TypeError):
        return fallback


def _default_skill_id(character_id: str, skill_type: str) -> str:
    """旧格式无 skill_id 时，按 <角色id>_<类型> 生成稳定的 skill_id。"""
    suffix = {
        "普通攻击": "normal",
        "战技": "skill",
        "连携技": "link",
        "终结技": "ultimate",
        "天赋": "talent",
        "潜能": "potential",
    }.get(skill_type, "skill")
    return f"{character_id}_{suffix}"


def _load_trigger_condition(
    trigger_data,
) -> tuple[str, list[EffectType], list[TriggerEffectGroup]]:
    """解析触发条件：兼容字符串与新格式 {"text", "effects"}。

    返回 (文本, 效果ID列表, 带运算符的效果组)。新格式优先使用显式 effects；
    仅字符串时回退用 match_effect_terms 自动解析。
    """
    if isinstance(trigger_data, dict):
        text = trigger_data.get("text", "")
        raw_effects = trigger_data.get("effects") or []
        if isinstance(raw_effects, dict):
            trigger_effect_groups = [
                TriggerEffectGroup(
                    operator=operator,
                    effects=tuple(EffectType(effect_id) for effect_id in raw_effects.get(operator) or []),
                )
                for operator in ("all", "any")
                if raw_effects.get(operator) or (operator == "all" and operator in raw_effects)
            ]
            effects = [effect for group in trigger_effect_groups for effect in group.effects]
        else:
            effects = [EffectType(effect_id) for effect_id in raw_effects]
            trigger_effect_groups = []
        return text, effects, trigger_effect_groups
    text = trigger_data or ""
    return text, [eff for _, eff in match_effect_terms(text)], []


def _load_enhancement(enh_data: dict) -> SkillEnhancement:
    """加载一个独立条件效果。"""
    trigger_condition, trigger_effects, trigger_effect_groups = _load_trigger_condition(
        enh_data.get("trigger_condition", ""),
    )
    return SkillEnhancement(
        name=enh_data["name"],
        trigger_condition=trigger_condition,
        trigger_effects=trigger_effects,
        trigger_effect_groups=trigger_effect_groups,
        effects=_load_skill_effects(enh_data.get("effects") or []),
        enhancement_visible_pulse=enh_data.get("enhancement_visible_pulse", False),
    )


def _load_character_from_json(file_path: Path) -> Character:
    """从JSON文件加载角色数据（兼容新旧两种格式）。"""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    character_id = data["character_id"]
    character_element = _element_of(data.get("element", ""))

    # 解析技能列表
    skills: list[Skill] = []
    for skill_data in data.get("skills", []):
        enhancements = [_load_enhancement(enh_data) for enh_data in skill_data.get("enhancements") or []]

        # 加载技能基础效果；旧格式的 attach/status/clear 纯ID列表合并进 effects
        effects = _load_skill_effects(skill_data.get("effects") or [])
        legacy_ids = []
        for legacy_key in ("attach_effects", "status_effects", "clear_effects"):
            legacy_ids.extend(skill_data.get(legacy_key, []) or [])
        for legacy_id in legacy_ids:
            if all(e.effect_id.value != legacy_id for e in effects):
                try:
                    effects.append(SkillEffect(effect_id=EffectType(legacy_id)))
                except ValueError:
                    pass

        skill_type = _skill_type_of(skill_data["skill_type"])
        skill = Skill(
            skill_id=skill_data.get("skill_id") or _default_skill_id(character_id, skill_data["skill_type"]),
            name=skill_data["name"],
            skill_type=skill_type,
            element=_element_of(skill_data.get("element", ""), character_element),
            # 已解析分支是唯一真源；JSON 中的旧布尔标记不参与运行时判定。
            enhancements=enhancements,
            effects=effects,
            description=skill_data.get("description", ""),
            damage_multiplier=skill_data.get("damage_multiplier", ""),
            stagger_value=skill_data.get("stagger_value", 0),
            cooldown=skill_data.get("cooldown", ""),
            spirit_cost=skill_data.get("spirit_cost", 0),
        )
        skills.append(skill)

    return Character(
        character_id=character_id,
        name=data["name"],
        star=data.get("star", 0),
        element=character_element,
        profession=data.get("profession", ""),
        weapon_type=data.get("weapon_type", ""),
        skills=skills,
    )


def load_all_characters() -> dict[str, Character]:
    """加载所有角色数据。"""
    characters: dict[str, Character] = {}
    # JSON 文件位于 assets/data/character_skills/ 目录
    characters_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "data" / "character_skills"

    for json_file in sorted(characters_dir.glob("*.json")):
        character = _load_character_from_json(json_file)
        characters[character.character_id] = character

    return characters


def get_character(character_id: str) -> Character | None:
    """获取指定角色。"""
    all_chars = load_all_characters()
    return all_chars.get(character_id)


__all__ = [
    "get_character",
    "load_all_characters",
]
