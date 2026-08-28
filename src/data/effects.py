"""效果ID系统定义。"""

from enum import Enum


class EffectType(Enum):
    """效果类型。"""
    # 元素附着
    ATTACH_COLD = "ATTACH_COLD"
    ATTACH_BURN = "ATTACH_BURN"
    ATTACH_ELECTROMAGNETIC = "ATTACH_ELECTROMAGNETIC"
    ATTACH_NATURAL = "ATTACH_NATURAL"

    # 元素脆弱
    VULN_COLD = "VULN_COLD"
    VULN_BURN = "VULN_BURN"
    VULN_ELECTROMAGNETIC = "VULN_ELECTROMAGNETIC"
    VULN_NATURAL = "VULN_NATURAL"
    VULN_PHYSICAL = "VULN_PHYSICAL"
    VULN_ALL = "VULN_ALL"

    # 异常状态
    STATUS_FROZEN = "STATUS_FROZEN"
    STATUS_BURNING = "STATUS_BURNING"
    STATUS_CONDUCTING = "STATUS_CONDUCTING"
    STATUS_SHRED = "STATUS_SHRED"
    STATUS_SHATTER = "STATUS_SHATTER"
    STATUS_STAGGER = "STATUS_STAGGER"
    STATUS_HEAVY_HIT = "STATUS_HEAVY_HIT"
    STATUS_SPELL_INFLICT = "STATUS_SPELL_INFLICT"
    STATUS_SPELL_BURST = "STATUS_SPELL_BURST"
    STATUS_SPELL_ANOMALY = "STATUS_SPELL_ANOMALY"

    # 层数系统
    STACK_MOLTEN = "STACK_MOLTEN"
    STACK_SHRED = "STACK_SHRED"
    STACK_IRON_OATH = "STACK_IRON_OATH"
    STACK_BLOOD_WING = "STACK_BLOOD_WING"
    STACK_COMBO = "STACK_COMBO"
    STACK_MORALE = "STACK_MORALE"
    STACK_WHIRLPOOL = "STACK_WHIRLPOOL"
    STACK_SEED = "STACK_SEED"
    STACK_TRACE = "STACK_TRACE"
    STACK_CHARGE = "STACK_CHARGE"

    # 增益效果
    BUFF_ATTACK_UP = "BUFF_ATTACK_UP"
    BUFF_CRIT_RATE_UP = "BUFF_CRIT_RATE_UP"
    BUFF_CRIT_DMG_UP = "BUFF_CRIT_DMG_UP"
    BUFF_SHIELD = "BUFF_SHIELD"
    BUFF_HEAL = "BUFF_HEAL"
    BUFF_SPEED_UP = "BUFF_SPEED_UP"
    BUFF_DAMAGE_UP = "BUFF_DAMAGE_UP"
    BUFF_COLD_UP = "BUFF_COLD_UP"
    BUFF_BURN_UP = "BUFF_BURN_UP"
    BUFF_ELECTROMAGNETIC_UP = "BUFF_ELECTROMAGNETIC_UP"
    BUFF_NATURAL_UP = "BUFF_NATURAL_UP"

    # 减益效果
    DEBUFF_DEF_DOWN = "DEBUFF_DEF_DOWN"
    DEBUFF_SPEED_DOWN = "DEBUFF_SPEED_DOWN"
    DEBUFF_HEAL_DOWN = "DEBUFF_HEAL_DOWN"

    # 特殊机制
    MECH_VACUUM = "MECH_VACUUM"
    MECH_GRAVITY = "MECH_GRAVITY"
    MECH_FREEZE_FIELD = "MECH_FREEZE_FIELD"
    MECH_FIRE_FIELD = "MECH_FIRE_FIELD"
    MECH_LIGHTNING_FIELD = "MECH_LIGHTNING_FIELD"
    MECH_NATURE_FIELD = "MECH_NATURE_FIELD"
    MECH_BOMB = "MECH_BOMB"
    MECH_RADAR = "MECH_RADAR"
    MECH_TURRET = "MECH_TURRET"

    # 消耗/清除
    CONSUME_ALL = "CONSUME_ALL"
    CONSUME_STACK = "CONSUME_STACK"
    CLEAR_ATTACH = "CLEAR_ATTACH"
    CLEAR_STATUS = "CLEAR_STATUS"

    # 触发效果
    TRIGGER_LINK = "TRIGGER_LINK"
    TRIGGER_ADDITIONAL = "TRIGGER_ADDITIONAL"
    TRIGGER_EXPLOSION = "TRIGGER_EXPLOSION"
    TRIGGER_HEAL = "TRIGGER_HEAL"
    TRIGGER_SHIELD = "TRIGGER_SHIELD"


