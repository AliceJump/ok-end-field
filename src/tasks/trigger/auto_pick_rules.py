"""Pure pick rules for the auto-pick trigger task.

Kept free of heavy imports so tests can validate the rules without
loading the Qt/win32 task stack.
"""

# These crops can all be grown on farm plots or in seed/plant machines
# (seed machine + planter production lines), so picking them is unnecessary.
PRODUCIBLE_PLANTS = frozenset({
    '琼叶参', '金石稻', '芽针', '锦草', '苦叶椒', '砂叶', '灰芦麦', '柑实', '荞花', '酮化灌木',
})

# Special variants are valuable pickups even though their names contain a
# producible plant name.
PICKABLE_PLANT_VARIANTS = frozenset({
    '荆刺芽针', '蓬茸锦草', '黯银柑实', '映火荞花',
})

# Items worth picking: rare materials, special plant variants, ores,
# timber and hunt/gather materials that cannot be mass produced.
WHITE_LIST = frozenset({
    '采集', '萤壳虫', '打开', '灼壳虫', '触碰', '激活',
    '荞花', '灰芦麦', '苦叶椒', '柑实', '芽针', '砂叶',
    '柱状菌', '酮化灌木',
    '多齿叶', '血菌', '星门菌', '黯石', '武陵石', '燎石', '玉化叶', '岩天使叶',
    '荆刺芽针', '蓬茸锦草', '黯银柑实', '映火荞花',
    '蓝铁矿', '紫晶矿', '源矿', '原木',
    '兽肉', '虫肉', '新笋', '虬兽的须', '彪兽的长绒', '球刺兽的肝脏',
    '水灯虫的灯坠', '软骨碎屑', '驮兽粪便', '大斧角', '异色油脂', '刺鼻干肉',
    '草籽干粉', '苦涩麦粉', '异香石', '晶城锭', '肖像石', '陈酿', '黑水',
    '气泡水', '坚韧的水', '大弩', '拳甲', '斧刃',
})

# Interaction prompts that must never be auto-picked.
BLACK_LIST = frozenset({
    '协议核心', '激活箱子'
})

CFG_SKIP_PRODUCIBLE = '屏蔽可量产植物'


def should_skip_pick(item_name: str, black_list=BLACK_LIST, skip_producible: bool = True) -> bool:
    """Whether the F-prompt text should be ignored.

    Hard blacklist always wins; producible plants are skipped only when
    the skip_producible flag (backed by the user config) is enabled.
    """
    if black_list and any(text in item_name for text in black_list):
        return True
    if (
        skip_producible
        and not any(variant in item_name for variant in PICKABLE_PLANT_VARIANTS)
        and any(plant in item_name for plant in PRODUCIBLE_PLANTS)
    ):
        return True
    return False
