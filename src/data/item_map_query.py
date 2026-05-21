from __future__ import annotations

import os
import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

try:
    from src.config import config as app_config
except ModuleNotFoundError:
    app_config = {}

MapPoint = dict[str, float]
ItemMap = dict[str, list[MapPoint]]
SummaryMap = dict[str, ItemMap]

_ITEM_MAP_CONFIG = app_config.get("item_map", {})
_SUMMARY_PATH = Path(
    _ITEM_MAP_CONFIG.get("summary_json", os.path.join("assets", "items", "map", "summary.json"))
)
_ITEM_NAMES_PATH = Path(
    _ITEM_MAP_CONFIG.get("item_names_json", os.path.join("assets", "items", "map", "item_names.json"))
)


def _normalize_names(values: str | Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return {values}
    return {value for value in values if value}


@lru_cache(maxsize=1)
def _load_summary() -> SummaryMap:
    with _SUMMARY_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


@lru_cache(maxsize=1)
def _load_item_names() -> list[str]:
    with _ITEM_NAMES_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def get_supported_item_names() -> list[str]:
    return list(_load_item_names())


def get_supported_map_types() -> list[str]:
    return list(_load_summary().keys())


def search_item_names(keyword: str) -> list[str]:
    keyword = keyword.lower()

    return [
        item_name
        for item_name in _load_item_names()
        if keyword in item_name.lower()
    ]


def get_item_map(
    item_names: str | Iterable[str],
    map_types: str | Iterable[str] | None = None,
) -> SummaryMap:
    """Return only the requested items, optionally restricted to some map types."""
    name_filter = _normalize_names(item_names)
    map_filter = _normalize_names(map_types)
    summary = _load_summary()

    result: SummaryMap = {}
    for map_type, items in summary.items():
        if map_filter is not None and map_type not in map_filter:
            continue

        filtered_items: ItemMap = {}
        for item_name, points in items.items():
            if item_name in name_filter:
                filtered_items[item_name] = points

        if filtered_items:
            result[map_type] = filtered_items

    return result


def get_item_positions(
    item_name: str,
    map_types: str | Iterable[str] | None = None,
) -> dict[str, list[MapPoint]]:
    """Return positions for one item, grouped by map type."""
    result = get_item_map(item_name, map_types)
    return {map_type: items[item_name] for map_type, items in result.items() if item_name in items}