# 效果描述映射
EFFECT_DESCRIPTIONS: dict[EffectType, str] = {
    # 元素附着
    EffectType.ATTACH_COLD: "敌人被施加寒冷元素",
    EffectType.ATTACH_BURN: "敌人被施加灼热元素",
    EffectType.ATTACH_ELECTROMAGNETIC: "敌人被施加电磁元素",
    EffectType.ATTACH_NATURAL: "敌人被施加自然元素",

    # 元素脆弱
    EffectType.VULN_COLD: "敌人受到寒冷伤害增加",
    EffectType.VULN_BURN: "敌人受到灼热伤害增加",
    EffectType.VULN_ELECTROMAGNETIC: "敌人受到电磁伤害增加",
    EffectType.VULN_NATURAL: "敌人受到自然伤害增加",
    EffectType.VULN_PHYSICAL: "敌人受到物理伤害增加",
    EffectType.VULN_ALL: "敌人受到所有元素伤害增加",

    # 异常状态
    EffectType.STATUS_FROZEN: "敌人被冻结，无法行动",
    EffectType.STATUS_BURNING: "敌人持续受到灼热伤害",
    EffectType.STATUS_CONDUCTING: "敌人处于导电状态",
    EffectType.STATUS_SHRED: "敌人防御力降低",
    EffectType.STATUS_SHATTER: "消耗破防层数造成额外伤害",
    EffectType.STATUS_STAGGER: "敌人失去平衡",
    EffectType.STATUS_HEAVY_HIT: "对敌人造成重击效果",
    EffectType.STATUS_SPELL_INFLICT: "通用的法术附着状态",
    EffectType.STATUS_SPELL_BURST: "法术爆发伤害",
    EffectType.STATUS_SPELL_ANOMALY: "法术异常状态",

    # 层数系统
    EffectType.STACK_MOLTEN: "莱万汀的熔火灼痕层数",
    EffectType.STACK_SHRED: "敌人身上的破防层数",
    EffectType.STACK_IRON_OATH: "余烬的铁誓层数",
    EffectType.STACK_BLOOD_WING: "卡缪的衔火血翼盘桓层数",
    EffectType.STACK_COMBO: "黎风的连击层数",
    EffectType.STACK_MORALE: "骏卫的士气激昂层数",
    EffectType.STACK_WHIRLPOOL: "汤汤的涡流数量",
    EffectType.STACK_SEED: "诀的种子层数",
    EffectType.STACK_TRACE: "洛茜的爪印斫痕层数",
    EffectType.STACK_CHARGE: "卡契尔的蓄力层数",

    # 增益效果
    EffectType.BUFF_ATTACK_UP: "攻击力增加",
    EffectType.BUFF_CRIT_RATE_UP: "暴击率增加",
    EffectType.BUFF_CRIT_DMG_UP: "暴击伤害增加",
    EffectType.BUFF_SHIELD: "获得护盾效果",
    EffectType.BUFF_HEAL: "恢复生命值",
    EffectType.BUFF_SPEED_UP: "移动速度增加",
    EffectType.BUFF_DAMAGE_UP: "所有伤害增加",
    EffectType.BUFF_COLD_UP: "寒冷伤害增加",
    EffectType.BUFF_BURN_UP: "灼热伤害增加",
    EffectType.BUFF_ELECTROMAGNETIC_UP: "电磁伤害增加",
    EffectType.BUFF_NATURAL_UP: "自然伤害增加",

    # 减益效果
    EffectType.DEBUFF_DEF_DOWN: "敌人防御力下降",
    EffectType.DEBUFF_SPEED_DOWN: "敌人移动速度下降",
    EffectType.DEBUFF_HEAL_DOWN: "敌人受到的治疗效果降低",

    # 特殊机制
    EffectType.MECH_VACUUM: "洁尔佩塔的真空牵引效果",
    EffectType.MECH_GRAVITY: "洁尔佩塔的重力场效果",
    EffectType.MECH_FREEZE_FIELD: "伊冯的冰冻领域效果",
    EffectType.MECH_FIRE_FIELD: "莱万汀的火焰领域效果",
    EffectType.MECH_LIGHTNING_FIELD: "梨诺的雷电领域效果",
    EffectType.MECH_NATURE_FIELD: "艾尔黛拉的自然领域效果",
    EffectType.MECH_BOMB: "萤石的炸弹效果",
    EffectType.MECH_RADAR: "安塔尔的雷达效果",
    EffectType.MECH_TURRET: "佩丽卡的炮台效果",

    # 消耗/清除
    EffectType.CONSUME_ALL: "清空所有层数/效果",
    EffectType.CONSUME_STACK: "消耗特定层数",
    EffectType.CLEAR_ATTACH: "清空所有元素附着",
    EffectType.CLEAR_STATUS: "清空所有异常状态",

    # 触发效果
    EffectType.TRIGGER_LINK: "触发连携技效果",
    EffectType.TRIGGER_ADDITIONAL: "触发额外攻击",
    EffectType.TRIGGER_EXPLOSION: "触发爆炸效果",
    EffectType.TRIGGER_HEAL: "触发治疗效果",
    EffectType.TRIGGER_SHIELD: "触发护盾效果",
}
