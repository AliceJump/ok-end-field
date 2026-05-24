from __future__ import annotations

import re
from typing import Any

from src.data.lang.locales.zh_cn import DATA as ZH_CN_DATA
from src.data.lang.locales.zh_tw import DATA as ZH_TW_DATA
from src.data.lang.runtime_locale import canonicalize_locale, get_runtime_locale


_LOCALE_DATA = {
    "zh_CN": ZH_CN_DATA,
    "zh_TW": ZH_TW_DATA,
}

_DEFAULT_LOCALE = "zh_CN"


def resolve_supported_locale(locale: str | None = None, context: Any = None) -> str:
    if locale is None:
        locale = get_runtime_locale(context=context, fallback=_DEFAULT_LOCALE)
    canonical = canonicalize_locale(locale, fallback=_DEFAULT_LOCALE)
    return canonical if canonical in _LOCALE_DATA else _DEFAULT_LOCALE


def get_locale_data(locale: str | None = None, context: Any = None) -> dict[str, Any]:
    return _LOCALE_DATA[resolve_supported_locale(locale=locale, context=context)]


def get_ocr_confusion_map(locale: str | None = None, context: Any = None) -> dict[str, list[str]]:
    payload = get_locale_data(locale=locale, context=context)
    confusion = payload.get("normalize", {}).get("ocr_confusion_map", {})
    return dict(confusion)


def get_sequence_delimiters(locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
    delimiters = payload.get("parser", {}).get("sequence", {}).get("delimiters", [])
    return list(delimiters)


def get_auto_pick_terms(locale: str | None = None, context: Any = None) -> tuple[set[str], set[str]]:
    payload = get_locale_data(locale=locale, context=context).get("auto_pick", {})
    return set(payload.get("white_list", [])), set(payload.get("black_list", []))


def get_warehouse_transfer_data(locale: str | None = None, context: Any = None) -> dict[str, Any]:
    return dict(get_locale_data(locale=locale, context=context).get("warehouse_transfer", {}))


def get_warehouse_location_labels(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_warehouse_transfer_data(locale=locale, context=context)
    return dict(payload.get("locations", {}))


def get_warehouse_current_location_rules(locale: str | None = None, context: Any = None) -> dict[str, list[list[str]]]:
    payload = get_warehouse_transfer_data(locale=locale, context=context)
    return {
        key: [list(group) for group in groups]
        for key, groups in payload.get("current_location_rules", {}).items()
    }


def get_warehouse_ocr_pattern_tokens(locale: str | None = None, context: Any = None) -> dict[str, list[str]]:
    payload = get_warehouse_transfer_data(locale=locale, context=context)
    return {
        key: list(value) if isinstance(value, list) else [str(value)]
        for key, value in payload.get("ocr_patterns", {}).items()
    }


def compile_any_pattern(patterns: list[str] | tuple[str, ...] | str) -> re.Pattern[str]:
    if isinstance(patterns, str):
        tokens = [patterns]
    else:
        tokens = [str(token) for token in patterns if str(token).strip()]
    escaped = [re.escape(token) for token in tokens]
    joined = "|".join(escaped) if escaped else r"^$"
    return re.compile(joined)


def get_item_category_en_by_name(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_category_en_by_name", {}))


def get_item_warehouse_category_en_by_name(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_warehouse_category_en_by_name", {}))


def get_item_translation_map(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_translation_map", {}))
