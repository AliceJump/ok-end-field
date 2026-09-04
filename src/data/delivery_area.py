"""送货地区配置（数据剥离自 src/data/delivery_area.py，见 assets/data/delivery_area.json）。

数据在 assets/data/delivery_area.json（JSON），本模块仅作薄加载器，
保持原有导入路径兼容：``from src.data.delivery_area import DELIVERY_AREA_CONFIG``。
"""

import json
from pathlib import Path

_DATA = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "assets" / "data" / "delivery_area.json").read_text(
        encoding="utf-8"
    )
)

# 当前默认启用的送货地区；新地区未显式选择时会回退到这里。
DEFAULT_DELIVERY_AREA = _DATA["default_delivery_area"]

# 目标券数下拉框选项。
# 这些值会直接用于接单特征标签生成和任务界面下拉展示。
DELIVERY_TARGET_TICKET_NUM_OPTIONS = _DATA["delivery_target_ticket_num_options"]

# 按“地区”组织的送货配置。
# 结构说明：
# - task_model_area: 进入任务地图后使用的任务区域名，可省略，省略时默认等于地区名
# - feature_label_area_code: 接单特征标签前缀
# - delivery_locations: 当前地区下可识别的委托地点名
# - delivery_targets_by_location: 每个地点对应的送货目标 NPC 列表
# - transfer_search_area: 每个地点在地图中搜索传送点时使用的区域，支持 preset 或坐标两种写法
# - ocr_priority_locations: OCR 识别时优先匹配的地点顺序
DELIVERY_AREA_CONFIG = _DATA["delivery_area_config"]
