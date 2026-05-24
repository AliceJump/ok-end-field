from __future__ import annotations

from typing import Any

from src.data.lang import get_ocr_confusion_map


ocr_confusion_map = get_ocr_confusion_map(locale="zh_CN")


def get_runtime_ocr_confusion_map(context: Any = None, locale: str | None = None) -> dict[str, list[str]]:
    return get_ocr_confusion_map(locale=locale, context=context)
