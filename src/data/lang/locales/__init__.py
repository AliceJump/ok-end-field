from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_BASE_DIR = Path(__file__).resolve().parent
_LOCALE_FILES = {
    "zh_CN": "zh_cn.json",
    "zh_TW": "zh_tw.json",
}


@lru_cache(maxsize=None)
def load_locale_data(locale: str) -> dict[str, Any]:
    file_name = _LOCALE_FILES[locale]
    return json.loads((_BASE_DIR / file_name).read_text(encoding="utf-8"))


zh_CN = load_locale_data("zh_CN")
zh_TW = load_locale_data("zh_TW")

__all__ = ["load_locale_data", "zh_CN", "zh_TW"]
