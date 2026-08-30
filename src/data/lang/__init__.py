import re
from enum import Enum
from pathlib import Path
from typing import Any

import json5

from ._lang_typed import _LangAccessorTyped

# ============================================================
# Locale activation config
# 将 locale 键值设为 True 即可启用该语言，
# 后续激活只需将对应语言改为 True。
# ============================================================
ACTIVE_LOCALES_CONFIG: dict[str, bool] = {
    "zh_CN": True,
    "zh_TW": True,
    "en_US": False,
    "ja_JP": False,
    "ko_KR": False,
    "es_ES": False,
}


def _discover_supported_locales() -> tuple[str, ...]:
    return tuple(locale for locale, active in ACTIVE_LOCALES_CONFIG.items() if active)


SUPPORTED_LOCALES = _discover_supported_locales()
LocaleCode = Enum("LocaleCode", {name: name for name in SUPPORTED_LOCALES}, type=str)


def get_supported_locales() -> tuple[str, ...]:
    """Get a tuple of all supported locale codes that are currently active."""
    return SUPPORTED_LOCALES


def _normalize_locale(locale: str | Enum | None) -> str:
    if not locale:
        return "zh_CN"
    if isinstance(locale, Enum):
        locale = locale.value
    locale = str(locale).replace("-", "_")
    if locale in SUPPORTED_LOCALES:
        return locale

    lowered = locale.lower()
    for supported in SUPPORTED_LOCALES:
        if supported.lower() == lowered:
            return supported

    parts = locale.split("_")
    if len(parts) == 2:
        candidate = parts[0].lower() + "_" + parts[1].upper()
        for supported in SUPPORTED_LOCALES:
            if supported.lower() == candidate.lower():
                return supported
    return "zh_CN"


def _parse_lang_value(v: Any) -> Any:
    """统一解析语言节点（核心逻辑抽取）"""
    if not isinstance(v, dict):
        return v

    if v.get("string") is not None:
        return v.get("string")
    if v.get("pattern") is not None:
        try:
            return re.compile(v.get("pattern"))
        except Exception:
            return None
    if v.get("terms") is not None:
        return v.get("terms")

    return LangNode(v)


class LangNode:
    """Represents a language node that can contain string, pattern, or terms data."""

    def __init__(self, data: dict | None):
        self._data = data or {}

    def __getattr__(self, item: str):
        v = self._data.get(item)
        return _parse_lang_value(v)

    def as_matcher(self):
        """转为 matcher"""
        return build_matcher(self)

    def __str__(self) -> str:
        m = self.as_matcher()
        if m is None:
            return f"<LangNode {self._data}>"
        if isinstance(m, str):
            return m
        if hasattr(m, "pattern"):
            return m.pattern
        return str(m)

    def __repr__(self) -> str:
        return self.__str__()

    @property
    def string(self) -> str | None:
        """Get the string value from this language node, if present."""
        return self._data.get("string")

    @property
    def pattern(self) -> str | None:
        """Get the pattern value from this language node, if present."""
        return self._data.get("pattern")

    @property
    def terms(self) -> list | None:
        """Get the terms list from this language node, if present."""
        return self._data.get("terms")


class LangModule:
    """Represents a language module containing multiple language keys and their localized values."""

    def __init__(self, data: dict):
        self._data = data or {}

    def __getattr__(self, item: str):
        v = self._data.get(item)
        return _parse_lang_value(v)

    def get(self, item: str, fallback=None):
        """安全读取"""
        v = self._data.get(item)
        if isinstance(v, dict):
            # 严格保持原始行为：dict 时走 build_matcher(LangNode(v))
            return build_matcher(LangNode(v))
        if v is None:
            return fallback
        return v


