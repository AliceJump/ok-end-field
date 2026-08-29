"""技能数据库类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.data.effects import EffectType


class SkillType(Enum):
    """技能类型。"""
    NORMAL_ATTACK = "普通攻击"
    SKILL = "战技"
    LINK_SKILL = "连携技"
    ULTIMATE = "终结技"
    TALENT = "天赋"
    POTENTIAL = "潜能"


class ElementType(Enum):
    """元素类型。"""
    COLD = "寒冷"
    BURN = "灼热"
    ELECTROMAGNETIC = "电磁"
    NATURAL = "自然"
    PHYSICAL = "物理"


class ConditionType(Enum):
    """条件逻辑类型。"""
    AND = "AND"  # 多个条件必须同时满足
    OR = "OR"  # 满足任意一个
    ANY = "ANY"  # 任意技能/事件


@dataclass
class SkillEffect:
    """技能原子化效果。"""
    effect_id: EffectType  # 效果ID
    value: int = 0  # 效果值（如层数、百分比等）
    duration: str = ""  # 持续时间
    target: str = "enemy"  # 效果目标：enemy/ally/self


@dataclass
class SkillEnhancement:
    """战技强化态信息（战技的条件触发效果）。"""
    name: str  # 强化状态名称
    trigger_condition: str  # 触发条件（如"命中处于寒冷附着或自然附着的敌人时"）
    trigger_effects: list[EffectType] = field(default_factory=list)  # 触发条件关联的效果 ID
    effects: list[SkillEffect] = field(default_factory=list)  # 强化效果列表
    enhancement_visible_pulse: bool = False  # 强化状态可见脉冲


@dataclass
class SkillReaction:
    """技能反应/组合效果。"""
    reaction_id: str  # 反应ID
    name: str  # 反应名称
    trigger_condition: str  # 触发条件
    condition_type: ConditionType  # 条件类型
    effects: list[SkillEffect] = field(default_factory=list)  # 效果列表
    trigger_effects: list[EffectType] = field(default_factory=list)  # 触发条件关联的效果 ID
    order_requirement: Optional[str] = None  # 顺序要求
    time_window: Optional[str] = None  # 时间窗口
    stack_requirement: Optional[int] = None  # 层数要求
    count_requirement: Optional[int] = None  # 次数要求


@dataclass
class Skill:
    """技能信息。"""
    skill_id: str  # 技能ID
    name: str  # 技能名称
    skill_type: SkillType  # 技能类型
    element: ElementType  # 元素类型
    has_enhancement: bool  # 是否存在强化态
    enhancement: Optional[SkillEnhancement] = None  # 强化态信息
    effects: list[SkillEffect] = field(default_factory=list)  # 技能基础效果列表
    description: str = ""  # 技能描述
    damage_multiplier: str = ""  # 伤害倍率
    stagger_value: int = 0  # 失衡值
    cooldown: str = ""  # 冷却时间
    spirit_cost: int = 0  # 技力消耗


@dataclass
class Character:
    """角色信息。"""
    character_id: str  # 角色ID
    name: str  # 角色名称
    star: int  # 星级
    element: ElementType  # 元素类型
    profession: str  # 职业
    weapon_type: str  # 武器类型
    skills: list[Skill] = field(default_factory=list)  # 技能列表


@dataclass
class AutoReleaseRestriction:
    """自动释放限制。"""
    skill_id: str  # 技能ID
    enhancement_source: str  # 强化来源
    should_forbid_normal_release: bool  # 是否影响普通释放
    reason: str  # 原因


@dataclass
class DetectionInfo:
    """自动化检测信息。"""
    state: str  # 状态
    detection_location: str  # 检测位置
    stability: str  # 稳定性
    recommended_method: str  # 推荐方案
    observation_class: str  # 可观测性分类（A类/B类）
