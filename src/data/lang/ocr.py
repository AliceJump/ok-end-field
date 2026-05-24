from __future__ import annotations

import re
from typing import Any

from src.data.lang.locale_data import compile_any_pattern, get_locale_data


def get_terms(key: str, *, locale: str | None = None, context: Any = None) -> list[str]:
    payload = get_locale_data(locale=locale, context=context)
    terms = payload.get("ocr", {}).get("terms", {})
    values = terms.get(key, [])
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values if str(v).strip()]


def get_pattern(key: str, *, locale: str | None = None, context: Any = None) -> re.Pattern[str]:
    return compile_any_pattern(get_terms(key, locale=locale, context=context))


def get_primary_term(key: str, *, locale: str | None = None, context: Any = None) -> str:
    terms = get_terms(key, locale=locale, context=context)
    return terms[0] if terms else ""
