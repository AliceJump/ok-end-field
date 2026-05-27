from src.data.FeatureList import FeatureList as fL

STAGE_CATEGORY_OPERATOR = "干员养成"
STAGE_CATEGORY_WEAPON = "武器养成"
STAGE_CATEGORY_DANGER_RECUR = "危境再现"
STAGE_CATEGORY_DANGER_REHEARSAL = "危境预演"
STAGE_CATEGORY_ENERGY_POOLING = "能量淤积点"

areas_list = ["武陵", "四号谷地"]
outpost_dict = {
    "武陵": ["天王坪援建点", "心脏修缮站"],
    "四号谷地": ["难民暂居处", "基建前站", "重建指挥部"]
}
goods_dict = {
    "四号谷地": [
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
    "武陵": [
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
    ]
}
exchange_goods_dict = {
    "四号谷地": [
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
    "武陵": [
        "冬虫夏笋货组",
        "岳研避瘴茶货组",
        "武陵冻梨货组",
        "武侠电影货组",
        "息壤净水芯货组",
        "天师龙泡泡货组",
        "清波筏货组",
    ]
}
item_to_warehouse_dict = {"蓝铁矿": "矿物", "高容谷地电池": "产物", "源矿": "矿物", "致密源石粉末": "产物"}
stages_dict = {
    STAGE_CATEGORY_OPERATOR: [
        "干员经验",
        "干员进阶",
        "钱币收集",
        "技能提升",
    ],
    STAGE_CATEGORY_WEAPON: [
        "武器经验",
        "武器进阶",
    ],
    STAGE_CATEGORY_DANGER_RECUR: [
        "罗丹",
        "三位一体",
        "白垩界卫",
        "阮一",
        "聂菲斯",
    ],
    STAGE_CATEGORY_DANGER_REHEARSAL: [
        "D96钢",
        "超距辉映管",
        "快子遴捡晶格",
        "象限拟合液",
        "三相纳米片",
    ],
    STAGE_CATEGORY_ENERGY_POOLING: [
        "枢纽区",
        "源石研究园",
        "试验园区",
        "矿脉源区",
        "供能高地",
        "武陵城",
        "清波寨",
        "首墩",
    ]
}
stages_cost = {
    STAGE_CATEGORY_OPERATOR: 80,
    STAGE_CATEGORY_WEAPON: 80,
    STAGE_CATEGORY_DANGER_RECUR: 120,
    STAGE_CATEGORY_DANGER_REHEARSAL: 80,
    STAGE_CATEGORY_ENERGY_POOLING: 80,
}
stages_list = [stage for stages in stages_dict.values() for stage in stages]
higher_order_feature_dict = {
    "D96钢": fL.higher_order_d96,
    "超距辉映管": fL.higher_order_reflection_tube,
    "快子遴捡晶格": fL.higher_order_lattice,
    "象限拟合液": fL.higher_order_quadrant_liquid,
    "三相纳米片": fL.higher_order_three_photos,
}
