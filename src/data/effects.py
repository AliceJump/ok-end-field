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
    VULN_NATURAL_BURST = "VULN_NATURAL_BURST"  # 自然爆发脆弱

    # 物理异常状态（wiki: 物理异常）
    STATUS_SHRED = "STATUS_SHRED"  # 破防（首次受到物理异常时进入，可叠加最多4层）
    STATUS_HEAVY_HIT = "STATUS_HEAVY_HIT"  # 击飞（已破防时触发，叠加破防层数+失衡+浮空）
    STATUS_KNOCKDOWN = "STATUS_KNOCKDOWN"  # 倒地（已破防时触发，叠加破防层数+失衡+击倒）
    STATUS_SHATTER = "STATUS_SHATTER"  # 碎甲（已破防时触发，消耗所有破防层数，增加物理受伤）
    STATUS_HEAVY_STRIKE = "STATUS_HEAVY_STRIKE"  # 猛击（已破防时触发，消耗所有破防层数，大量物理伤害）
    STATUS_STAGGER = "STATUS_STAGGER"  # 失衡

    # 法术异常状态（wiki: 法术异常 - 不同元素附着交叉触发）
    STATUS_CORROSION = "STATUS_CORROSION"  # 腐蚀（自然+其他元素→消耗附着→全属性抗性逐渐下降）
    STATUS_FROZEN = "STATUS_FROZEN"  # 冻结（寒冷+其他元素→消耗附着→弱小敌人无法行动）
    STATUS_CONDUCTING = "STATUS_CONDUCTING"  # 导电（电磁+其他元素→消耗附着→法术伤害提高）
    STATUS_BURNING = "STATUS_BURNING"  # 燃烧（灼热+其他元素→消耗附着→持续灼热伤害）

    # 其他状态
    STATUS_SPELL_INFLICT = "STATUS_SPELL_INFLICT"  # 通用的法术附着状态
    STATUS_SPELL_BURST = "STATUS_SPELL_BURST"  # 法术爆发伤害（同元素再次附着触发）
    STATUS_SPELL_ANOMALY = "STATUS_SPELL_ANOMALY"  # 法术异常状态（通用）
    STATUS_SLOW = "STATUS_SLOW"  # 缓速
    STATUS_BROKEN = "STATUS_BROKEN"  # 破碎
    STATUS_FOCUS = "STATUS_FOCUS"  # 安塔尔施加的聚焦状态
    STATUS_CONFINEMENT = "STATUS_CONFINEMENT"  # 诀施加的囹圄状态
    STATUS_ORIGINIUM_CRYSTAL = "STATUS_ORIGINIUM_CRYSTAL"  # 管理员施加的源石结晶
    STATUS_SINGING = "STATUS_SINGING"  # 梨诺的演唱姿态
    STATUS_HIGH_SINGING = "STATUS_HIGH_SINGING"  # 梨诺的高歌姿态
    STATUS_HOVERING = "STATUS_HOVERING"

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
    STACK_QINGTING_SWORD = "STACK_QINGTING_SWORD"

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
    BUFF_SPELL_UP = "BUFF_SPELL_UP"
    BUFF_PROTECTION = "BUFF_PROTECTION"

    # 减益效果
    DEBUFF_DEF_DOWN = "DEBUFF_DEF_DOWN"
    DEBUFF_SPEED_DOWN = "DEBUFF_SPEED_DOWN"
    DEBUFF_HEAL_DOWN = "DEBUFF_HEAL_DOWN"
    DEBUFF_WEAKEN = "DEBUFF_WEAKEN"

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
    MECH_SUPPORT_CRYSTAL = "MECH_SUPPORT_CRYSTAL"

    # 放置物
    PLACE_THUNDER_SPEAR = "PLACE_THUNDER_SPEAR"
    REMOVE_THUNDER_SPEAR = "REMOVE_THUNDER_SPEAR"

    # 消耗/清除
    CONSUME_ALL = "CONSUME_ALL"
    CONSUME_STACK = "CONSUME_STACK"
    CLEAR_ATTACH = "CLEAR_ATTACH"
    CLEAR_STATUS = "CLEAR_STATUS"
    CLEAR_COLD = "CLEAR_COLD"
    CLEAR_NATURAL = "CLEAR_NATURAL"
    CLEAR_FROZEN = "CLEAR_FROZEN"

    # 触发效果
    TRIGGER_LINK = "TRIGGER_LINK"
    TRIGGER_ADDITIONAL = "TRIGGER_ADDITIONAL"
    TRIGGER_EXPLOSION = "TRIGGER_EXPLOSION"
    TRIGGER_HEAL = "TRIGGER_HEAL"
    TRIGGER_SHIELD = "TRIGGER_SHIELD"
    TRIGGER_REPEAT_EFFECT = "TRIGGER_REPEAT_EFFECT"


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
    EffectType.VULN_ALL: "敌人受到的法术伤害增加（不含物理伤害）",
    EffectType.VULN_NATURAL_BURST: "敌人受到自然爆发伤害增加",
    # 物理异常状态
    EffectType.STATUS_SHRED: "破防状态，可被击飞和倒地叠加（最多4层），被猛击和碎甲消耗",
    EffectType.STATUS_HEAVY_HIT: "击飞：已破防时触发，叠加破防层数，造成物理伤害和失衡，浮空弱小敌人",
    EffectType.STATUS_KNOCKDOWN: "倒地：已破防时触发，叠加破防层数，造成物理伤害和失衡，击倒弱小敌人",
    EffectType.STATUS_SHATTER: "碎甲：已破防时触发，消耗所有破防层数，造成物理伤害，增加物理受伤",
    EffectType.STATUS_HEAVY_STRIKE: "猛击：已破防时触发，消耗所有破防层数，造成大量物理伤害",
    EffectType.STATUS_STAGGER: "敌人失去平衡",
    # 法术异常状态（不同元素附着交叉触发）
    EffectType.STATUS_CORROSION: "腐蚀：自然+其他元素→消耗所有附着→初始自然伤害+全属性抗性逐渐下降",
    EffectType.STATUS_FROZEN: "冻结：寒冷+其他元素→消耗所有附着→初始寒冷伤害+弱小敌人无法行动",
    EffectType.STATUS_CONDUCTING: "导电：电磁+其他元素→消耗所有附着→初始电磁伤害+法术伤害提高",
    EffectType.STATUS_BURNING: "燃烧：灼热+其他元素→消耗所有附着→初始灼热伤害+持续灼热伤害",
    # 其他状态
    EffectType.STATUS_SPELL_INFLICT: "通用的法术附着状态",
    EffectType.STATUS_SPELL_BURST: "法术爆发伤害（同元素再次附着时触发）",
    EffectType.STATUS_SPELL_ANOMALY: "法术异常状态（通用）",
    EffectType.STATUS_SLOW: "敌人被施加缓速",
    EffectType.STATUS_BROKEN: "敌人处于破碎状态",
    EffectType.STATUS_FOCUS: "安塔尔施加的聚焦状态，同一时间最多存在于一个敌人",
    EffectType.STATUS_CONFINEMENT: "诀施加的囹圄状态，使目标所有行动减缓",
    EffectType.STATUS_ORIGINIUM_CRYSTAL: "管理员附着的源石结晶，可被物理异常或破防消耗",
    EffectType.STATUS_SINGING: "梨诺的演唱姿态，持续强化全队并周期追加攻击与治疗",
    EffectType.STATUS_HIGH_SINGING: "梨诺的高歌姿态，替代演唱姿态并提供强化效果",
    EffectType.STATUS_HOVERING: "目标进入浮空状态",
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
    EffectType.STACK_QINGTING_SWORD: "庄方宜的青霆剑数量，可按导电异常等级动态生成，单次战技最多生成3柄，并在逐柄雷击后消费",
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
    EffectType.BUFF_SPELL_UP: "法术伤害增加",
    EffectType.BUFF_PROTECTION: "干员获得庇护效果",
    # 减益效果
    EffectType.DEBUFF_DEF_DOWN: "敌人防御力下降",
    EffectType.DEBUFF_SPEED_DOWN: "敌人移动速度下降",
    EffectType.DEBUFF_HEAL_DOWN: "敌人受到的治疗效果降低",
    EffectType.DEBUFF_WEAKEN: "敌人被施加虚弱效果",
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
    EffectType.MECH_SUPPORT_CRYSTAL: "赛希召唤的支援晶体",
    EffectType.PLACE_THUNDER_SPEAR: "艾维文娜的雷枪放置物",
    EffectType.REMOVE_THUNDER_SPEAR: "艾维文娜的雷枪召回",
    # 消耗/清除
    EffectType.CONSUME_ALL: "清空所有层数/效果",
    EffectType.CONSUME_STACK: "消耗特定层数",
    EffectType.CLEAR_ATTACH: "清空所有元素附着",
    EffectType.CLEAR_STATUS: "清空所有异常状态",
    EffectType.CLEAR_COLD: "清空敌人寒冷附着",
    EffectType.CLEAR_NATURAL: "清空敌人自然附着",
    EffectType.CLEAR_FROZEN: "消耗敌人冻结状态",
    # 触发效果
    EffectType.TRIGGER_LINK: "触发连携技效果",
    EffectType.TRIGGER_ADDITIONAL: "触发额外攻击",
    EffectType.TRIGGER_EXPLOSION: "触发爆炸效果",
    EffectType.TRIGGER_HEAL: "触发治疗效果",
    EffectType.TRIGGER_SHIELD: "触发护盾效果",
    EffectType.TRIGGER_REPEAT_EFFECT: "再次施加目标当前的同类状态或附着",
}


