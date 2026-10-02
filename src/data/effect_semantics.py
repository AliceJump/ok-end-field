"""效果语义元数据：给 effects.py 的每个 EffectType 标注建模属性。

三层职责分工：
- effects.py           文本术语 → EffectType（match_effect_terms）
- effect_semantics.py  EffectType → 建模属性（本模块，供战斗快照/排轴器消费）
- combat_model.py      运行时状态容器与异常反应转移函数

字段语义
--------
kind:
    pool       整数计数池，可叠层、可消耗（附着层、破防层、连击、猎矢…）
    state      持续状态或 modifier（冻结、燃烧、增益、姿态…）
    entity     有身份/持续时间的实体或标记（炸弹、支援晶体、衔火血翼…）
    event      瞬时结算事件（法术爆发、碎冰、猛击、追加攻击…）
    operation  对已有状态/资源执行的操作（清除、消费、召回…）
    predicate  仅用于条件查询的类别/别名，不应独立存进运行时状态
    unresolved 旧数据占位；当前版本没有足够证据，不得参与机制推断

owner:
    enemy  挂在敌人身上        team   队伍共享池（连击、技力同类资源）
    actor  任意干员侧效果，具体 recipient 看 SkillEffect.target
    self   明确只属于技能拥有者的私有资源/姿态
    field  场地实体（领域、晶体、雷枪等）

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
    """效果语义类型。"""

    POOL = "pool"
    STATE = "state"
    ENTITY = "entity"
    EVENT = "event"
    OPERATION = "operation"
    PREDICATE = "predicate"
    UNRESOLVED = "unresolved"


class EffectOwner(Enum):
    """效果实际存放/作用的世界状态范围。"""

    ENEMY = "enemy"
    TEAM = "team"
    ACTOR = "actor"  # 任意干员；具体 self/ally/team recipient 由 SkillEffect.target 决定
    SELF = "self"    # 明确只属于技能拥有者的私有机制/姿态
    FIELD = "field"
    NONE = "none"


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


def _x(owner: EffectOwner) -> EffectSemantics:
    """entity 快捷构造：有身份、寿命或位置语义的实体/标记。"""
    return EffectSemantics(EffectKind.ENTITY, owner, None, ConsumePolicy.EXTERNAL, RefreshPolicy.REPLACE)


def _o(owner: EffectOwner = EffectOwner.ENEMY) -> EffectSemantics:
    """operation 快捷构造：只改变已有状态，不是可持有状态。"""
    return EffectSemantics(EffectKind.OPERATION, owner, None, ConsumePolicy.EXTERNAL, RefreshPolicy.REPLACE)


def _q(owner: EffectOwner) -> EffectSemantics:
    """predicate 快捷构造：仅用于条件查询，不应存进状态容器。"""
    return EffectSemantics(EffectKind.PREDICATE, owner, None, ConsumePolicy.EXTERNAL, RefreshPolicy.REPLACE)


def _u() -> EffectSemantics:
    """unresolved 快捷构造：旧版/占位语义，禁止参与自动推断。"""
    return EffectSemantics(
        EffectKind.UNRESOLVED,
        EffectOwner.NONE,
        None,
        ConsumePolicy.EXTERNAL,
        RefreshPolicy.REPLACE,
    )


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
    # 破防的唯一运行时真值是 STACK_SHRED；STATUS_SHRED 仅保留为“破防层数 > 0”的条件别名。
    EffectType.STATUS_SHRED: _q(EffectOwner.ENEMY),
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
    # 这两个 STATUS_* 是类别谓词：真实法术附着存于 ATTACH_*，真实异常存于四种具体异常。
    EffectType.STATUS_SPELL_INFLICT: _q(EffectOwner.ENEMY),
    EffectType.STATUS_SPELL_BURST: _e(EffectOwner.ENEMY),
    EffectType.STATUS_SPELL_ANOMALY: _q(EffectOwner.ENEMY),
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
    # 当前角色数据里的 STATUS_HOVERING 只表示提弗洛斯自身浮空姿态；
    # 敌人被击飞后的浮空属于 STATUS_HEAVY_HIT 的控制结果，不复用此 ID。
    EffectType.STATUS_HOVERING: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_YVONNE_ASSISTED: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_LAEVATAIN_SWORD: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    # 庄方宜终结技姿态与首次战技一次性强化标记。
    EffectType.STATUS_TIANLI_HEZHEN: _s(EffectOwner.SELF, refresh=RefreshPolicy.REPLACE),
    EffectType.STATUS_TIANLI_FIRST_SKILL_READY: _s(
        EffectOwner.SELF,
        consume=ConsumePolicy.ON_USE,
        refresh=RefreshPolicy.REPLACE,
    ),
    EffectType.STATUS_MIFU_ZHUIXING_READY: _s(
        EffectOwner.SELF,
        consume=ConsumePolicy.ON_USE,
        refresh=RefreshPolicy.REPLACE,
    ),
    EffectType.STATUS_MIFU_KAITIAN_READY: _s(
        EffectOwner.SELF,
        consume=ConsumePolicy.ON_USE,
        refresh=RefreshPolicy.REPLACE,
    ),
    EffectType.STATUS_CAMILLE_PURSUIT_READY: _s(
        EffectOwner.SELF,
        consume=ConsumePolicy.ON_USE,
        refresh=RefreshPolicy.REPLACE,
    ),
    # 破防消费后事件携带实际 consumed_count；用于骏卫连携、弭弗追形等后续条件。
    EffectType.EVENT_SHRED_CONSUMED: _e(EffectOwner.ENEMY),
    # ---- 层数 / 实体系统 ----
    # 莱万汀自身资源，最多 4 层；4 层战技会消费全部并追加攻击。
    EffectType.STACK_MOLTEN: _p(EffectOwner.SELF, 4, ConsumePolicy.ON_USE),
    EffectType.STACK_AIR_ATTACKS: EffectSemantics(
        EffectKind.POOL, EffectOwner.SELF, 5, ConsumePolicy.ON_USE, RefreshPolicy.REPLACE,
    ),
    EffectType.STACK_YVONNE_CRIT: _p(EffectOwner.SELF, 10, ConsumePolicy.EXPIRES),
    EffectType.STACK_SHRED: _p(EffectOwner.ENEMY, 4, ConsumePolicy.BY_REACTION),
    # 骏卫终结技固定生成 5 点铁誓，持续 30 秒并逐点消费。
    EffectType.STACK_IRON_OATH: _p(EffectOwner.SELF, 5, ConsumePolicy.ON_USE),
    # 历史命名为 STACK，实际是盘桓在某一敌人身上的可转移实体/标记。
    EffectType.STACK_BLOOD_WING: _x(EffectOwner.ENEMY),
    # 连击：队伍共享池，下一发战技/终结技使用即耗（数值见 DAMAGE_FORMULA §8）
    EffectType.STACK_COMBO: _p(EffectOwner.TEAM, 4, ConsumePolicy.ON_USE),
    # 士气激昂是干员个人 buff 层，最多 3 层，每层独立计时到期。
    EffectType.STACK_MORALE: _p(EffectOwner.ACTOR, 3, ConsumePolicy.EXPIRES),
    # 涡流是场上实体集合；计数投影最多 2，完整模拟还需记录每个实体的寿命/位置。
    EffectType.STACK_WHIRLPOOL: _p(EffectOwner.FIELD, 2, ConsumePolicy.BY_REACTION),
    # 当前版本没有可验证 producer 的历史占位，禁止自动机制推断。
    EffectType.STACK_SEED: _u(),
    # 爪印斫痕不能叠加，实际是挂在敌人身上的持续标记，不是层数池。
    EffectType.STACK_TRACE: _s(EffectOwner.ENEMY, refresh=RefreshPolicy.REPLACE),
    # “敌人开始蓄力”是事件条件，不代表卡契尔拥有名为蓄力的角色资源。
    EffectType.STACK_CHARGE: _u(),
    # 青霆剑存在于场上，最多 9 柄；“单次战技最多生成 3 柄”不是资源上限。
    EffectType.STACK_QINGTING_SWORD: _p(EffectOwner.FIELD, 9, ConsumePolicy.ON_USE),
    # 启示：满 8 解锁连携，连携发动时全部转化（消耗）
    EffectType.STACK_SIGN: _p(EffectOwner.SELF, 8, ConsumePolicy.ON_USE),
    # 猎矢：强化射击消耗并触发自然爆发；获取途径重置计数
    EffectType.STACK_HUNTING_ARROW: _p(EffectOwner.SELF, 4, ConsumePolicy.ON_USE),
    EffectType.STACK_CAMILLE_BLOOD_SURGE: _p(EffectOwner.SELF, 5, ConsumePolicy.EXPIRES),
    # ---- 增益：干员侧状态 ----
    EffectType.BUFF_ATTACK_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_CRIT_RATE_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_CRIT_DMG_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_SHIELD: _s(EffectOwner.ACTOR),
    EffectType.BUFF_HEAL: _e(EffectOwner.ACTOR),
    EffectType.BUFF_SPEED_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_DAMAGE_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_COLD_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_BURN_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_ELECTROMAGNETIC_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_NATURAL_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_SPELL_UP: _s(EffectOwner.ACTOR),
    EffectType.BUFF_PROTECTION: _s(EffectOwner.ACTOR),
    EffectType.BUFF_INVULNERABLE: _s(EffectOwner.ACTOR, refresh=RefreshPolicy.REPLACE),
    # ---- 减益：敌人侧状态 ----
    EffectType.DEBUFF_DEF_DOWN: _u(),
    EffectType.DEBUFF_SPEED_DOWN: _u(),
    EffectType.DEBUFF_HEAL_DOWN: _u(),
    EffectType.DEBUFF_WEAKEN: _s(EffectOwner.ENEMY),
    # ---- 特殊机制 ----
    # 真空是瞬时控制；重力场、自制炸弹和支援晶体均具有持续实体语义。
    EffectType.MECH_VACUUM: _e(EffectOwner.ENEMY),
    EffectType.MECH_GRAVITY: _x(EffectOwner.FIELD),
    EffectType.MECH_BOMB: _x(EffectOwner.ENEMY),
    # 泛化领域 ID 暂无当前版本稳定 producer，先隔离，避免根据名字脑补。
    EffectType.MECH_FREEZE_FIELD: _u(),
    EffectType.MECH_FIRE_FIELD: _u(),
    EffectType.MECH_LIGHTNING_FIELD: _u(),
    EffectType.MECH_NATURE_FIELD: _u(),
    EffectType.MECH_RADAR: _u(),
    EffectType.MECH_TURRET: _u(),
    EffectType.MECH_SUPPORT_CRYSTAL: _x(EffectOwner.FIELD),
    EffectType.MECH_ANCIENT_PATTERN: _x(EffectOwner.FIELD),
    EffectType.MECH_THUNDER_SPEAR: _x(EffectOwner.FIELD),
    EffectType.MECH_STRONG_THUNDER_SPEAR: _x(EffectOwner.FIELD),
    # ---- 放置物：对场上雷枪实体集合的操作 ----
    EffectType.PLACE_THUNDER_SPEAR: _o(EffectOwner.FIELD),
    EffectType.REMOVE_THUNDER_SPEAR: _o(EffectOwner.FIELD),
    # ---- 消耗/清除：操作已有状态/资源，本身不进入状态容器 ----
    EffectType.CONSUME_ALL: _o(EffectOwner.ENEMY),
    EffectType.CONSUME_STACK: _o(EffectOwner.ENEMY),
    EffectType.CLEAR_ATTACH: _o(EffectOwner.ENEMY),
    EffectType.CLEAR_STATUS: _o(EffectOwner.ACTOR),
    EffectType.CLEAR_COLD: _o(EffectOwner.ENEMY),
    EffectType.CLEAR_NATURAL: _o(EffectOwner.ENEMY),
    EffectType.CLEAR_FROZEN: _o(EffectOwner.ENEMY),
    # ---- 触发效果 ----
    EffectType.TRIGGER_LINK: _e(EffectOwner.TEAM),
    EffectType.TRIGGER_ADDITIONAL: _e(EffectOwner.ENEMY),
    EffectType.TRIGGER_EXPLOSION: _e(EffectOwner.ENEMY),
    EffectType.TRIGGER_HEAL: _e(EffectOwner.SELF),
    EffectType.TRIGGER_SHIELD: _e(EffectOwner.SELF),
    # “再次施加当前同类附着/状态”依赖既有 payload，是操作而非可持有事件。
    EffectType.TRIGGER_REPEAT_EFFECT: _o(EffectOwner.ENEMY),
}
