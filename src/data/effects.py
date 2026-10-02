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
    STATUS_BROKEN = "STATUS_BROKEN"  # 碎冰（官方名 Shatter：固结敌人受到物理异常触发，勿被枚举名误导）
    STATUS_FOCUS = "STATUS_FOCUS"  # 安塔尔施加的聚焦状态
    STATUS_CONFINEMENT = "STATUS_CONFINEMENT"  # 诀施加的囹圄状态
    STATUS_ORIGINIUM_CRYSTAL = "STATUS_ORIGINIUM_CRYSTAL"  # 管理员施加的源石结晶
    STATUS_SINGING = "STATUS_SINGING"  # 梨诺的演唱姿态
    STATUS_HIGH_SINGING = "STATUS_HIGH_SINGING"  # 梨诺的高歌姿态
    STATUS_HOVERING = "STATUS_HOVERING"
    STATUS_YVONNE_ASSISTED = "STATUS_YVONNE_ASSISTED"
    STATUS_LAEVATAIN_SWORD = "STATUS_LAEVATAIN_SWORD"
    STATUS_TIANLI_HEZHEN = "STATUS_TIANLI_HEZHEN"
    STATUS_TIANLI_FIRST_SKILL_READY = "STATUS_TIANLI_FIRST_SKILL_READY"
    STATUS_MIFU_ZHUIXING_READY = "STATUS_MIFU_ZHUIXING_READY"
    STATUS_MIFU_KAITIAN_READY = "STATUS_MIFU_KAITIAN_READY"
    STATUS_CAMILLE_PURSUIT_READY = "STATUS_CAMILLE_PURSUIT_READY"

    # 结算后事件
    EVENT_SHRED_CONSUMED = "EVENT_SHRED_CONSUMED"

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
    STACK_SIGN = "STACK_SIGN"  # 提弗洛斯的启示层数
    STACK_HUNTING_ARROW = "STACK_HUNTING_ARROW"  # 提弗洛斯的猎矢数量
    STACK_CAMILLE_BLOOD_SURGE = "STACK_CAMILLE_BLOOD_SURGE"
    STACK_AIR_ATTACKS = "STACK_AIR_ATTACKS"
    STACK_YVONNE_CRIT = "STACK_YVONNE_CRIT"

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
    BUFF_INVULNERABLE = "BUFF_INVULNERABLE"

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
    MECH_ANCIENT_PATTERN = "MECH_ANCIENT_PATTERN"
    MECH_THUNDER_SPEAR = "MECH_THUNDER_SPEAR"
    MECH_STRONG_THUNDER_SPEAR = "MECH_STRONG_THUNDER_SPEAR"

    # 放置物操作
    PLACE_THUNDER_SPEAR = "PLACE_THUNDER_SPEAR"  # 历史生成动作 ID，运行时实体改用 MECH_*_THUNDER_SPEAR
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
    EffectType.STATUS_SPELL_INFLICT: "条件谓词：目标当前存在任意法术附着；真实附着存于 ATTACH_*，本 ID 不应独立存储",
    EffectType.STATUS_SPELL_BURST: "法术爆发伤害（同元素再次附着时触发）",
    EffectType.STATUS_SPELL_ANOMALY: "条件谓词：目标当前存在任意法术异常；真实异常存于四种具体 STATUS_*，本 ID 不应独立存储",
    EffectType.STATUS_SLOW: "敌人被施加缓速",
    EffectType.STATUS_BROKEN: "碎冰（官方名 Shatter）：处于固结/冻结状态的敌人受到物理异常（或破防）时触发，造成大量物理伤害（120%）并结束固结",
    EffectType.STATUS_FOCUS: "安塔尔施加的聚焦状态，同一时间最多存在于一个敌人",
    EffectType.STATUS_CONFINEMENT: "诀施加的囹圄状态，使目标所有行动减缓",
    EffectType.STATUS_ORIGINIUM_CRYSTAL: "管理员附着的源石结晶，可被物理异常或破防消耗",
    EffectType.STATUS_SINGING: "梨诺的演唱姿态，持续强化全队并周期追加攻击与治疗",
    EffectType.STATUS_HIGH_SINGING: "梨诺的高歌姿态，替代演唱姿态并提供强化效果",
    EffectType.STATUS_HOVERING: "提弗洛斯自身的浮空姿态（历史 ID）；敌人浮空由击飞 STATUS_HEAVY_HIT 表达",
    EffectType.STATUS_YVONNE_ASSISTED: "伊冯小嘀嗒辅助强化普攻姿态，持续7秒",
    EffectType.STATUS_LAEVATAIN_SWORD: "莱万汀烈焰魔剑强化普攻姿态，持续15秒",
    EffectType.STATUS_TIANLI_HEZHEN: "庄方宜终结技进入的天理合真姿态，持续25秒，改写普攻/战技/连携技",
    EffectType.STATUS_TIANLI_FIRST_SKILL_READY: "天理合真期间首次惊霆诀的一次性强化标记：不消耗技力与导电，并固定生成3柄青霆剑",
    EffectType.STATUS_MIFU_ZHUIXING_READY: "弭弗下一次战技替换为追形的一次性状态",
    EffectType.STATUS_MIFU_KAITIAN_READY: "弭弗下一次战技替换为开天的一次性状态",
    EffectType.STATUS_CAMILLE_PURSUIT_READY: "卡缪下一次战技替换为追猎的一次性状态，追猎视为连携技且不消耗技力",
    EffectType.EVENT_SHRED_CONSUMED: "最近一次猛击/碎甲等结算实际消费的破防层数事件；count用于后续连携/替换条件",
    # 层数系统
    EffectType.STACK_MOLTEN: "莱万汀自身的熔火层数，最多4层",
    EffectType.STACK_AIR_ATTACKS: "提弗洛斯剩余空中普攻次数，最多5次；战技/连携/终结技重置为5",
    EffectType.STACK_YVONNE_CRIT: "伊冯终结技期间强化普攻累计暴击层数，最多10层，每层暴击率+3%",
    EffectType.STACK_SHRED: "敌人身上的破防层数",
    EffectType.STACK_IRON_OATH: "骏卫终结技生成的铁誓，固定生成5点、逐点消耗、持续30秒",
    EffectType.STACK_BLOOD_WING: "历史 STACK 命名；实际为卡缪衔火血翼盘桓在某个敌人身上的可转移标记/实体",
    EffectType.STACK_COMBO: "队伍连击层数（官方名 Link/连击）：最多 4 层，持有时下一发战技（加成更大）或终结技伤害提升，使用后消耗；黎风、秋栗等干员可施加，黎风终结技消耗连击追加伤害",
    EffectType.STACK_MORALE: "干员身上的士气激昂，最多3层，每层独立计算持续时间",
    EffectType.STACK_WHIRLPOOL: "汤汤在场上生成的涡流实体计数，最多2处；完整模拟需记录各实体寿命/位置",
    EffectType.STACK_SEED: "旧版/未解析占位；当前版本无可信 producer，不参与自动机制推断",
    EffectType.STACK_TRACE: "历史 STACK 命名；实际为洛茜施加在敌人身上的爪印斫痕标记，无法叠加",
    EffectType.STACK_CHARGE: "旧版/误解占位；敌人开始蓄力是连携触发事件，不等于卡契尔拥有蓄力资源",
    EffectType.STACK_QINGTING_SWORD: "庄方宜的场上青霆剑实体计数，场上最多9柄；单次战技最多生成3柄，并在逐柄雷击后消费",
    EffectType.STACK_SIGN: "提弗洛斯的启示层数，最多8点，满时可发动连携技",
    EffectType.STACK_HUNTING_ARROW: "提弗洛斯的猎矢数量，最多4枚，消耗后强化射击触发自然爆发",
    EffectType.STACK_CAMILLE_BLOOD_SURGE: "卡缪天赋“血涌苏生”的灼热伤害提升层数，最多5层，持续40秒",
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
    EffectType.BUFF_INVULNERABLE: "干员在指定动作/姿态期间免疫所有伤害",
    # 减益效果
    EffectType.DEBUFF_DEF_DOWN: "旧版/未解析占位；当前角色技能/天赋无可信 producer",
    EffectType.DEBUFF_SPEED_DOWN: "历史减速别名；当前统一使用 STATUS_SLOW，不参与自动机制推断",
    EffectType.DEBUFF_HEAL_DOWN: "旧版/未解析占位；当前角色技能/天赋无可信 producer",
    EffectType.DEBUFF_WEAKEN: "敌人被施加虚弱效果",
    # 特殊机制
    EffectType.MECH_VACUUM: "历史命名；洁尔佩塔的重力牵引/聚怪控制事件",
    EffectType.MECH_GRAVITY: "洁尔佩塔的持续重力场实体/区域效果",
    EffectType.MECH_FREEZE_FIELD: "伊冯的冰冻领域效果",
    EffectType.MECH_FIRE_FIELD: "莱万汀的火焰领域效果",
    EffectType.MECH_LIGHTNING_FIELD: "梨诺的雷电领域效果",
    EffectType.MECH_NATURE_FIELD: "艾尔黛拉的自然领域效果",
    EffectType.MECH_BOMB: "萤石黏附在目标敌人身上的自制炸弹实体，场上同时只能存在一个",
    EffectType.MECH_RADAR: "旧版/未解析占位；当前安塔尔技能数据无可信雷达机制",
    EffectType.MECH_TURRET: "旧版/未解析占位；当前佩丽卡技能数据无可信炮台机制",
    EffectType.MECH_SUPPORT_CRYSTAL: "赛希召唤的支援晶体",
    EffectType.MECH_ANCIENT_PATTERN: "汤汤终结技释放的古老图形场地，持续4秒，封锁范围内敌人并使其暂停行动",
    EffectType.MECH_THUNDER_SPEAR: "艾维文娜连携技生成的普通雷枪场上实体，默认存在30秒",
    EffectType.MECH_STRONG_THUNDER_SPEAR: "艾维文娜终结技生成的强雷枪场上实体，默认存在30秒",
    EffectType.PLACE_THUNDER_SPEAR: "历史生成动作 ID；新数据应直接产出具体雷枪实体",
    EffectType.REMOVE_THUNDER_SPEAR: "召回/移除场上全部普通雷枪与强雷枪的操作",
    # 消耗/清除
    EffectType.CONSUME_ALL: "消费某个明确资源的全部数量；必须由调用上下文提供被消费对象",
    EffectType.CONSUME_STACK: "消费某个明确资源的指定层数；必须由调用上下文提供被消费对象",
    EffectType.CLEAR_ATTACH: "清空所有元素附着",
    EffectType.CLEAR_STATUS: "清空所有异常状态（官方名「清除异常状态」，社区攻略常转述为「净化」）",
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
    "碎冰": EffectType.STATUS_BROKEN,
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
    "天理合真": EffectType.STATUS_TIANLI_HEZHEN,
    # 层数系统
    "消耗破防层数": EffectType.STACK_SHRED,
    "破防层数": EffectType.STACK_SHRED,
    "熔火": EffectType.STACK_MOLTEN,
    "铁誓": EffectType.STACK_IRON_OATH,
    "连击": EffectType.STACK_COMBO,
    "士气": EffectType.STACK_MORALE,
    "涡流": EffectType.STACK_WHIRLPOOL,
    "青霆剑": EffectType.STACK_QINGTING_SWORD,
    "启示": EffectType.STACK_SIGN,
    "猎矢": EffectType.STACK_HUNTING_ARROW,
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
    "免疫所有伤害": EffectType.BUFF_INVULNERABLE,
    # 减益效果
    "减速": EffectType.STATUS_SLOW,
    "虚弱": EffectType.DEBUFF_WEAKEN,
    # 特殊机制
    "重力牵引": EffectType.MECH_VACUUM,
    "重力场": EffectType.MECH_GRAVITY,
    "自制炸弹": EffectType.MECH_BOMB,
    "支援晶体": EffectType.MECH_SUPPORT_CRYSTAL,
    "古老图形": EffectType.MECH_ANCIENT_PATTERN,
    "强雷枪": EffectType.MECH_STRONG_THUNDER_SPEAR,
    "雷枪": EffectType.MECH_THUNDER_SPEAR,
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
    "净化": EffectType.CLEAR_STATUS,
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