# 效果术语映射：游戏文案中出现的术语 -> 效果ID
# 用于在 trigger_condition / description / enhancement_effect 等自然语言字段中
# 将中文术语（如"寒冷附着""冻结""破防"）关联到对应的效果ID。
# 匹配时按术语长度从长到短优先（避免"寒冷附着"被"附着"误吞）。
EFFECT_TERMS: dict[str, EffectType] = {
    # 元素附着
    "寒冷附着": EffectType.ATTACH_COLD,
    "灼热附着": EffectType.ATTACH_BURN,
    "电磁附着": EffectType.ATTACH_ELECTROMAGNETIC,
    "自然附着": EffectType.ATTACH_NATURAL,
    # 元素脆弱
    "寒冷脆弱": EffectType.VULN_COLD,
    "灼热脆弱": EffectType.VULN_BURN,
    "电磁脆弱": EffectType.VULN_ELECTROMAGNETIC,
    "自然脆弱": EffectType.VULN_NATURAL,
    "物理脆弱": EffectType.VULN_PHYSICAL,
    "法术脆弱": EffectType.VULN_ALL,
    # 异常状态
    "冻结": EffectType.STATUS_FROZEN,
    "燃烧": EffectType.STATUS_BURNING,
    "导电": EffectType.STATUS_CONDUCTING,
    "腐蚀": EffectType.STATUS_CORROSION,
    "破防": EffectType.STATUS_SHRED,
    "碎甲": EffectType.STATUS_SHATTER,
    "猛击": EffectType.STATUS_HEAVY_STRIKE,
    "倒地": EffectType.STATUS_KNOCKDOWN,
    "击飞": EffectType.STATUS_HEAVY_HIT,
    "失衡": EffectType.STATUS_STAGGER,
    "法术附着": EffectType.STATUS_SPELL_INFLICT,
    "法术爆发": EffectType.STATUS_SPELL_BURST,
    "法术异常": EffectType.STATUS_SPELL_ANOMALY,
    "缓速": EffectType.STATUS_SLOW,
    "破碎": EffectType.STATUS_BROKEN,
    "聚焦": EffectType.STATUS_FOCUS,
    "囹圄": EffectType.STATUS_CONFINEMENT,
    "源石结晶": EffectType.STATUS_ORIGINIUM_CRYSTAL,
    "演唱姿态": EffectType.STATUS_SINGING,
    "高歌姿态": EffectType.STATUS_HIGH_SINGING,
    "浮空": EffectType.STATUS_HOVERING,
    # 层数系统
    "消耗破防层数": EffectType.STACK_SHRED,
    "破防层数": EffectType.STACK_SHRED,
    "熔火": EffectType.STACK_MOLTEN,
    "铁誓": EffectType.STACK_IRON_OATH,
    "连击": EffectType.STACK_COMBO,
    "士气": EffectType.STACK_MORALE,
    "涡流": EffectType.STACK_WHIRLPOOL,
    "种子": EffectType.STACK_SEED,
    "蓄力": EffectType.STACK_CHARGE,
    "青霆剑": EffectType.STACK_QINGTING_SWORD,
    # 增益效果
    "攻击力提升": EffectType.BUFF_ATTACK_UP,
    "暴击率提升": EffectType.BUFF_CRIT_RATE_UP,
    "暴击伤害提升": EffectType.BUFF_CRIT_DMG_UP,
    "护盾": EffectType.BUFF_SHIELD,
    "治疗": EffectType.BUFF_HEAL,
    "移速提升": EffectType.BUFF_SPEED_UP,
    "伤害提升": EffectType.BUFF_DAMAGE_UP,
    "寒冷伤害提升": EffectType.BUFF_COLD_UP,
    "灼热伤害提升": EffectType.BUFF_BURN_UP,
    "电磁伤害提升": EffectType.BUFF_ELECTROMAGNETIC_UP,
    "自然伤害提升": EffectType.BUFF_NATURAL_UP,
    "法术增幅": EffectType.BUFF_SPELL_UP,
    "庇护": EffectType.BUFF_PROTECTION,
    # 减益效果
    "防御力降低": EffectType.DEBUFF_DEF_DOWN,
    "减速": EffectType.DEBUFF_SPEED_DOWN,
    "治疗效果降低": EffectType.DEBUFF_HEAL_DOWN,
    "虚弱": EffectType.DEBUFF_WEAKEN,
    # 特殊机制
    "真空": EffectType.MECH_VACUUM,
    "重力": EffectType.MECH_GRAVITY,
    "冰冻领域": EffectType.MECH_FREEZE_FIELD,
    "火焰领域": EffectType.MECH_FIRE_FIELD,
    "雷电领域": EffectType.MECH_LIGHTNING_FIELD,
    "自然领域": EffectType.MECH_NATURE_FIELD,
    "炸弹": EffectType.MECH_BOMB,
    "雷达": EffectType.MECH_RADAR,
    "炮台": EffectType.MECH_TURRET,
    # 消耗/清除（保留明确的组合术语；避免动词"消耗""清空"误报）
    "消耗寒冷附着": EffectType.CLEAR_COLD,
    "消耗自然附着": EffectType.CLEAR_NATURAL,
    "消耗冻结状态": EffectType.CLEAR_FROZEN,
    "消耗冻结": EffectType.CLEAR_FROZEN,
    "清空寒冷附着": EffectType.CLEAR_COLD,
    "清空自然附着": EffectType.CLEAR_NATURAL,
    "消耗电磁附着": EffectType.CLEAR_ATTACH,
    "消耗灼热附着": EffectType.CLEAR_ATTACH,
    "清空附着": EffectType.CLEAR_ATTACH,
    "清空状态": EffectType.CLEAR_STATUS,
    "自然爆发脆弱": EffectType.VULN_NATURAL_BURST,
}

# 按术语长度从长到短排序（匹配时优先长术语，避免"寒冷附着"被"附着"误吞）
_TERMS_BY_LEN: list[tuple[str, EffectType]] = sorted(
    EFFECT_TERMS.items(),
    key=lambda kv: len(kv[0]),
    reverse=True,
)


def match_effect_terms(text: str) -> list[tuple[str, EffectType]]:
    """在自然语言文本中提取命中效果术语。

    返回 [(术语, 效果ID), ...]，按出现顺序、同一位置优先长术语。
    例如 match_effect_terms("命中处于寒冷附着或自然附着的敌人时") ->
      [("寒冷附着", EffectType.ATTACH_COLD), ("自然附着", EffectType.ATTACH_NATURAL)]
    """
    hits: list[tuple[str, EffectType]] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for term, eff in _TERMS_BY_LEN:
            if text.startswith(term, i):
                hits.append((term, eff))
                i += len(term)
                matched = True
                break
        if not matched:
            i += 1
    return hits
