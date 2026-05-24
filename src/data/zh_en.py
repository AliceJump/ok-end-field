from __future__ import annotations

from typing import Any

from src.data.lang import (
    get_item_category_en_by_name,
    get_item_translation_map,
    get_item_warehouse_category_en_by_name,
)


ITEM_CATEGORY_EN_BY_ZH = get_item_category_en_by_name(locale="zh_CN")
ITEM_WAREHOUSE_CATEGORY_EN_BY_ZH = get_item_warehouse_category_en_by_name(locale="zh_CN")
ITEM_TRANSLATION_DICT = get_item_translation_map(locale="zh_CN")


def get_item_category_map(context: Any = None, locale: str | None = None) -> dict[str, str]:
    return get_item_category_en_by_name(locale=locale, context=context)


def get_item_warehouse_category_map(context: Any = None, locale: str | None = None) -> dict[str, str]:
    return get_item_warehouse_category_en_by_name(locale=locale, context=context)


def get_item_translation_dict(context: Any = None, locale: str | None = None) -> dict[str, str]:
    return get_item_translation_map(locale=locale, context=context)
