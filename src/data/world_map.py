"""地图/关卡/货物数据（剥离自内嵌字典，见 assets/data/world_map.json）。

纯数据部分（areas_list/outpost_dict/goods_dict/exchange_goods_dict/
item_to_warehouse_dict/stages_dict/stages_cost）在 assets/data/world_map.json；
涉及 FeatureList 枚举的 higher_order_feature_dict 在本模块重建。

保持原有导入路径兼容：``from src.data.world_map import stages_dict`` 等。
"""

import json
from pathlib import Path

from src.data.FeatureList import FeatureList as fL

_DATA = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "assets" / "data" / "world_map.json").read_text(encoding="utf-8")
)

STAGE_CATEGORY_OPERATOR = "干员养成"
STAGE_CATEGORY_WEAPON = "武器养成"
STAGE_CATEGORY_DANGER_RECUR = "危境再现"
STAGE_CATEGORY_DANGER_REHEARSAL = "危境预演"
STAGE_CATEGORY_ENERGY_POOLING = "能量淤积点"
YINGTUO_MONUMENT = "影拓丰碑"

permanent_dict = {
    YINGTUO_MONUMENT: _DATA.get("permanent_dict", {}).get(
        YINGTUO_MONUMENT,
        ["大地的弃子", "无机造物", "灼痛疤痕", "浊流具现", "死寂争鸣"],
    )
}

areas_list = _DATA["areas_list"]
outpost_dict = _DATA["outpost_dict"]
goods_dict = _DATA["goods_dict"]
exchange_goods_dict = _DATA["exchange_goods_dict"]
item_to_warehouse_dict = _DATA["item_to_warehouse_dict"]
stages_dict = _DATA["stages_dict"]
stages_cost = _DATA["stages_cost"]
stages_list = [stage for stages in stages_dict.values() for stage in stages]
higher_order_feature_dict = {
    "D96钢": fL.higher_order_d96,
    "超距辉映管": fL.higher_order_reflection_tube,
    "快子遴捡晶格": fL.higher_order_lattice,
    "象限拟合液": fL.higher_order_quadrant_liquid,
    "三相纳米片": fL.higher_order_three_photos,
}
