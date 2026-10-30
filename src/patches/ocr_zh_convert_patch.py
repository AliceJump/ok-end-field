"""OCR 简繁归一的匹配层补丁。

背景
----
``executor.locale`` 只来自 GUI 界面语言（``configs/ui_config.json`` 的
``MainWindow.Language``），与游戏内界面语言无关。GUI 简体、游戏繁体时任务会选中
简体 pattern，于是 ``丰碑`` 匹配不到 ``豐碑留名·魔林``、``触碰`` ≠ ``觸碰``、
``苦难`` ≠ ``苦難``，大片 OCR 静默失配——OCR 本身置信度很高，纯粹是简繁字形不同。

ok-ww 的做法是在 ``config.py`` 打开 ``'ocr': {'auto_simplify': True}``，由 ok-script
``BaseTask.fix_texts`` 用 ``OpenCC('jp2t')`` + ``OpenCC('t2s')`` **改写 OCR 输出文本**。
本仓库不采用改写输出的方式：``Box.name`` 被大量业务代码直接读取，改写会改变现有
行为。这里保留同一条 OpenCC 转换链（见 ``src.core.ocr_zh_convert``），把 hook 放在
**匹配环节**：比对前把 pattern 与 OCR 文本都归一成简体，返回给业务代码的仍是原始
``Box`` 对象。

安全保证
--------
- 只包装 ``ok.feature.Box.find_boxes_by_name`` / ``find_box_by_name`` 以及
  ``ok.task.task`` 中的同名引用；参数与返回值语义不变。
- 归一后的文本放在匹配用替身里，原始 ``Box`` 不被复制、不被改写。
- **归一必须两侧同时成功**：任一侧转换失败（``CONVERSION_FAILED``）就整体退回
  原始参数，绝不出现「pattern 已归一、文本没归一」这种反而更容易失配的组合。
- 传入的 ``boxes`` 若是迭代器，先物化一次，快路径与异常回退都用这一份，
  不会把已耗尽的迭代器交回框架。
- 归一不可用（未安装 OpenCC）或任何异常都退回框架原实现。
- ``_PATCH_INSTALLED`` 保证幂等，重复 install 不会重复包装。
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from src.core.ocr_zh_convert import CONVERSION_FAILED, convert_matcher, convert_text

_PATCH_INSTALLED = False


class _NormalizedBox:
    """匹配用替身：只暴露归一后的 ``name``，不携带任何业务字段。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _materialize(boxes: Any) -> list[Any] | tuple[Any, ...] | None:
    """把 boxes 统一成可重复迭代的容器；无法处理时返回 ``None``。"""
    if boxes is None:
        return None
    if isinstance(boxes, (list, tuple)):
        return boxes
    try:
        return list(boxes)
    except TypeError:
        return None


def _normalize_boxes(boxes: Any) -> tuple[list[Any], dict[int, Any], bool] | None:
    """归一每个 box 的 ``name``。

    Returns:
        ``(匹配用列表, {替身id: 原box}, 是否有改动)``；任一名称转换失败返回 ``None``，
        调用方必须整体回退。
    """
    normalized: list[Any] = []
    originals: dict[int, Any] = {}
    changed = False
    for box in boxes:
        name = getattr(box, "name", None)
        if isinstance(name, str) and name:
            converted = convert_text(name)
            if converted is None:
                return None
            if converted != name:
                shim = _NormalizedBox(converted)
                normalized.append(shim)
                originals[id(shim)] = box
                changed = True
                continue
        normalized.append(box)
        originals[id(box)] = box
    return normalized, originals, changed


def _prepare(boxes: Any, match: Any) -> tuple[list[Any], Any, dict[int, Any]] | None:
    """构造归一后的匹配参数；无需归一或无法安全归一返回 ``None``。"""
    if not boxes:
        return None

    converted_match = convert_matcher(match)
    if converted_match is CONVERSION_FAILED:
        return None

    normalized_boxes = _normalize_boxes(boxes)
    if normalized_boxes is None:
        return None

    normalized, originals, changed = normalized_boxes
    if not changed and converted_match == match:
        return None
    return normalized, converted_match, originals


def _make_find_boxes_by_name(original):
    @wraps(original)
    def find_boxes_by_name(boxes, names):
        materialized = _materialize(boxes)
        if materialized is None:
            return original(boxes, names)
        try:
            prepared = _prepare(materialized, names)
            if prepared is None:
                return original(materialized, names)
            normalized, converted_names, originals = prepared
            matched = original(normalized, converted_names)
        except Exception:
            return original(materialized, names)
        return [originals.get(id(box), box) for box in matched]

    find_boxes_by_name._ocr_zh_convert_patched = True
    return find_boxes_by_name


def _make_find_box_by_name(original):
    @wraps(original)
    def find_box_by_name(boxes, names):
        materialized = _materialize(boxes)
        if materialized is None:
            return original(boxes, names)
        try:
            prepared = _prepare(materialized, names)
            if prepared is None:
                return original(materialized, names)
            normalized, converted_names, originals = prepared
            matched = original(normalized, converted_names)
        except Exception:
            return original(materialized, names)
        return originals.get(id(matched), matched) if matched is not None else None

    find_box_by_name._ocr_zh_convert_patched = True
    return find_box_by_name


def install_ocr_zh_convert_patch():
    """安装匹配层繁简归一补丁（幂等）。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import importlib

    box_module = importlib.import_module("ok.feature.Box")
    task_module = importlib.import_module("ok.task.task")

    find_boxes_by_name = _make_find_boxes_by_name(box_module.find_boxes_by_name)
    find_box_by_name = _make_find_box_by_name(box_module.find_box_by_name)

    box_module.find_boxes_by_name = find_boxes_by_name
    box_module.find_box_by_name = find_box_by_name
    # ok.task.task 在导入时已把这两个函数绑进自己的命名空间，必须同步替换，
    # 否则 OCR.ocr 与 BaseTask.find_boxes 仍走未归一的老实现。
    task_module.find_boxes_by_name = find_boxes_by_name
    task_module.find_box_by_name = find_box_by_name

    _PATCH_INSTALLED = True
