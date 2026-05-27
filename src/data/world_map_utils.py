import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.data.lang import LangNode, build_matcher
from src.data.world_map import outpost_dict, goods_dict, stages_dict


def get_area_by_outpost_name(outpost_name: str) -> str:
    """
    根据据点名称获取该据点所在区域
    参数:
        outpost_name: 据点名称
    返回值:
        该据点所在区域，如果据点不存在返回空字符串
    """
    for area, outposts in outpost_dict.items():
        if outpost_name in outposts:
            return area
    return ""


def get_goods_by_outpost_name(outpost_name: str) -> list[str]:
    """
    根据据点名称获取该据点可交易的货物列表
    参数:
        outpost_name: 据点名称
    返回值:
        该据点的货物列表，如果据点不存在返回空列表
    """
    for area, outposts in outpost_dict.items():
        if outpost_name in outposts:
            return goods_dict.get(area, [])
    return []


def get_stage_category(stage_name):
    for category, stages in stages_dict.items():
        if stage_name in stages:
            return category
    return None


@lru_cache(maxsize=1)
def _world_map_zh_key_map() -> dict[str, str]:
    """Build and cache a reverse index from world_map zh text values to lang keys."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "assets" / "lang" / "world_map" / "zh_CN.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    result = {}
    for key, node in data.items():
        if not isinstance(node, dict):
            continue
        text = node.get("pattern") or node.get("string")
        if isinstance(text, str) and text and text not in result:
            result[text] = key
    return result


def get_world_map_matcher(lang_accessor: Any, zh_text: str) -> re.Pattern | str | list | None:
    """
    Get world_map matcher for current locale.

    Returns:
        re.Pattern: most common matcher type from lang world_map pattern/string.
        str | list: returned when locale node is defined as string or terms matcher.
        None: when zh_text is empty.
    Falls back to re.compile(zh_text) when lang_accessor is None or locale data is unavailable.
    """
    if not zh_text:
        return None
    key = _world_map_zh_key_map().get(zh_text)
    if key and lang_accessor is not None:
        try:
            module = getattr(lang_accessor, "world_map")
            node = getattr(module, "_data", {}).get(key)
            matcher = build_matcher(LangNode(node)) if isinstance(node, dict) else None
            if matcher is not None:
                return matcher
        except Exception:
            pass
    return re.compile(zh_text)


def get_world_map_text(lang_accessor: Any, zh_text: str) -> str:
    """Get locale text (or matcher.pattern) for comparison, fallback to zh_text."""
    matcher = get_world_map_matcher(lang_accessor, zh_text)
    if isinstance(matcher, str):
        return matcher
    if hasattr(matcher, "pattern"):
        return matcher.pattern
    return zh_text


def is_world_map_text(lang_accessor: Any, value: str | None, zh_text: str) -> bool:
    """
    Check whether a value equals zh_text or its current locale counterpart.

    Args:
        value: Runtime text value to compare (category/stage text from OCR or config).
        zh_text: Canonical zh_CN text key source used by world_map locale files.
    The comparison is `value == zh_text or value == locale_text`;
    when lang_accessor is None, locale_text will fall back to zh_text.
    """
    if not value:
        return False
    locale_text = get_world_map_text(lang_accessor, zh_text)
    return value == zh_text or value == locale_text
