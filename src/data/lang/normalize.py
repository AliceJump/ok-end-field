from __future__ import annotations

from typing import Any

from src.data.lang.locale_data import get_locale_data


def get_ocr_confusion_map(*, locale: str | None = None, context: Any = None) -> dict[str, list[str]]:
    payload = get_locale_data(locale=locale, context=context)
    normalize = payload.get("normalize", {})
    confusion = normalize.get("ocr_confusion_map", {})
    return {
        str(k): [str(v) for v in values]
        for k, values in confusion.items()
    }


def get_text_normalize_tables(*, locale: str | None = None, context: Any = None) -> tuple[dict[str, str], dict[str, str]]:
    payload = get_locale_data(locale=locale, context=context)
    normalize = payload.get("normalize", {})
    punctuation_map = normalize.get("punctuation_map", {})
    traditional_to_simplified_map = normalize.get("traditional_to_simplified_map", {})
    return (
        {str(k): str(v) for k, v in punctuation_map.items()},
        {str(k): str(v) for k, v in traditional_to_simplified_map.items()},
    )
