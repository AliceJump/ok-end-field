from __future__ import annotations

from typing import Any

from src.data.lang import normalize as lang_normalize


ocr_confusion_map = lang_normalize.get_ocr_confusion_map()


def get_runtime_ocr_confusion_map(context: Any = None, locale: str | None = None) -> dict[str, list[str]]:
    return lang_normalize.get_ocr_confusion_map(locale=locale, context=context)
