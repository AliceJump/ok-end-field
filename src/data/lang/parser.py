from __future__ import annotations

from typing import Any

from src.data.lang.locale_data import get_locale_data


def get_sequence_delimiters(*, locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
    delimiters = payload.get("parser", {}).get("sequence", {}).get("delimiters", [])
    return [str(v) for v in delimiters if str(v).strip()]


def get_essence_parser_terms(*, locale: str | None = None, context: Any = None) -> dict[str, Any]:
    payload = get_locale_data(locale=locale, context=context)
    essence = payload.get("parser", {}).get("essence", {})
    return {
        "essence_keywords": list(essence.get("essence_keywords", [])),
        "affix_keywords": list(essence.get("affix_keywords", [])),
        "gold_keywords": list(essence.get("gold_keywords", [])),
        "name_regex": str(essence.get("name_regex", r"([\\u4e00-\\u9fff]+)\\s*([\\u4e00-\\u9fff]+)?")),
    }
