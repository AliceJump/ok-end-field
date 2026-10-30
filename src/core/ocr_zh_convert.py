"""OCR 繁体中文到简体中文的归一化。

ok-ww（ok-wuthering-waves）没有自己实现繁简转换：它在 ``config.py`` 里打开
``'ocr': {'auto_simplify': True}``，由 ok-script ``BaseTask.fix_texts`` 调用
``OpenCC('jp2t')`` + ``OpenCC('t2s')`` 把 OCR 结果改写成简体后再参与匹配。本模块
搬运同一条转换链，但只暴露纯函数，由 ``src.patches.ocr_zh_convert_patch`` 在匹配层
调用，不直接改写 OCR 输出文本。

与 ok-script 2.0.7b1 ``ok/task/task.py::BaseTask.fix_texts`` 保持一致：

1. ``jp2t``：日文新字体 → 繁体（ok-ww 用于兼容日文字形）。
2. ``t2s``：繁体 → 简体。

任何一步不可用（未安装 OpenCC、配置缺失）或转换抛异常时都返回原文，调用方可安全
按原行为继续匹配。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# 文本转换缓存上限：OCR 文本在同一界面反复出现，缓存能省掉重复转换开销。
_TEXT_CACHE_SIZE = 4096

_CONVERTERS_UNLOADED = object()

_converters: Any = _CONVERTERS_UNLOADED


def _load_converters() -> tuple[Any, Any] | None:
    """构建 ``(jp2t, t2s)`` 转换器；OpenCC 不可用时返回 ``None``。"""
    try:
        from opencc import OpenCC
    except Exception:
        return None

    try:
        t2s = OpenCC("t2s")
    except Exception:
        return None

    try:
        jp2t = OpenCC("jp2t")
    except Exception:
        # 老版本 OpenCC 没有 jp2t 配置：退化为只做繁转简，不影响主流程。
        jp2t = None
    return jp2t, t2s


def _get_converters() -> tuple[Any, Any] | None:
    """惰性获取转换器（只初始化一次）。"""
    global _converters
    if _converters is _CONVERTERS_UNLOADED:
        _converters = _load_converters()
    return _converters


def reload_converters() -> None:
    """丢弃已缓存的转换器与文本缓存（测试用）。"""
    global _converters
    _converters = _CONVERTERS_UNLOADED
    to_simplified.cache_clear()


@lru_cache(maxsize=_TEXT_CACHE_SIZE)
def to_simplified(text: str) -> str:
    """把繁体（含日文新字体）文本归一成简体。

    与 ok-ww 的转换链一致：先 ``jp2t``，再 ``t2s``。转换器不可用、输入不是字符串
    或转换抛异常时原样返回，保证补丁不会破坏原有匹配。
    """
    if not isinstance(text, str) or not text:
        return text

    converters = _get_converters()
    if converters is None:
        return text

    jp2t, t2s = converters
    original = text
    try:
        if jp2t is not None:
            text = jp2t.convert(text)
        return t2s.convert(text)
    except Exception:
        return original


def convert_pattern(pattern: re.Pattern) -> re.Pattern:
    """把正则 pattern 的字面文本归一成简体，保留原有 flags。

    转换只影响 CJK 字符；ASCII 的转义、元字符、量词保持不变。归一后无法编译时
    返回原 pattern。
    """
    raw = pattern.pattern
    converted = to_simplified(raw)
    if converted == raw:
        return pattern
    try:
        return re.compile(converted, pattern.flags)
    except re.error:
        return pattern


def convert_matcher(matcher: Any) -> Any:
    """归一 ``match`` 参数：支持 ``str`` / ``re.Pattern`` / 列表与元组。

    无法识别的类型原样返回，保持框架原有语义。
    """
    if isinstance(matcher, str):
        return to_simplified(matcher)

    if isinstance(matcher, re.Pattern):
        return convert_pattern(matcher)

    if isinstance(matcher, (list, tuple)):
        converted = [convert_matcher(item) for item in matcher]
        if len(converted) == len(matcher) and all(new == old for new, old in zip(converted, matcher, strict=True)):
            return matcher
        return converted

    return matcher
