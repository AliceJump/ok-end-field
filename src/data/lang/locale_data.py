from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.data.lang.runtime_locale import canonicalize_locale, get_runtime_locale

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_LOCALE_FILE_NAME = {
    "zh_CN": "zh_cn.json",
    "zh_TW": "zh_tw.json",
}


@lru_cache(maxsize=None)
def _load_locale_data(locale: str) -> dict[str, Any]:
    file_name = _LOCALE_FILE_NAME.get(locale)
    if file_name is None:
        raise ValueError(f"Unsupported locale: {locale}")
    locale_file = _LOCALES_DIR / file_name
    try:
        return json.loads(locale_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Locale file not found for {locale}: {locale_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid locale JSON for {locale}: {locale_file}") from exc


_LOCALE_DATA = {
    "zh_CN": _load_locale_data("zh_CN"),
    "zh_TW": _load_locale_data("zh_TW"),
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


class LangAccessor:
    """Bind language payload access to a runtime task/context instance."""

    def __init__(self, context: Any):
        self._context = context

    def locale(self, locale: str | None = None) -> str:
        return resolve_supported_locale(locale=locale, context=self._context)

    def data(self, locale: str | None = None) -> dict[str, Any]:
        return get_locale_data(locale=locale, context=self._context)

    def ocr_confusion_map(self, locale: str | None = None) -> dict[str, list[str]]:
        return get_ocr_confusion_map(locale=locale, context=self._context)

    def sequence_delimiters(self, locale: str | None = None) -> list[str]:
        return get_sequence_delimiters(locale=locale, context=self._context)

    def auto_pick_terms(self, locale: str | None = None) -> tuple[set[str], set[str]]:
        return get_auto_pick_terms(locale=locale, context=self._context)

    def warehouse_transfer_data(self, locale: str | None = None) -> dict[str, Any]:
        return get_warehouse_transfer_data(locale=locale, context=self._context)

    def warehouse_location_labels(self, locale: str | None = None) -> dict[str, str]:
        return get_warehouse_location_labels(locale=locale, context=self._context)

    def warehouse_current_location_rules(self, locale: str | None = None) -> dict[str, list[list[str]]]:
        return get_warehouse_current_location_rules(locale=locale, context=self._context)

    def warehouse_ocr_pattern_tokens(self, locale: str | None = None) -> dict[str, list[str]]:
        return get_warehouse_ocr_pattern_tokens(locale=locale, context=self._context)

    def item_category_en_by_name(self, locale: str | None = None) -> dict[str, str]:
        return get_item_category_en_by_name(locale=locale, context=self._context)

    def item_warehouse_category_en_by_name(self, locale: str | None = None) -> dict[str, str]:
        return get_item_warehouse_category_en_by_name(locale=locale, context=self._context)

    def item_translation_map(self, locale: str | None = None) -> dict[str, str]:
        return get_item_translation_map(locale=locale, context=self._context)

    def terms(self, key: str, locale: str | None = None) -> list[str]:
        payload = self.data(locale=locale)
        terms = payload.get("ocr", {}).get("terms", {})
        values = terms.get(key, [])
        if isinstance(values, str):
            return [values]
        return [str(v) for v in values if str(v).strip()]

    def pattern(self, key: str, locale: str | None = None) -> re.Pattern[str]:
        return compile_any_pattern(self.terms(key, locale=locale))

    def term(self, key: str, locale: str | None = None) -> str:
        values = self.terms(key, locale=locale)
        return values[0] if values else ""

    def regex(self, key: str, locale: str | None = None) -> re.Pattern[str]:
        payload = self.data(locale=locale)
        raw = payload.get("ocr", {}).get("regex", {}).get(key)
        if raw is None:
            raise KeyError(f"Missing OCR regex key: {key}")
        try:
            return re.compile(str(raw))
        except re.error as exc:
            raise ValueError(f"Invalid OCR regex for key '{key}': {raw}") from exc

    def contains(self, key: str, text: str, locale: str | None = None) -> bool:
        needle = self.term(key, locale=locale)
        return bool(needle) and needle in str(text)

    def with_locale(self, locale: str | None):
        """Return a locale-bound accessor that calls methods with the given locale."""
        return _LocaleBoundLangAccessor(self, locale)


def get_lang_accessor(context: Any) -> LangAccessor:
    return LangAccessor(context)


class _LocaleBoundLangAccessor:
    """A thin wrapper that binds a LangAccessor to a specific locale."""

    def __init__(self, accessor: LangAccessor, locale: str | None):
        self._accessor = accessor
        self._locale = locale

    def locale(self, _locale: str | None = None) -> str:
        return self._accessor.locale(locale=self._locale if _locale is None else _locale)

    def data(self, _locale: str | None = None) -> dict[str, Any]:
        return self._accessor.data(locale=self._locale if _locale is None else _locale)

    def ocr_confusion_map(self, _locale: str | None = None) -> dict[str, list[str]]:
        return self._accessor.ocr_confusion_map(locale=self._locale if _locale is None else _locale)

    def sequence_delimiters(self, _locale: str | None = None) -> list[str]:
        return self._accessor.sequence_delimiters(locale=self._locale if _locale is None else _locale)

    def auto_pick_terms(self, _locale: str | None = None) -> tuple[set[str], set[str]]:
        return self._accessor.auto_pick_terms(locale=self._locale if _locale is None else _locale)

    def warehouse_transfer_data(self, _locale: str | None = None) -> dict[str, Any]:
        return self._accessor.warehouse_transfer_data(locale=self._locale if _locale is None else _locale)

    def warehouse_location_labels(self, _locale: str | None = None) -> dict[str, str]:
        return self._accessor.warehouse_location_labels(locale=self._locale if _locale is None else _locale)

    def warehouse_current_location_rules(self, _locale: str | None = None) -> dict[str, list[list[str]]]:
        return self._accessor.warehouse_current_location_rules(locale=self._locale if _locale is None else _locale)

    def warehouse_ocr_pattern_tokens(self, _locale: str | None = None) -> dict[str, list[str]]:
        return self._accessor.warehouse_ocr_pattern_tokens(locale=self._locale if _locale is None else _locale)

    def item_category_en_by_name(self, _locale: str | None = None) -> dict[str, str]:
        return self._accessor.item_category_en_by_name(locale=self._locale if _locale is None else _locale)

    def item_warehouse_category_en_by_name(self, _locale: str | None = None) -> dict[str, str]:
        return self._accessor.item_warehouse_category_en_by_name(locale=self._locale if _locale is None else _locale)

    def item_translation_map(self, _locale: str | None = None) -> dict[str, str]:
        return self._accessor.item_translation_map(locale=self._locale if _locale is None else _locale)

    def terms(self, key: str, _locale: str | None = None) -> list[str]:
        return self._accessor.terms(key, locale=self._locale if _locale is None else _locale)

    def pattern(self, key: str, _locale: str | None = None) -> re.Pattern[str]:
        return self._accessor.pattern(key, locale=self._locale if _locale is None else _locale)

    def term(self, key: str, _locale: str | None = None) -> str:
        return self._accessor.term(key, locale=self._locale if _locale is None else _locale)

    def regex(self, key: str, _locale: str | None = None) -> re.Pattern[str]:
        return self._accessor.regex(key, locale=self._locale if _locale is None else _locale)

    def contains(self, key: str, text: str, _locale: str | None = None) -> bool:
        return self._accessor.contains(key, text, locale=self._locale if _locale is None else _locale)
