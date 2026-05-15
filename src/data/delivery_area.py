# 当前默认启用的送货地区；新地区未显式选择时会回退到这里。
DEFAULT_DELIVERY_AREA = "武陵"

# 目标券数下拉框选项。
# 这些值会直接用于接单特征标签生成和任务界面下拉展示。
DELIVERY_TARGET_TICKET_NUM_OPTIONS = ["119000", "79800", "73100"]

# 按“地区”组织的送货配置。
# 结构说明：
# - task_model_area: 进入任务地图后使用的任务区域名，可省略，省略时默认等于地区名
# - feature_label_area_code: 接单特征标签前缀
# - delivery_locations: 当前地区下可识别的委托地点名
# - delivery_targets_by_location: 每个地点对应的送货目标 NPC 列表
# - transfer_search_area: 每个地点在地图中搜索传送点时使用的区域，支持 preset 或坐标两种写法
# - ocr_priority_locations: OCR 识别时优先匹配的地点顺序
# - target_ocr_pattern_overrides: 送货目标名的 OCR 兼容写法
#
# 新地区完整样例（复制后按实际地图改名即可）：
# DELIVERY_AREA_CONFIG = {
#     "新区": {
#         # 任务地图区域名；如果和地区名一致，可以写，也可以省略。
#         "task_model_area": "新区",
#         "feature_label_area_code": "new_area",
#         "delivery_locations": ["新区主城", "新区外环"],
#         "delivery_targets_by_location": {
#             "新区主城": ["目标A", "目标B"],
#             "新区外环": ["目标C", "目标D"],
#         },
#         "transfer_search_area": {
#             # 写法1：直接用页面预设区域名
#             "新区主城": {"preset": "top"},
#             # 写法2：用相对屏幕比例坐标定义区域
#             # "新区外环": {"x": 0.70, "y": 0.20, "to_x": 0.98, "to_y": 0.80},
#             "新区外环": {"preset": "right"},
#         },
#         "ocr_priority_locations": ["新区外环", "新区主城"],
#         "target_ocr_pattern_overrides": {
#             "目标A": r"目标[阿A]",
#         },
#     }
# }
DELIVERY_AREA_CONFIG = {
    "武陵": {
        "task_model_area": "武陵",
        "feature_label_area_code": "wuling",
        # 接单时会在委托文本里识别这些地点名，识别到哪个就缓存哪个地点。
        "delivery_locations": ["武陵城", "试验园区"],
        # 不同地点对应的目标 NPC 列表，送达时会按这里的名单做 OCR 匹配。
        "delivery_targets_by_location": {
            "武陵城": ["常沄", "资源", "彦宁", "齐纶"],
            "试验园区": ["赵昭", "裴令容", "阿禾"],
        },
        # 不同地点在地图里搜索传送点的区域可能不同，因此这里单独配置。
        "transfer_search_area": {
            "武陵城": {"preset": "top"},
            "试验园区": {"preset": "right"},
        },
        # OCR 识别地点名时的优先顺序，越靠前越先尝试。
        "ocr_priority_locations": ["试验园区", "武陵城"],
        # 某些目标名存在识别偏差时，用正则兼容不同写法。
        "target_ocr_pattern_overrides": {
            "常沄": r"常[沄云汶运法]",
        },
    }
}
