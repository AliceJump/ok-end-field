from __future__ import annotations

import re
from typing import Any

from src.data.lang.lang_helpers import collect_alias_terms, get_alias_mapped_value, to_str_list
from src.data.lang.locale_data import compile_any_pattern, get_locale_data


def get_terms(key: str, *, locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
    ocr_payload = payload.get("ocr", {})
    terms_map = ocr_payload.get("terms", {})
    aliases = {
        str(alias): to_str_list(targets)
        for alias, targets in ocr_payload.get("aliases", {}).items()
    }
    return collect_alias_terms(key, terms_map, aliases)


def get_pattern(key: str, *, locale: str | None = None, context: Any = None) -> re.Pattern[str]:
    return compile_any_pattern(get_terms(key, locale=locale, context=context))


def get_primary_term(key: str, *, locale: str | None = None, context: Any = None) -> str:
    terms = get_terms(key, locale=locale, context=context)
    return terms[0] if terms else ""


def get_regex_pattern(key: str, *, locale: str | None = None, context: Any = None) -> re.Pattern[str]:
    payload = get_locale_data(locale=locale, context=context)
    ocr_payload = payload.get("ocr", {})
    regex_map = ocr_payload.get("regex", {})
    aliases = {
        str(alias): to_str_list(targets)
        for alias, targets in ocr_payload.get("regex_aliases", {}).items()
    }

    raw = get_alias_mapped_value(key, regex_map, aliases)
    if raw is None:
        raise KeyError(f"Missing OCR regex key: {key}")
    try:
        return re.compile(str(raw))
    except re.error as exc:
        raise ValueError(f"Invalid OCR regex for key '{key}': {raw}") from exc
