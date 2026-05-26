import json
import re
from pathlib import Path
from typing import Any


def _normalize_locale(locale: str | None) -> str:
    if not locale:
        return "zh_CN"
    locale = str(locale).replace("-", "_")
    parts = locale.split("_")
    if len(parts) == 2:
        return parts[0].lower() + "_" + parts[1].upper()
    return locale


class LangNode:
    def __init__(self, data: dict | None):
        self._data = data or {}

    def __getattr__(self, item: str):
        v = self._data.get(item)
        if isinstance(v, dict):
            return LangNode(v)
        return v

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
        if isinstance(v, dict):
            return LangNode(v)
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
        # 优先在 repo_root/lang/{module}/{locale}.json 查找
        lang_root = self._repo_root / "lang"
        locales_to_try = [self.locale, "en_US"]
        for loc in locales_to_try:
            p = lang_root / module_name / f"{loc}.json"
            if p.exists():
                try:
                    return json.load(p.open(encoding="utf-8"))
                except Exception:
                    pass

        # 未找到时返回空 dict
        return {}


def get_lang_accessor(obj_or_locale: Any = None) -> LangAccessor:
    # 接受 task 对象或 locale 字符串
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


def build_matcher(node: Any):
    if node is None:
        return None

    # 支持 LangNode
    if isinstance(node, LangNode):
        if node.pattern:
            try:
                return re.compile(node.pattern)
            except Exception:
                return None
        if node.string:
            return node.string
        if node.terms:
            return node.terms

    # 支持 dict/raw
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
