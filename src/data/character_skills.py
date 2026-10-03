"""角色数据模块。从JSON文件加载所有角色技能数据。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from src.data.character_progression import load_character_progression
from src.data.damage_modifiers import bind_damage_modifier
from src.data.effects import EffectType, match_effect_terms
from src.data.skill_types import (
    Character,
    ElementType,
    Skill,
    SkillEffect,
    SkillEnhancement,
    SkillResourceChange,
    SkillType,
    TriggerEffectGroup,
    TriggerEffectRequirement,
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


def _load_skill_effects(effects_data: list[dict], *, skill_id="", skills=None, rank=None, progression=None) -> list[SkillEffect]:
    """加载技能效果列表，严格保留“未知”和“未声明”。

    旧实现会把缺失字段静默补成 value=0 / duration="" / target=enemy / count=1，
    这会把不完整数据伪装成确定机制。现在只有 JSON 明确给出的值才进入模型。
    空字符串 duration 统一归一为 None。
    """
    effects = []
    for effect_data in effects_data:
        duration = effect_data.get("duration") if "duration" in effect_data else None
        if duration == "":
            duration = None
        effect = SkillEffect(
            effect_id=EffectType(effect_data["effect_id"]),
            value=effect_data.get("value") if "value" in effect_data else None,
            duration=duration,
            target=effect_data.get("target") if "target" in effect_data else None,
            count=effect_data.get("count") if "count" in effect_data else None,
            consumes_all=bool(effect_data.get("consumes_all", False)),
            subject_effect_id=(
                EffectType(effect_data["subject_effect_id"])
                if effect_data.get("subject_effect_id")
                else None
            ),
            damage_modifier=(
                bind_damage_modifier(effect_data["damage_modifier"], skill_id=skill_id,
                                     skills=skills or {}, rank=rank, progression=progression)
                if effect_data.get("damage_modifier") else None
            ),
        )
        effects.append(effect)
    return effects


def _load_resource_changes(changes_data: list[dict], *, rank=None) -> list[SkillResourceChange]:
    """加载技力/SP 与终结技能量变化规则。"""
    changes = []
    for data in changes_data:
        resolved = dict(data)
        reference = resolved.pop("ranked_amount", None)
        if reference is not None:
            from src.data.skill_timing import load_skill_timings

            resolved["amount"] = load_skill_timings().ranked_parameter(
                reference["native_skill"], reference["parameter"], rank
            )
        changes.append(SkillResourceChange.from_dict(resolved))
    return changes


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
            # {"all": []} / {"any": []} 不是“永远满足”的条件。
            # 条件项既兼容旧字符串，也支持 {"effect_id": "...", "min_count": N}。
            trigger_effect_groups = []
            for operator in ("all", "any"):
                raw_requirements = raw_effects.get(operator) or []
                if not raw_requirements:
                    continue
                requirements = []
                for raw_requirement in raw_requirements:
                    if isinstance(raw_requirement, str):
                        effect_id = raw_requirement
                        min_count = 1
                    else:
                        effect_id = raw_requirement["effect_id"]
                        min_count = int(raw_requirement.get("min_count", 1))
                    requirements.append(
                        TriggerEffectRequirement(
                            effect=EffectType(effect_id),
                            min_count=min_count,
                        )
                    )
                trigger_effect_groups.append(
                    TriggerEffectGroup(
                        operator=operator,
                        requirements=tuple(requirements),
                    )
                )
            effects = [effect for group in trigger_effect_groups for effect in group.effects]
        else:
            effects = [EffectType(effect_id) for effect_id in raw_effects]
            trigger_effect_groups = []
        return text, effects, trigger_effect_groups
    text = trigger_data or ""
    return text, [eff for _, eff in match_effect_terms(text)], []


def _load_enhancement(enh_data: dict, **effect_context) -> SkillEnhancement:
    """加载一个独立条件效果。"""
    trigger_condition, trigger_effects, trigger_effect_groups = _load_trigger_condition(
        enh_data.get("trigger_condition", ""),
    )
    return SkillEnhancement(
        name=enh_data["name"],
        trigger_condition=trigger_condition,
        trigger_effects=trigger_effects,
        trigger_effect_groups=trigger_effect_groups,
        effects=_load_skill_effects(enh_data.get("effects") or [], **effect_context),
        resource_changes=_load_resource_changes(enh_data.get("resource_changes") or [], rank=effect_context.get("rank")),
        replaces_base_action=bool(enh_data.get("replaces_base_action", False)),
        spirit_cost_override=enh_data.get("spirit_cost_override"),
        damage_multiplier_override=enh_data.get("damage_multiplier_override"),
        stagger_value_override=enh_data.get("stagger_value_override"),
        enhancement_visible_pulse=enh_data.get("enhancement_visible_pulse", False),
        evaluation_point=enh_data.get("evaluation_point"),
    )


def _load_character_from_json(file_path: Path, *, skill_rank: int | None = None, potential: int | None = None) -> Character:
    """从JSON文件加载角色数据（兼容新旧两种格式）。"""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)

    character_id = data["character_id"]
    character_element = _element_of(data.get("element", ""))
    progression = load_character_progression(character_id)
    if potential is not None:
        if not isinstance(potential, int) or not 0 <= potential <= 5 or progression is None:
            raise ValueError("Potential override requires native progression and an integer in 0..5")
        progression = replace(progression, baseline=replace(progression.baseline, potential=potential, potential_basis="explicit_override"))
    skill_rows = {s.get("skill_id") or _default_skill_id(character_id, s["skill_type"]): s for s in data.get("skills", [])}

    # 解析技能列表
    skills: list[Skill] = []
    for skill_data in data.get("skills", []):
        effect_context = {"skill_id": skill_data.get("skill_id") or _default_skill_id(character_id, skill_data["skill_type"]),
                          "skills": skill_rows, "rank": skill_rank, "progression": progression}
        enhancements = [_load_enhancement(enh_data, **effect_context) for enh_data in skill_data.get("enhancements") or []]

        # 加载技能基础效果；旧格式的 attach/status/clear 纯ID列表合并进 effects
        effects = _load_skill_effects(skill_data.get("effects") or [], **effect_context)
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
            resource_changes=_load_resource_changes(skill_data.get("resource_changes") or [], rank=skill_rank),
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
        progression=progression,
    )


def load_all_characters(*, skill_rank: int | None = None) -> dict[str, Character]:
    """加载所有角色数据。"""
    characters: dict[str, Character] = {}
    # JSON 文件位于 assets/data/character_skills/ 目录
    characters_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "data" / "character_skills"

    for json_file in sorted(characters_dir.glob("*.json")):
        character = _load_character_from_json(json_file, skill_rank=skill_rank)
        characters[character.character_id] = character

    return characters


def get_character(character_id: str, *, skill_rank: int | None = None, potential: int | None = None) -> Character | None:
    """获取指定角色。"""
    if not re.fullmatch(r"[a-z0-9_]+", character_id):
        return None
    path = Path(__file__).resolve().parents[2] / "assets/data/character_skills" / (character_id + ".json")
    return _load_character_from_json(path, skill_rank=skill_rank, potential=potential) if path.is_file() else None


__all__ = [
    "get_character",
    "load_all_characters",
]
