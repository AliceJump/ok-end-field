import re

from src.data.FeatureList import FeatureList
from src.data.delivery_area import DELIVERY_AREA_CONFIG

VALID_FEATURE_LABELS = {feature.value for feature in FeatureList}


def format_delivery_ticket_price_code(target_ticket_num: str) -> str | None:
    """把目标券数转换成接单特征里使用的价格码。

    规则示例：
    - 73100 -> 7_31w
    - 79800 -> 7_98w
    - 119000 -> 11_9w

    返回 None 表示输入不是合法整数，或者无法转换成正的万券格式。
    """
    try:
        ticket_num = int(str(target_ticket_num).strip())
    except (TypeError, ValueError):
        return None

    if ticket_num <= 0:
        return None

    price_in_w = ticket_num / 10000
    price_text = f"{price_in_w:.2f}".rstrip("0").rstrip(".")
    return price_text.replace(".", "_") + "w"


def _get_area_config(area_name: str) -> dict:
    if area_name not in DELIVERY_AREA_CONFIG:
        supported = "、".join(DELIVERY_AREA_CONFIG.keys())
        raise ValueError(f"不支持的送货区域: {area_name}，当前支持: {supported}")
    return DELIVERY_AREA_CONFIG[area_name]


def get_delivery_locations(area_name: str) -> list[str]:
    return _get_area_config(area_name)["delivery_locations"]


def get_delivery_targets(area_name: str) -> list[str]:
    area_config = _get_area_config(area_name)
    targets_by_location = area_config["delivery_targets_by_location"]
    targets = []
    for location_name in area_config["delivery_locations"]:
        targets.extend(targets_by_location.get(location_name, []))
    return targets


def get_ocr_priority_locations(area_name: str) -> list[str]:
    return _get_area_config(area_name)["ocr_priority_locations"]


def get_full_cycle_targets(area_name: str, location_name: str) -> list[str]:
    return _get_area_config(area_name)["delivery_targets_by_location"].get(location_name, [])


def extract_delivery_location(text: str, area_name: str) -> str | None:
    for location_name in get_delivery_locations(area_name):
        if location_name in text:
            return location_name
    return None


def get_transfer_search_area(location_name: str | None, area_name: str) -> dict | None:
    """返回指定地点的传送搜索配置；可能是 {"preset": "..."}、坐标字典或 None。"""
    if location_name is None:
        return None
    return _get_area_config(area_name)["transfer_search_area"].get(location_name)


def get_task_model_area(area_name: str) -> str:
    return _get_area_config(area_name).get("task_model_area", area_name)


def get_delivery_target_ocr_pattern(area_name: str, target_name: str) -> re.Pattern:
    pattern_overrides = _get_area_config(area_name).get("target_ocr_pattern_overrides", {})
    return re.compile(pattern_overrides.get(target_name, target_name))


def get_accept_feature_labels(area_name: str, target_ticket_num: str) -> list[str]:
    area_code = _get_area_config(area_name).get("feature_label_area_code")
    price_code = format_delivery_ticket_price_code(target_ticket_num)
    if not area_code or not price_code:
        return []
    label = f"{area_code}_{price_code}"
    if label not in VALID_FEATURE_LABELS:
        return []
    return [label]
