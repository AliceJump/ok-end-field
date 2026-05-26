from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.data.lang.runtime_locale import canonicalize_locale, get_runtime_locale

_LANG_ROOT = Path(__file__).resolve().parent
_LEGACY_LOCALES_DIR = _LANG_ROOT / "locales"
_LOCALE_FILE_NAME = {
    "zh_CN": "zh_cn.json",
    "zh_TW": "zh_tw.json",
    "en": "en.json",
}
_DEFAULT_LOCALE = "zh_CN"


def _deep_merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_locale_fragment(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.parent.name == "ocr" and any(k in payload for k in ("terms", "regex", "aliases", "regex_aliases")):
        return {"ocr": payload}
    return payload


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Locale file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid locale JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Locale JSON root must be object: {path}")
    return data


@lru_cache(maxsize=None)
def _load_locale_data(locale: str) -> dict[str, Any]:
    file_name = _LOCALE_FILE_NAME.get(locale)
    if file_name is None:
        raise ValueError(f"Unsupported locale: {locale}")

    merged: dict[str, Any] = {}

    legacy_file = _LEGACY_LOCALES_DIR / file_name
    if legacy_file.exists():
        merged = _deep_merge_dict(merged, _normalize_locale_fragment(legacy_file, _load_json_file(legacy_file)))

    module_files = sorted(
        path for path in _LANG_ROOT.rglob(file_name)
        if path.parent.name != "locales"
    )
    for module_file in module_files:
        merged = _deep_merge_dict(merged, _normalize_locale_fragment(module_file, _load_json_file(module_file)))

    if not merged:
        raise FileNotFoundError(f"Locale data not found for {locale} ({file_name}) under {_LANG_ROOT}")

    return merged


_LOCALE_DATA = {locale: _load_locale_data(locale) for locale in _LOCALE_FILE_NAME}


def resolve_supported_locale(locale: str | None = None, context: Any = None) -> str:
    if locale is None:
        locale = get_runtime_locale(context=context, fallback=_DEFAULT_LOCALE)
    canonical = canonicalize_locale(locale, fallback=_DEFAULT_LOCALE)
    return canonical if canonical in _LOCALE_DATA else _DEFAULT_LOCALE


def get_locale_data(locale: str | None = None, context: Any = None) -> dict[str, Any]:
    return _LOCALE_DATA[resolve_supported_locale(locale=locale, context=context)]


def _coerce_str_list(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values] if values.strip() else []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value)
        if text.strip():
            result.append(text)
    return result


def _iter_alias_related_keys(key: str, aliases: dict[str, list[str]]) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    queue: list[str] = [key]

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        discovered.append(current)

        for nxt in aliases.get(current, []):
            if nxt not in seen:
                queue.append(nxt)

        for alias_key, targets in aliases.items():
            if current in targets and alias_key not in seen:
                queue.append(alias_key)

    return discovered


def _get_ocr_terms(payload: dict[str, Any], key: str) -> list[str]:
    ocr_payload = payload.get("ocr", {})
    terms_map = ocr_payload.get("terms", {})
    aliases = {
        str(alias): _coerce_str_list(targets)
        for alias, targets in ocr_payload.get("aliases", {}).items()
    }

    result: list[str] = []
    seen_terms: set[str] = set()
    for related_key in _iter_alias_related_keys(key, aliases):
        for term in _coerce_str_list(terms_map.get(related_key, [])):
            if term not in seen_terms:
                seen_terms.add(term)
                result.append(term)
    return result


def _get_ocr_regex_raw(payload: dict[str, Any], key: str) -> str | None:
    ocr_payload = payload.get("ocr", {})
    regex_map = ocr_payload.get("regex", {})
    aliases = {
        str(alias): _coerce_str_list(targets)
        for alias, targets in ocr_payload.get("regex_aliases", {}).items()
    }

    for candidate in _iter_alias_related_keys(key, aliases):
        if candidate in regex_map:
            return str(regex_map[candidate])
    return None


def get_ocr_confusion_map(locale: str | None = None, context: Any = None) -> dict[str, list[str]]:
    payload = get_locale_data(locale=locale, context=context)
    confusion = payload.get("normalize", {}).get("ocr_confusion_map", {})
    return {
        str(k): _coerce_str_list(values)
        for k, values in confusion.items()
    }


def get_sequence_delimiters(locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
    delimiters = payload.get("parser", {}).get("sequence", {}).get("delimiters", [])
    return _coerce_str_list(delimiters)


def get_auto_pick_terms(locale: str | None = None, context: Any = None) -> tuple[set[str], set[str]]:
    payload = get_locale_data(locale=locale, context=context).get("auto_pick", {})
    return set(_coerce_str_list(payload.get("white_list", []))), set(_coerce_str_list(payload.get("black_list", [])))


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
        key: _coerce_str_list(value)
        for key, value in payload.get("ocr_patterns", {}).items()
    }


def compile_any_pattern(patterns: list[str] | tuple[str, ...] | str) -> re.Pattern[str]:
    if isinstance(patterns, str):
        tokens = _coerce_str_list(patterns)
    else:
        tokens = [str(token) for token in patterns if str(token).strip()]
    escaped = [re.escape(token) for token in tokens]
    joined = "|".join(escaped) if escaped else r"^$"
    return re.compile(f"(?:{joined})")


def get_item_category_en_by_name(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_category_en_by_name", {}))


def get_item_warehouse_category_en_by_name(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_warehouse_category_en_by_name", {}))


def get_item_translation_map(locale: str | None = None, context: Any = None) -> dict[str, str]:
    payload = get_locale_data(locale=locale, context=context)
    return dict(payload.get("item_translation_map", {}))


def _get_text(payload: dict[str, Any], key: str, default: str | None = None) -> str:
    value = payload.get("text", {}).get(key)
    if isinstance(value, str) and value.strip():
        return value
    terms = _get_ocr_terms(payload, key)
    if terms:
        return terms[0]
    if default is not None:
        return default
    return key


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
        return _get_ocr_terms(payload, key)

    def pattern(self, key: str, locale: str | None = None) -> re.Pattern[str]:
        return compile_any_pattern(self.terms(key, locale=locale))

    def term(self, key: str, locale: str | None = None) -> str:
        values = self.terms(key, locale=locale)
        return values[0] if values else ""

    def t(self, key: str, default: str | None = None, locale: str | None = None) -> str:
        return _get_text(self.data(locale=locale), key, default=default)

    def regex(self, key: str, locale: str | None = None) -> re.Pattern[str]:
        payload = self.data(locale=locale)
        raw = _get_ocr_regex_raw(payload, key)
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

    def t(self, key: str, default: str | None = None, _locale: str | None = None) -> str:
        return self._accessor.t(key, default=default, locale=self._locale if _locale is None else _locale)

    def regex(self, key: str, _locale: str | None = None) -> re.Pattern[str]:
        return self._accessor.regex(key, locale=self._locale if _locale is None else _locale)

    def contains(self, key: str, text: str, _locale: str | None = None) -> bool:
        return self._accessor.contains(key, text, locale=self._locale if _locale is None else _locale)
