import re

from src.data.FeatureList import FeatureList
from src.data.delivery_area import DELIVERY_AREA_CONFIG
from src.data.world_map_utils import get_world_map_matcher, get_world_map_text

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


def get_delivery_locations(area_name: str, lang_accessor=None) -> list[str]:
    locations = _get_area_config(area_name)["delivery_locations"]
    if lang_accessor is None:
        return list(locations)
    return [get_world_map_text(lang_accessor, location_name) for location_name in locations]


def get_delivery_targets(area_name: str, lang_accessor=None) -> list[str]:
    area_config = _get_area_config(area_name)
    targets_by_location = area_config["delivery_targets_by_location"]
    targets = []
    for location_name in area_config["delivery_locations"]:
        targets.extend(targets_by_location.get(location_name, []))
    if lang_accessor is None:
        return targets
    return [get_world_map_text(lang_accessor, target_name) for target_name in targets]


def get_ocr_priority_locations(area_name: str, lang_accessor=None) -> list[str]:
    locations = _get_area_config(area_name)["ocr_priority_locations"]
    if lang_accessor is None:
        return list(locations)
    return [get_world_map_text(lang_accessor, location_name) for location_name in locations]


def get_full_cycle_targets(area_name: str, location_name: str, lang_accessor=None) -> list[str]:
    targets = _get_area_config(area_name)["delivery_targets_by_location"].get(location_name, [])
    if lang_accessor is None:
        return list(targets)
    return [get_world_map_text(lang_accessor, target_name) for target_name in targets]


def extract_delivery_location(text: str, area_name: str, lang_accessor=None) -> str | None:
    canonical_locations = get_delivery_locations(area_name)
    localized_locations = get_delivery_locations(area_name, lang_accessor=lang_accessor)
    for canonical_name, localized_name in zip(canonical_locations, localized_locations):
        if canonical_name in text or localized_name in text:
            return canonical_name
    return None


def get_transfer_search_area(location_name: str | None, area_name: str) -> dict | None:
    """返回指定地点的传送搜索配置；可能是 {"preset": "..."}、坐标字典或 None。"""
    if location_name is None:
        return None
    return _get_area_config(area_name)["transfer_search_area"].get(location_name)


def get_task_model_area(area_name: str, lang_accessor=None) -> str:
    task_model_area = _get_area_config(area_name).get("task_model_area", area_name)
    if lang_accessor is None:
        return task_model_area
    return get_world_map_text(lang_accessor, task_model_area)


def compile_ocr_pattern(text: str, override_text: str | None = None) -> re.Pattern:
    """Compile a data-driven OCR pattern with optional override text."""
    pattern_text = override_text or text
    return re.compile(pattern_text)


def _normalize_matcher_to_pattern(matcher, fallback_text: str) -> re.Pattern:
    if isinstance(matcher, re.Pattern):
        return matcher
    if isinstance(matcher, str) and matcher:
        return re.compile(matcher)
    if isinstance(matcher, list):
        candidates = [str(item).strip() for item in matcher if str(item).strip()]
        if candidates:
            return re.compile("|".join(re.escape(item) for item in candidates))
    return re.compile(fallback_text)


def get_delivery_target_ocr_pattern(_area_name: str, target_name: str, lang_accessor=None) -> re.Pattern:
    if lang_accessor is not None:
        return _normalize_matcher_to_pattern(get_world_map_matcher(lang_accessor, target_name), target_name)
    return compile_ocr_pattern(target_name)


def get_accept_feature_labels(area_name: str, target_ticket_num: str) -> list[str]:
    area_code = _get_area_config(area_name).get("feature_label_area_code")
    price_code = format_delivery_ticket_price_code(target_ticket_num)
    if not area_code or not price_code:
        return []
    label = f"{area_code}_{price_code}"
    if label not in VALID_FEATURE_LABELS:
        return []
    return [label]
