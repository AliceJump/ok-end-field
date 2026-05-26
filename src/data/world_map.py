from src.data.FeatureList import FeatureList as fL
from src.data.lang import world_map as lang_world_map

# area identifiers (logic layer)
AREA_IDS = ["wuling", "valley4"]


def area_id_to_label(area_id: str) -> str:
    return lang_world_map.get_area_label(area_id, locale="zh_CN")


def canonicalize_area(area_or_id: str) -> str:
    return lang_world_map.canonicalize_area(area_or_id, locale="zh_CN")


areas_list = [area_id_to_label(area_id) for area_id in AREA_IDS]

outpost_dict_by_id = {
    "wuling": ["天王坪援建点", "心脏修缮站"],
    "valley4": ["难民暂居处", "基建前站", "重建指挥部"],
}

outpost_dict = {
    area_id_to_label(area_id): list(outposts)
    for area_id, outposts in outpost_dict_by_id.items()
}

goods_dict_by_id = {
    "valley4": [
        "精选荞愈胶囊",
        "高容谷地电池",
        "精选柑实罐头",
        "中容谷地电池",
        "优质荞愈胶囊",
        "优质柑实罐头",
        "荞愈胶囊",
        "柑实罐头",
        "晶体外壳",
    ],
    "wuling": [
        "息壤玉葫芦",
        "息壤葫芦",
        "中容武陵电池",
        "优质芽针针剂",
        "优质锦草软饮",
        "低容武陵电池",
        "芽针针剂",
        "锦草软饮",
        "重息壤",
        "赫铜零件",
    ],
}

goods_dict = {
    area_id_to_label(area_id): list(goods)
    for area_id, goods in goods_dict_by_id.items()
}

exchange_goods_dict_by_id = {
    "valley4": [
        "锚点厨具货组",
        "悬空鼷兽骨雕货组",
        "巫术矿钻货组",
        "天使罐头货组",
        "谷地水培肉货组",
        "团结牌口服液货组",
        "源石树幼苗货组",
        "塞什卡髀石货组",
        "星体晶块货组",
        "警戒者矿镐货组",
        "硬脑壳头盔货组",
        "边角料积木货组",
    ],
    "wuling": [
        "冬虫夏笋货组",
        "岳研避瘴茶货组",
        "武陵冻梨货组",
        "武侠电影货组",
        "息壤净水芯货组",
        "天师龙泡泡货组",
        "清波筏货组",
    ],
}

exchange_goods_dict = {
    area_id_to_label(area_id): list(goods)
    for area_id, goods in exchange_goods_dict_by_id.items()
}

item_to_warehouse_dict = {"蓝铁矿": "矿物", "高容谷地电池": "产物", "源矿": "矿物"}

# stage category identifiers (logic layer)
STAGE_CATEGORY_IDS = [
    "operator_training",
    "weapon_training",
    "danger_playback",
    "danger_preview",
    "energy_silt_point",
]


def stage_category_id_to_label(category_id: str) -> str:
    return lang_world_map.get_stage_category_label(category_id, locale="zh_CN")


def canonicalize_stage_category(category_or_id: str) -> str:
    return lang_world_map.canonicalize_stage_category(category_or_id, locale="zh_CN")


stages_dict_by_id = {
    "operator_training": [
        "干员经验",
        "干员进阶",
        "钱币收集",
        "技能提升",
    ],
    "weapon_training": [
        "武器经验",
        "武器进阶",
    ],
    "danger_playback": [
        "罗丹",
        "三位一体",
        "白垩界卫",
        "阮一",
        "聂菲斯",
    ],
    "danger_preview": [
        "D96钢",
        "超距辉映管",
        "快子遴捡晶格",
        "象限拟合液",
        "三相纳米片",
    ],
    "energy_silt_point": [
        "枢纽区",
        "源石研究园",
        "试验园区",
        "矿脉源区",
        "供能高地",
        "武陵城",
        "清波寨",
        "首墩",
    ],
}

stages_dict = {
    stage_category_id_to_label(category_id): list(stages)
    for category_id, stages in stages_dict_by_id.items()
}

stages_cost_by_id = {
    "operator_training": 80,
    "weapon_training": 80,
    "danger_playback": 120,
    "danger_preview": 80,
    "energy_silt_point": 80,
}

stages_cost = {
    stage_category_id_to_label(category_id): cost
    for category_id, cost in stages_cost_by_id.items()
}

stages_list = [stage for stages in stages_dict.values() for stage in stages]

higher_order_feature_dict = {
    "D96钢": fL.higher_order_d96,
    "超距辉映管": fL.higher_order_reflection_tube,
    "快子遴捡晶格": fL.higher_order_lattice,
    "象限拟合液": fL.higher_order_quadrant_liquid,
    "三相纳米片": fL.higher_order_three_photos,
}


def get_outposts(area_or_id: str) -> list[str]:
    return list(outpost_dict_by_id.get(canonicalize_area(area_or_id), []))


def get_goods(area_or_id: str) -> list[str]:
    return list(goods_dict_by_id.get(canonicalize_area(area_or_id), []))


def get_exchange_goods(area_or_id: str) -> list[str]:
    return list(exchange_goods_dict_by_id.get(canonicalize_area(area_or_id), []))
