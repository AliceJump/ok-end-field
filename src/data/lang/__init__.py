import json
import re
from enum import Enum
from pathlib import Path
from typing import Any


def _discover_supported_locales() -> tuple[str, ...]:
    repo_root = Path(__file__).resolve().parents[3]
    i18n_root = repo_root / "i18n"
    locales: list[str] = []

    if i18n_root.exists():
        for child in sorted(i18n_root.iterdir(), key=lambda p: p.name):
            if child.is_dir() and (child / "LC_MESSAGES").exists():
                locales.append(child.name)

    if not locales:
        locales = ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"]

    return tuple(locales)


SUPPORTED_LOCALES = _discover_supported_locales()
LocaleCode = Enum("LocaleCode", {name: name for name in SUPPORTED_LOCALES}, type=str)


def get_supported_locales() -> tuple[str, ...]:
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
        return candidate
    return locale


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
        if hasattr(m, 'pattern'):
            return m.pattern
        return str(m)

    def __repr__(self) -> str:
        return self.__str__()

    @property
    def string(self) -> str | None:
        return self._data.get("string")

    @property
    def pattern(self) -> str | None:
        return self._data.get("pattern")

    @property
    def terms(self) -> list | None:
        return self._data.get("terms")


class LangModule:
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


class LangAccessor:
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
        locales_to_try = [self.locale]
        for supported_locale in SUPPORTED_LOCALES:
            if supported_locale not in locales_to_try:
                locales_to_try.append(supported_locale)

        for loc in locales_to_try:
            p = lang_root / module_name / f"{loc}.json"
            if p.exists():
                try:
                    return json.load(p.open(encoding="utf-8"))
                except Exception:
                    pass
        return {}


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


def get_lang_accessor(obj_or_locale: Any = None) -> LangAccessor:
    locale = None
    if isinstance(obj_or_locale, str):
        locale = obj_or_locale
    elif obj_or_locale is not None:
        try:
            executor = getattr(obj_or_locale, "executor", None)
            locale_obj = (
                getattr(executor, "locale", None)
                if executor is not None
                else getattr(obj_or_locale, "locale", None)
            )
            if locale_obj is not None:
                if isinstance(locale_obj, Enum):
                    locale = str(locale_obj.value)
                    return LangAccessor(locale)
                if hasattr(locale_obj, "name"):
                    name_attr = getattr(locale_obj, "name")
                    value = name_attr() if callable(name_attr) else name_attr
                    if value:
                        locale = str(value)
                else:
                    locale = str(locale_obj)
        except Exception:
            locale = None

    return LangAccessor(locale)


__all__ = [
    "LangAccessor",
    "LangModule",
    "LangNode",
    "LocaleCode",
    "SUPPORTED_LOCALES",
    "build_matcher",
    "get_lang_accessor",
    "get_supported_locales",
]