"""技能数据库类型定义。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

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
    count: int | None = 1  # 施加/消耗计数；None=数量由运行时状态动态决定


@dataclass(frozen=True)
class TriggerEffectGroup:
    """保留触发效果组的逻辑运算符。"""

    operator: Literal["all", "any"]
    effects: tuple[EffectType, ...]

    def is_satisfied(
        self,
        current_effects: Collection[EffectType] | Mapping[EffectType, int],
    ) -> bool:
        """按组运算符判断当前效果是否满足触发条件。"""
        required = set(self.effects)
        available = set(current_effects)
        if EffectType.STACK_SIGN in required:
            sign_count = (
                current_effects.get(EffectType.STACK_SIGN, 0)
                if isinstance(current_effects, Mapping)
                else sum(effect == EffectType.STACK_SIGN for effect in current_effects)
            )
            if sign_count < 8:
                available.discard(EffectType.STACK_SIGN)
        return required <= available if self.operator == "all" else bool(required & available)


@dataclass
class SkillEnhancement:
    """战技强化态信息（战技的条件触发效果）。"""

    name: str  # 强化状态名称
    trigger_condition: str  # 触发条件（如"命中处于寒冷附着或自然附着的敌人时"）
    trigger_effects: list[EffectType] = field(default_factory=list)  # 触发条件关联的效果 ID
    effects: list[SkillEffect] = field(default_factory=list)  # 强化效果列表
    enhancement_visible_pulse: bool = False  # 强化状态可见脉冲
    trigger_effect_groups: list[TriggerEffectGroup] = field(default_factory=list)  # 带 all/any 语义的条件组

    def is_trigger_satisfied(
        self,
        current_effects: Collection[EffectType] | Mapping[EffectType, int],
    ) -> bool:
        """所有条件组均满足时触发；组内按各自的 all/any 运算。"""
        return bool(self.trigger_effect_groups) and all(
            group.is_satisfied(current_effects) for group in self.trigger_effect_groups
        )


@dataclass
class SkillReaction:
    """技能反应/组合效果。"""

    reaction_id: str  # 反应ID
    name: str  # 反应名称
    trigger_condition: str  # 触发条件
    condition_type: ConditionType  # 条件类型
    effects: list[SkillEffect] = field(default_factory=list)  # 效果列表
    trigger_effects: list[EffectType] = field(default_factory=list)  # 触发条件关联的效果 ID
    order_requirement: str | None = None  # 顺序要求
    time_window: str | None = None  # 时间窗口
    stack_requirement: int | None = None  # 层数要求
    count_requirement: int | None = None  # 次数要求


@dataclass
class Skill:
    """技能信息。"""

    skill_id: str  # 技能ID
    name: str  # 技能名称
    skill_type: SkillType  # 技能类型
    element: ElementType  # 元素类型
    enhancements: list[SkillEnhancement] = field(default_factory=list)  # 全部独立条件效果
    effects: list[SkillEffect] = field(default_factory=list)  # 技能基础效果列表
    description: str = ""  # 技能描述
    damage_multiplier: str = ""  # 伤害倍率
    stagger_value: int = 0  # 失衡值
    cooldown: str = ""  # 冷却时间
    spirit_cost: int = 0  # 技力消耗

    @property
    def enhancement(self) -> SkillEnhancement | None:
        """旧单分支接口，始终指向 enhancements 的首项。"""
        return self.enhancements[0] if self.enhancements else None

    @property
    def has_enhancement(self) -> bool:
        """是否存在已解析的条件效果分支。"""
        return bool(self.enhancements)


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