class LangAccessor(_LangAccessorTyped):
    """Accessor for loading and accessing language modules for a specific locale."""

    def __init__(self, locale: str | None = None):
        self.locale = _normalize_locale(locale)
        self._cache: dict[str, LangModule] = {}
        self._repo_root = Path(__file__).resolve().parents[3]

    def __getattr__(self, module_name: str) -> LangModule:
        if module_name in self._cache:
            return self._cache[module_name]

        data = self._load_module(module_name)
        mod = LangModule(data)
        self._cache[module_name] = mod
        return mod

    def _load_module(self, module_name: str) -> dict:
        lang_root = self._repo_root / "assets" / "lang"
        unified_path = lang_root / f"{module_name}.json"

        if not unified_path.exists():
            return {}

        try:
            raw_data = json5.load(unified_path.open(encoding="utf-8"))
        except Exception:
            return {}

        # 确定当前 locale 及其 fallback
        primary_locale = self.locale
        fallback_locale = "zh_TW" if self.locale == "zh_TW" else "zh_CN"

        result = {}
        for key, locale_dict in raw_data.items():
            if not isinstance(locale_dict, dict):
                continue
            # 优先使用当前 locale，其次 fallback locale，最后第一个可用 locale
            value = locale_dict.get(primary_locale)
            if value is None:
                value = locale_dict.get(fallback_locale)
            if value is None:
                for loc, v in locale_dict.items():
                    value = v
                    break
            if value is not None:
                result[key] = value

        return result


def build_matcher(node: Any):
    """构建 matcher，保持与原始一致"""
    if node is None:
        return None

    if isinstance(node, LangNode):
        # 优先使用 properties
        if node.pattern:
            try:
                return re.compile(node.pattern)
            except Exception:
                return None
        if node.string:
            return node.string
        if node.terms:
            return node.terms
        return node  # 返回 LangNode 本身作为 fallback

    if isinstance(node, dict):
        if node.get("pattern"):
            try:
                return re.compile(node.get("pattern"))
            except Exception:
                return None
        if node.get("string"):
            return node.get("string")
        if node.get("terms"):
            return node.get("terms")

    if isinstance(node, str):
        return node

    return None


def _locale_from_obj(obj: Any) -> str | None:
    """从执行器/对象中提取 locale 字符串，提取失败时返回 None。"""
    executor = getattr(obj, "executor", None)
    locale_obj = getattr(executor, "locale", None) if executor is not None else getattr(obj, "locale", None)
    if locale_obj is None:
        return None
    if isinstance(locale_obj, Enum):
        return str(locale_obj.value)
    name_attr = getattr(locale_obj, "name", None)
    if name_attr is not None:
        value = name_attr() if callable(name_attr) else name_attr
        if value:
            return str(value)
    return str(locale_obj)


def get_lang_accessor(obj_or_locale: Any = None) -> LangAccessor:
    """Get a language accessor for the specified locale or object's locale attribute."""
    locale = None
    if isinstance(obj_or_locale, str):
        locale = obj_or_locale
    elif obj_or_locale is not None:
        try:
            locale = _locale_from_obj(obj_or_locale)
        except Exception:
            locale = None

    return LangAccessor(locale)


def _resolve_module_value(data: dict, item: str, fallback):
    """从语言模块数据中解析单个键值，dict 节点走 build_matcher 转换。"""
    value = data.get(item)
    if value is None:
        return fallback
    if isinstance(value, dict):
        localized = build_matcher(LangNode(value))
        return fallback if localized is None else localized
    return value


def get_lang_module_value(lang_accessor: Any, module_name: str, item: str, fallback=None):
    """Read a localized value from self.lang.<module_name> with fallback.

    The returned value keeps the existing semantics of LangModule/LangNode:
    - dict nodes are converted through build_matcher(LangNode(...))
    - missing values fall back to the provided fallback
    - non-dict values are returned as-is
    """
    if lang_accessor is None:
        return fallback

    try:
        module = getattr(lang_accessor, module_name)
        data = getattr(module, "_data", {})
        if not isinstance(data, dict):
            return fallback
        return _resolve_module_value(data, item, fallback)
    except Exception:
        return fallback


__all__ = [
    "ACTIVE_LOCALES_CONFIG",
    "SUPPORTED_LOCALES",
    "LangAccessor",
    "LangModule",
    "LangNode",
    "LocaleCode",
    "build_matcher",
    "get_lang_accessor",
    "get_lang_module_value",
    "get_supported_locales",
]
