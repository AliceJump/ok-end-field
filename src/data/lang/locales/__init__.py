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
    file_name = _LOCALE_FILES.get(locale)
    if file_name is None:
        raise ValueError(f"Unsupported locale: {locale}")
    locale_file = _BASE_DIR / file_name
    try:
        return json.loads(locale_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Locale file not found for {locale}: {locale_file}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid locale JSON for {locale}: {locale_file}") from exc


zh_CN = load_locale_data("zh_CN")
zh_TW = load_locale_data("zh_TW")

__all__ = ["load_locale_data", "zh_CN", "zh_TW"]
