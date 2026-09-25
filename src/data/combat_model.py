"""战斗快照状态容器：排轴器/伤害计算共享的运行时效果状态。

建模约定（详见 effect_semantics.py 模块注释）：
- 敌人身上任意时刻最多一种法术附着（异元素交叉会清空全部，官方规则），
  因此附着存为互斥的 (元素, 层数) 结构，而非每元素一个计数器。
- 附着层数即异常等级（I~IV），直接作为反应伤害的 ×(1+异常等级) 输入。
- 连击是队伍共享池，数值表来自 DAMAGE_FORMULA §8（灰机wiki 数值）。
- 反应规则（谁能触发什么）见本文件 REACTION_RULES（第 3 部分）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.data.effects import EffectType

# 法术附着四元素（EffectType 中的 ATTACH_* 全集）
ATTACH_ELEMENTS: frozenset[EffectType] = frozenset(
    {
        EffectType.ATTACH_COLD,
        EffectType.ATTACH_BURN,
        EffectType.ATTACH_ELECTROMAGNETIC,
        EffectType.ATTACH_NATURAL,
    }
)

MAX_INFLICTION_STACKS = 4  # 附着层数上限（状态等级 I~IV）
MAX_SHRED_STACKS = 4  # 破防层数上限
MAX_LINK_STACKS = 4  # 连击层数上限


@dataclass
class EnemyCombatState:
    """单个敌人的效果快照。"""

    # 当前法术附着：互斥结构（官方：异元素反应消耗全部已有附着）
    infliction_element: EffectType | None = None
    infliction_stacks: int = 0
    # 破防层数（物理侧独立于法术附着，二者可共存）
    shred_stacks: int = 0
    # 其余持续状态 → 剩余持续时间（秒）；由编排器负责 tick 与过期
    states: dict[EffectType, float] = field(default_factory=dict)

    def apply_infliction(self, element: EffectType) -> EffectType | None:
        """对敌人施加法术附着，按官方规则转移并返回应结算的事件。

        返回：
        - None                无附着 → 直接设置 1 层
        - STATUS_SPELL_BURST  同元素 → 层数 +1（封顶 4），触发法术爆发（等级=新层数）
        - STATUS_BURNING 等   异元素 → 触发对应法术反应（强度=旧层数），清空全部附着；
                                反应后不自动保留新元素附着（是否重附着未确认，待实测）
        """
        if element not in ATTACH_ELEMENTS:
            raise ValueError(f"{element} 不是法术附着元素")
        if self.infliction_element is None:
            self.infliction_element = element
            self.infliction_stacks = 1
            return None
        if self.infliction_element is element:
            self.infliction_stacks = min(self.infliction_stacks + 1, MAX_INFLICTION_STACKS)
            return EffectType.STATUS_SPELL_BURST
        # 异元素交叉：反应类型由新施加元素决定（EFFECT_SYSTEM §2 反应组合表）
        self.infliction_element = None
        self.infliction_stacks = 0
        return _ELEMENT_REACTION[element]

    def add_shred(self, stacks: int = 1) -> None:
        """叠破防层（击飞/倒地 +1；首次物理异常只叠层不触发效果）。"""
        self.shred_stacks = min(self.shred_stacks + stacks, MAX_SHRED_STACKS)

    def consume_shred(self) -> int:
        """消耗全部破防层（猛击/碎甲触发时），返回被消耗的层数。"""
        consumed = self.shred_stacks
        self.shred_stacks = 0
        return consumed


@dataclass
class TeamCombatState:
    """队伍共享池快照（连击与技力池同级的队伍资源）。"""

    link_stacks: int = 0

    def add_link(self, stacks: int = 1) -> None:
        """叠连击层（黎风获得、秋栗大招期间全队 +1 等）。"""
        self.link_stacks = min(self.link_stacks + stacks, MAX_LINK_STACKS)

    def consume_link(self) -> int:
        """消耗全部连击层（下一发战技/终结技结算时），返回层数。"""
        consumed = self.link_stacks
        self.link_stacks = 0
        return consumed

    def link_bonus(self, *, is_ult: bool) -> float:
        """当前层数下，下一发战技/终结技的连击加成（小数，如 0.30 = +30%）。

        数值表：灰机wiki「伤害」页 + DAMAGE_FORMULA §8。
        层数 0 → 无加成；消耗在结算后由调用方执行 consume_link()。
        """
        if self.link_stacks == 0:
            return 0.0
        table = (0.20, 0.30, 0.40, 0.50) if is_ult else (0.30, 0.45, 0.60, 0.75)
        return table[self.link_stacks - 1]


# 异元素交叉的反应映射：新施加元素 → 触发的法术异常（EFFECT_SYSTEM §2）
_ELEMENT_REACTION: dict[EffectType, EffectType] = {
    EffectType.ATTACH_BURN: EffectType.STATUS_BURNING,
    EffectType.ATTACH_ELECTROMAGNETIC: EffectType.STATUS_CONDUCTING,
    EffectType.ATTACH_COLD: EffectType.STATUS_FROZEN,
    EffectType.ATTACH_NATURAL: EffectType.STATUS_CORROSION,
}
