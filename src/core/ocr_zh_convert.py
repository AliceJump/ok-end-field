"""OCR 繁体中文到简体中文的归一化。

ok-ww（ok-wuthering-waves）没有自己实现繁简转换：它在 ``config.py`` 里打开
``'ocr': {'auto_simplify': True}``，由 ok-script ``BaseTask.fix_texts`` 调用
``OpenCC('jp2t')`` + ``OpenCC('t2s')`` 把 OCR 结果改写成简体后再参与匹配。本模块
搬运同一条转换链，但只暴露纯函数，由 ``src.patches.ocr_zh_convert_patch`` 在匹配层
调用，不直接改写 OCR 输出文本。

与 ok-script 2.0.7b1 ``ok/task/task.py::BaseTask.fix_texts`` 保持一致：

1. ``jp2t``：日文新字体 → 繁体（ok-ww 用于兼容日文字形）。
2. ``t2s``：繁体 → 简体。

转换不可用时区分两种情况，调用方必须据此决定是否回退：

- :func:`to_simplified`：宽松版，失败返回原文，供只需要「尽量归一」的场景；
- :func:`convert_text` / :func:`convert_pattern` / :func:`convert_matcher`：
  严格版，失败返回 :data:`CONVERSION_FAILED`。匹配层必须用严格版——只归一
  pattern 一侧而另一侧保持原文，会让本来能匹配的文本变成不匹配。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# 文本转换缓存上限：OCR 文本在同一界面反复出现，缓存能省掉重复转换开销。
_TEXT_CACHE_SIZE = 4096

#: 严格版的失败标记。调用方见到它必须整体回退到框架原行为。
CONVERSION_FAILED = object()

_CONVERTERS_UNLOADED = object()


class _ConversionError(Exception):
    """转换不可用或转换失败。"""


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
    _convert_cached.cache_clear()


@lru_cache(maxsize=_TEXT_CACHE_SIZE)
def _convert_cached(text: str) -> str:
    """返回转换结果；转换器不可用或转换抛异常时抛出 :class:`_ConversionError`。

    失败走异常而不是返回哨兵值，``lru_cache`` 只缓存成功结果——否则一次瞬时失败
    会被永久缓存，之后再也不会重试。
    """
    converters = _get_converters()
    if converters is None:
        raise _ConversionError("opencc unavailable")

    jp2t, t2s = converters
    try:
        if jp2t is not None:
            text = jp2t.convert(text)
        return t2s.convert(text)
    except Exception as exc:
        raise _ConversionError(str(exc)) from exc


def convert_text(text: str) -> str | None:
    """严格版：返回归一后的文本；转换不可用或失败时返回 ``None``。"""
    if not isinstance(text, str) or not text:
        return text
    try:
        return _convert_cached(text)
    except _ConversionError:
        return None


def to_simplified(text: str) -> str:
    """宽松版：返回归一后的文本；转换不可用或失败时返回原文。

    只用于「尽力归一」的场景。匹配层不能用它——单侧归一失败会让匹配结果变差，
    那里必须用 :func:`convert_text` 并整体回退。
    """
    if not isinstance(text, str):
        return text
    converted = convert_text(text)
    return text if converted is None else converted


def convert_pattern(pattern: re.Pattern) -> re.Pattern | Any:
    """归一正则 pattern 的字面文本，保留原有 flags。

    转换只影响 CJK 字符；ASCII 的转义、元字符、量词保持不变。转换不可用、失败或
    归一后无法编译时返回 :data:`CONVERSION_FAILED`。
    """
    raw = pattern.pattern
    converted = convert_text(raw)
    if converted is None:
        return CONVERSION_FAILED
    if converted == raw:
        return pattern
    try:
        return re.compile(converted, pattern.flags)
    except re.error:
        return CONVERSION_FAILED


def convert_matcher(matcher: Any) -> Any:
    """归一 ``match`` 参数：支持 ``str`` / ``re.Pattern`` / 列表与元组。

    任一元素转换失败即返回 :data:`CONVERSION_FAILED`，避免出现「部分归一」的
    匹配参数。无法识别的类型原样返回，保持框架原有语义。
    """
    if isinstance(matcher, str):
        converted = convert_text(matcher)
        return CONVERSION_FAILED if converted is None else converted

    if isinstance(matcher, re.Pattern):
        return convert_pattern(matcher)

    if isinstance(matcher, (list, tuple)):
        converted_items: list[Any] = []
        for item in matcher:
            converted = convert_matcher(item)
            if converted is CONVERSION_FAILED:
                return CONVERSION_FAILED
            converted_items.append(converted)
        if len(converted_items) == len(matcher) and all(
            new == old for new, old in zip(converted_items, matcher, strict=True)
        ):
            return matcher
        return converted_items

    return matcher
