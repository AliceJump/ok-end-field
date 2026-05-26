from __future__ import annotations

import re
from typing import Any

from src.data.lang.locale_data import compile_any_pattern, get_locale_data


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


def get_terms(key: str, *, locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
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
        str(alias): _coerce_str_list(targets)
        for alias, targets in ocr_payload.get("regex_aliases", {}).items()
    }

    raw = None
    for candidate in _iter_alias_related_keys(key, aliases):
        if candidate in regex_map:
            raw = regex_map[candidate]
            break

    if raw is None:
        raise KeyError(f"Missing OCR regex key: {key}")
    try:
        return re.compile(str(raw))
    except re.error as exc:
        raise ValueError(f"Invalid OCR regex for key '{key}': {raw}") from exc
