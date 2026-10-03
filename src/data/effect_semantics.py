"""效果语义元数据：给 effects.py 的每个 EffectType 标注建模属性。

三层职责分工：
- effects.py           文本术语 → EffectType（match_effect_terms）
- effect_semantics.py  EffectType → 建模属性（本模块，供战斗快照/排轴器消费）
- combat_model.py      运行时状态容器与异常反应转移函数

字段语义
--------
kind:
    pool   整数计数池，可叠层、可消耗（附着层、破防层、连击、猎矢…）
    state  开关型状态，带持续时间与刷新策略（冻结、燃烧、增益、姿态…）
    event  瞬时结算动作，无持续实体（法术爆发、碎冰、猛击、清除类…）

owner:
    enemy  挂在敌人身上        team   队伍共享池（连击、技力同类资源）
    self   干员个人资源/增益    field  场地放置物（领域、炮台、晶体）

consume（层/状态被移除的典型方式）:
    on_use       随使用消耗（连击：下一发战技/终结技；猎矢：强化射击）
    by_reaction  被反应或显式消耗效果移除（附着层被异元素清空、破防被猛击消耗、
                 源石结晶被物理异常消耗）
    expires      到时自动消失（绝大多数 state）
    external     仅被特定效果清除（姿态类，正常随姿态切换）

refresh（再次施加时的行为）:
    stack        叠层（附着+1 并触发法术爆发）
    reset_timer  刷新计时（易伤类再施加）
    replace      替换重来（姿态切换、控制覆盖）

标注依据：官方技能文案 + assets/lang/effect_names.json 官方多语言名 +
灰机wiki 异常状态/伤害页 + GameWith/game8 交叉验证。
个别层数归属（涡流、雷达等）文案不足以完全判定，按最可能归属标注并留 TODO，
后续实测修正；修正只改本表，不动枚举。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.data.effects import EffectType


class EffectKind(Enum):
    """效果语义类型：池 / 状态 / 瞬时结算。"""

    POOL = "pool"
    STATE = "state"
    EVENT = "event"


class EffectOwner(Enum):
    """效果挂载位置。"""

    ENEMY = "enemy"
    TEAM = "team"
    SELF = "self"
    FIELD = "field"


class ConsumePolicy(Enum):
    """层/状态被移除的典型方式。"""

    ON_USE = "on_use"
    BY_REACTION = "by_reaction"
    EXPIRES = "expires"
    EXTERNAL = "external"


class RefreshPolicy(Enum):
    """再次施加时的行为。"""

    STACK = "stack"
    RESET_TIMER = "reset_timer"
    REPLACE = "replace"


@dataclass(frozen=True)
class EffectSemantics:
    """单个效果 ID 的建模属性。"""

    kind: EffectKind
    owner: EffectOwner
    cap: int | None  # 池层数上限；state/event 为 None
    consume: ConsumePolicy
    refresh: RefreshPolicy


def _p(owner: EffectOwner, cap: int | None, consume: ConsumePolicy) -> EffectSemantics:
    """pool 快捷构造：refresh 恒为 STACK。"""
    return EffectSemantics(EffectKind.POOL, owner, cap, consume, RefreshPolicy.STACK)


def _s(owner: EffectOwner, consume: ConsumePolicy = ConsumePolicy.EXPIRES,
       refresh: RefreshPolicy = RefreshPolicy.RESET_TIMER) -> EffectSemantics:
    """state 快捷构造。"""
    return EffectSemantics(EffectKind.STATE, owner, None, consume, refresh)


def _e(owner: EffectOwner = EffectOwner.ENEMY) -> EffectSemantics:
    """event 快捷构造：瞬时结算，无持续实体。"""
    return EffectSemantics(EffectKind.EVENT, owner, None, ConsumePolicy.EXPIRES, RefreshPolicy.REPLACE)


# ---------------------------------------------------------------------------
# 87 条语义标注（与 EffectType 枚举一一对应，缺失由测试保证）
# ---------------------------------------------------------------------------

EFFECT_SEMANTICS: dict[EffectType, EffectSemantics] = {
    # ---- 元素附着：池，上限 4 层，被异元素反应/技能消耗，再施加叠层 ----
    EffectType.ATTACH_COLD: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    EffectType.ATTACH_BURN: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    EffectType.ATTACH_ELECTROMAGNETIC: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    EffectType.ATTACH_NATURAL: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    # ---- 元素脆弱：敌人承伤易伤，持续时间制，再施加刷新 ----
    EffectType.VULN_COLD: _s(EffectOwner.ENEMY),
    EffectType.VULN_BURN: _s(EffectOwner.ENEMY),
    EffectType.VULN_ELECTROMAGNETIC: _s(EffectOwner.ENEMY),
    EffectType.VULN_NATURAL: _s(EffectOwner.ENEMY),
    EffectType.VULN_PHYSICAL: _s(EffectOwner.ENEMY),
    EffectType.VULN_ALL: _s(EffectOwner.ENEMY),
    EffectType.VULN_NATURAL_BURST: _s(EffectOwner.ENEMY),
    # ---- 物理异常 ----
    # 破防：层数池（首次物理异常只叠层不触发），被猛击/碎甲消耗
    EffectType.STATUS_SHRED: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    # 击飞/倒地：对已破防敌人的即时控制 + 叠破防层，本体是持续控制状态
    EffectType.STATUS_HEAVY_HIT: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_KNOCKDOWN: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    # 碎甲：消耗破防层后进入的物理易伤状态
    EffectType.STATUS_SHATTER: _s(EffectOwner.ENEMY),
    # 猛击：瞬时大量物理伤害结算
    EffectType.STATUS_HEAVY_STRIKE: _e(EffectOwner.ENEMY),
    # 失衡：状态，失衡值满进入，×1.3 承伤窗口
    EffectType.STATUS_STAGGER: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    # ---- 法术异常：持续状态（触发伤害部分由反应规则表结算） ----
    EffectType.STATUS_CORROSION: _s(EffectOwner.ENEMY),
    EffectType.STATUS_FROZEN: _s(EffectOwner.ENEMY),
    EffectType.STATUS_CONDUCTING: _s(EffectOwner.ENEMY),
    EffectType.STATUS_BURNING: _s(EffectOwner.ENEMY),
    # ---- 其他状态 ----
    EffectType.STATUS_SPELL_INFLICT: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.STACK),
    EffectType.STATUS_SPELL_BURST: _e(EffectOwner.ENEMY),
    EffectType.STATUS_SPELL_ANOMALY: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_SLOW: _s(EffectOwner.ENEMY),
    # 碎冰：固结敌人受物理异常时的瞬时结算（官方名 Shatter）
    EffectType.STATUS_BROKEN: _e(EffectOwner.ENEMY),
    EffectType.STATUS_FOCUS: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_CONFINEMENT: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    # 源石结晶：被物理异常或破防消耗
    EffectType.STATUS_ORIGINIUM_CRYSTAL: _s(EffectOwner.ENEMY, consume=ConsumePolicy.BY_REACTION),
    # 梨诺姿态：随姿态切换替换
    EffectType.STATUS_SINGING: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_HIGH_SINGING: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    # 浮空：目标（敌人）进入浮空；干员浮空（提弗洛斯）由姿态类状态表达
    EffectType.STATUS_HOVERING: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    # ---- 层数系统 ----
    # 熔火灼痕：莱万汀战技命中叠层，4 层时终结技消耗全部触发追加（官方文案确认）
    EffectType.STACK_MOLTEN: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    EffectType.STACK_SHRED: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    # 以下为干员自身资源层：随技能使用消耗；上限未实测（cap=None 待补）
    EffectType.STACK_IRON_OATH: _p(EffectOwner.SELF, None, ConsumePolicy.ON_USE),
    EffectType.STACK_BLOOD_WING: _p(EffectOwner.SELF, None, ConsumePolicy.ON_USE),
    # 连击：队伍共享池，下一发战技/终结技使用即耗（数值见 DAMAGE_FORMULA §8）
    EffectType.STACK_COMBO: _p(EffectOwner.TEAM, 4, ConsumePolicy.ON_USE),
    EffectType.STACK_MORALE: _p(EffectOwner.SELF, None, ConsumePolicy.ON_USE),
    # 涡流：汤汤的场上水龙卷实体，「场上最多存在 2 处」（官方文案确认）
    EffectType.STACK_WHIRLPOOL: _p(EffectOwner.FIELD, 2, ConsumePolicy.BY_REACTION),
    EffectType.STACK_SEED: _p(EffectOwner.SELF, None, ConsumePolicy.ON_USE),
    # 斫痕：洛茜挂在敌人身上的爪印层
    EffectType.STACK_TRACE: _p(EffectOwner.ENEMY, None, ConsumePolicy.BY_REACTION),
    EffectType.STACK_CHARGE: _p(EffectOwner.SELF, None, ConsumePolicy.ON_USE),
    EffectType.STACK_QINGTING_SWORD: _p(EffectOwner.SELF, 3, ConsumePolicy.ON_USE),
    # 启示：满 8 解锁连携，连携发动时全部转化（消耗）
    EffectType.STACK_SIGN: _p(EffectOwner.SELF, 8, ConsumePolicy.ON_USE),
    # 猎矢：强化射击消耗并触发自然爆发；获取途径重置计数
    EffectType.STACK_HUNTING_ARROW: _p(EffectOwner.SELF, 4, ConsumePolicy.ON_USE),
    # ---- 增益：干员侧状态 ----
    EffectType.BUFF_ATTACK_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_CRIT_RATE_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_CRIT_DMG_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_SHIELD: _s(EffectOwner.SELF),
    EffectType.BUFF_HEAL: _e(EffectOwner.SELF),
    EffectType.BUFF_SPEED_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_DAMAGE_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_COLD_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_BURN_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_ELECTROMAGNETIC_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_NATURAL_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_SPELL_UP: _s(EffectOwner.SELF),
    EffectType.BUFF_PROTECTION: _s(EffectOwner.SELF),
    # ---- 减益：敌人侧状态 ----
    EffectType.DEBUFF_DEF_DOWN: _s(EffectOwner.ENEMY),
    EffectType.DEBUFF_SPEED_DOWN: _s(EffectOwner.ENEMY),
    EffectType.DEBUFF_HEAL_DOWN: _s(EffectOwner.ENEMY),
    EffectType.DEBUFF_WEAKEN: _s(EffectOwner.ENEMY),
    # ---- 特殊机制 ----
    # 真空牵引/重力/炸弹：瞬时结算
    EffectType.MECH_VACUUM: _e(EffectOwner.ENEMY),
    EffectType.MECH_GRAVITY: _e(EffectOwner.ENEMY),
    EffectType.MECH_BOMB: _e(EffectOwner.ENEMY),
    # 领域/装置：场地持续实体
    EffectType.MECH_FREEZE_FIELD: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    EffectType.MECH_FIRE_FIELD: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    EffectType.MECH_LIGHTNING_FIELD: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    EffectType.MECH_NATURE_FIELD: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    # 雷达：安塔尔的侦测装置（TODO 归属文案不足，暂标 field）
    EffectType.MECH_RADAR: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    EffectType.MECH_TURRET: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    EffectType.MECH_SUPPORT_CRYSTAL: _s(EffectOwner.FIELD, refresh=RefreshPolicy.REPLACE),
    # ---- 放置物：雷枪放置/召回动作 ----
    EffectType.PLACE_THUNDER_SPEAR: _e(EffectOwner.SELF),
    EffectType.REMOVE_THUNDER_SPEAR: _e(EffectOwner.SELF),
    # ---- 消耗/清除：瞬时动作，作用于敌人身上实体 ----
    EffectType.CONSUME_ALL: _e(EffectOwner.ENEMY),
    EffectType.CONSUME_STACK: _e(EffectOwner.ENEMY),
    EffectType.CLEAR_ATTACH: _e(EffectOwner.ENEMY),
    EffectType.CLEAR_STATUS: _e(EffectOwner.ENEMY),
    EffectType.CLEAR_COLD: _e(EffectOwner.ENEMY),
    EffectType.CLEAR_NATURAL: _e(EffectOwner.ENEMY),
    EffectType.CLEAR_FROZEN: _e(EffectOwner.ENEMY),
    # ---- 触发效果 ----
    EffectType.TRIGGER_LINK: _e(EffectOwner.TEAM),
    EffectType.TRIGGER_ADDITIONAL: _e(EffectOwner.ENEMY),
    EffectType.TRIGGER_EXPLOSION: _e(EffectOwner.ENEMY),
    EffectType.TRIGGER_HEAL: _e(EffectOwner.SELF),
    EffectType.TRIGGER_SHIELD: _e(EffectOwner.SELF),
    EffectType.TRIGGER_REPEAT_EFFECT: _e(EffectOwner.ENEMY),
}
