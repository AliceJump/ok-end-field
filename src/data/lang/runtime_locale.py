from __future__ import annotations

from typing import Any

_DEFAULT_LOCALE = "zh_CN"

_LOCALE_ALIASES = {
    "zh": "zh_CN",
    "zh_cn": "zh_CN",
    "zh-hans": "zh_CN",
    "zh_hans": "zh_CN",
    "zh_tw": "zh_TW",
    "zh-tw": "zh_TW",
    "zh_hant": "zh_TW",
    "zh-hant": "zh_TW",
}


def canonicalize_locale(raw_locale: Any, fallback: str = _DEFAULT_LOCALE) -> str:
    if raw_locale is None:
        return fallback

    if hasattr(raw_locale, "name"):
        try:
            name_attr = getattr(raw_locale, "name")
            raw_locale = name_attr() if callable(name_attr) else name_attr
        except Exception:
            pass

    locale_text = str(raw_locale).strip()
    if not locale_text:
        return fallback

    key = locale_text.replace("-", "_").lower()
    if key in _LOCALE_ALIASES:
        return _LOCALE_ALIASES[key]

    if key.startswith("zh") and "tw" in key:
        return "zh_TW"
    if key.startswith("zh") and "hant" in key:
        return "zh_TW"
    if key.startswith("zh"):
        return "zh_CN"

    return locale_text.replace("-", "_")


def _extract_locale_from_object(obj: Any) -> Any:
    if obj is None:
        return None

    executor = getattr(obj, "executor", None)
    locale_obj = getattr(executor, "locale", None) if executor is not None else getattr(obj, "locale", None)
    if locale_obj is not None:
        if hasattr(locale_obj, "name"):
            try:
                name_attr = getattr(locale_obj, "name")
                value = name_attr() if callable(name_attr) else name_attr
                if value:
                    return value
            except Exception:
                pass
        if locale_obj:
            return locale_obj

    return None


def get_runtime_locale(context: Any = None, fallback: str = _DEFAULT_LOCALE) -> str:
    raw_locale = _extract_locale_from_object(context)
    return canonicalize_locale(raw_locale, fallback=fallback)
