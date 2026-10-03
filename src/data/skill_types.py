"""技能数据库类型定义。"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from src.data.character_progression import CharacterProgression
from src.data.damage_modifiers import DamageModifierSpec
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


class CombatResourceType(Enum):
    """战斗中的数值资源。"""

    SKILL_POINT = "skill_point"  # 全队共享技力/SP
    ULTIMATE_ENERGY = "ultimate_energy"  # 角色自己的终结技能量


class ResourceChangeKind(Enum):
    """资源变化的计算方式。"""

    FIXED = "fixed"
    PER_HIT = "per_hit"
    PER_EFFECT_COUNT = "per_effect_count"
    PIECEWISE_BY_COUNT = "piecewise_by_count"
    DYNAMIC = "dynamic"


@dataclass
class SkillResourceChange:
    """技能导致的技力/终结技能量变化。

    先把真实语义结构化保存；复杂公式允许暂存 formula，
    下一阶段 TeamCombatState/ActionOutcome 再负责执行。
    """

    resource: CombatResourceType
    target: str  # team / self
    kind: ResourceChangeKind
    amount: int | float | None = None
    per_unit: int | float | None = None
    source_effect_id: EffectType | None = None
    max_units: int | None = None
    values_by_count: dict[int, int | float] = field(default_factory=dict)
    formula: str | None = None
    trigger: str | None = None  # 命中/击杀/每次事件等；None 不代表无条件结算
    count_basis: str | None = None  # consumed / before_action / hit_targets 等快照口径
    affected_by_energy_gain: bool | None = None
    max_amount: int | float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> SkillResourceChange:
        return cls(
            resource=CombatResourceType(data["resource"]), target=data["target"],
            kind=ResourceChangeKind(data["kind"]), amount=data.get("amount"),
            per_unit=data.get("per_unit"),
            source_effect_id=EffectType(data["source_effect_id"]) if data.get("source_effect_id") else None,
            max_units=data.get("max_units"),
            values_by_count={int(k): v for k, v in (data.get("values_by_count") or {}).items()},
            formula=data.get("formula"), trigger=data.get("trigger"),
            count_basis=data.get("count_basis"), affected_by_energy_gain=data.get("affected_by_energy_gain"),
            max_amount=data.get("max_amount"),
        )


@dataclass
class SkillEffect:
    """技能原子化效果。

    None 表示“数据源没有给出/当前模型无法静态确定”，不得等价成 0、1 或 enemy。
    动态公式（例如“导电异常等级+1”）同样保留为 None，由机制层在运行时计算。
    """

    effect_id: EffectType
    value: int | float | str | None = None
    duration: int | float | str | None = None
    target: str | None = None  # enemy/ally/self/team/field；None=未声明
    count: int | None = None  # 正=施加/增加，负=消费/减少，None=未知或动态
    subject_effect_id: EffectType | None = None  # operation/predicate 所指向的具体资源/状态
    damage_modifier: DamageModifierSpec | None = None
    consumes_all: bool = False


@dataclass(frozen=True)
class TriggerEffectRequirement:
    """单个效果条件，可携带最低层数/数量要求。"""

    effect: EffectType
    min_count: int = 1

    def is_satisfied(
        self,
        current_effects: Collection[EffectType] | Mapping[EffectType, int],
    ) -> bool:
        if isinstance(current_effects, Mapping):
            return current_effects.get(self.effect, 0) >= self.min_count
        if self.min_count > 1:
            # 无计数信息的集合不能证明高层数条件成立。
            return False
        return self.effect in current_effects


@dataclass(frozen=True)
class TriggerEffectGroup:
    """保留触发效果组的逻辑运算符和各效果最低层数。"""

    operator: Literal["all", "any"]
    requirements: tuple[TriggerEffectRequirement, ...]

    @property
    def effects(self) -> tuple[EffectType, ...]:
        """兼容旧调用方的只读效果 ID 视图。"""
        return tuple(requirement.effect for requirement in self.requirements)

    def is_satisfied(
        self,
        current_effects: Collection[EffectType] | Mapping[EffectType, int],
    ) -> bool:
        results = [requirement.is_satisfied(current_effects) for requirement in self.requirements]
        return all(results) if self.operator == "all" else any(results)


@dataclass
class SkillEnhancement:
    """战技强化态信息（战技的条件触发效果）。"""

    name: str  # 强化状态名称
    trigger_condition: str  # 触发条件（如"命中处于寒冷附着或自然附着的敌人时"）
    trigger_effects: list[EffectType] = field(default_factory=list)  # 触发条件关联的效果 ID
    effects: list[SkillEffect] = field(default_factory=list)  # 强化效果列表
    resource_changes: list[SkillResourceChange] = field(default_factory=list)
    replaces_base_action: bool = False  # True=这是技能替换动作，不能与基础 action 的 effects/cost 同时结算
    spirit_cost_override: int | None = None
    damage_multiplier_override: str | None = None
    stagger_value_override: int | None = None
    enhancement_visible_pulse: bool = False  # 强化状态可见脉冲
    trigger_effect_groups: list[TriggerEffectGroup] = field(default_factory=list)  # 带 all/any 语义的条件组
    evaluation_point: str | None = None  # before_action / on_hit / on_normal_attack 等

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
    resource_changes: list[SkillResourceChange] = field(default_factory=list)
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
    progression: CharacterProgression | None = None


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
