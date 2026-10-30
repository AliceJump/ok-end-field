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
- 归一不可用（未安装 OpenCC）或任何异常都退回框架原实现。
- ``_PATCH_INSTALLED`` 保证幂等，重复 install 不会重复包装。
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from src.core.ocr_zh_convert import convert_matcher, to_simplified

_PATCH_INSTALLED = False


class _NormalizedBox:
    """匹配用替身：只暴露归一后的 ``name``，不携带任何业务字段。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _normalize_boxes(boxes: list[Any]) -> tuple[list[Any], dict[int, Any], bool]:
    """归一每个 box 的 ``name``，返回 (匹配用列表, {替身id: 原box}, 是否有改动)。"""
    normalized: list[Any] = []
    originals: dict[int, Any] = {}
    changed = False
    for box in boxes:
        name = getattr(box, "name", None)
        if isinstance(name, str) and name:
            converted = to_simplified(name)
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
    """构造归一后的匹配参数；无需归一或无法处理时返回 ``None``。"""
    if boxes is None:
        return None
    if not isinstance(boxes, (list, tuple)):
        # 迭代器只能消费一次，先物化，避免快路径把已耗尽的迭代器交回原实现。
        boxes = list(boxes)
    if not boxes:
        return None

    converted_match = convert_matcher(match)
    normalized, originals, changed = _normalize_boxes(boxes)
    if not changed and converted_match == match:
        return None
    return normalized, converted_match, originals


def _make_find_boxes_by_name(original):
    @wraps(original)
    def find_boxes_by_name(boxes, names):
        try:
            prepared = _prepare(boxes, names)
            if prepared is None:
                return original(boxes, names)
            normalized, converted_names, originals = prepared
            matched = original(normalized, converted_names)
            return [originals.get(id(box), box) for box in matched]
        except Exception:
            return original(boxes, names)

    find_boxes_by_name._ocr_zh_convert_patched = True
    return find_boxes_by_name


def _make_find_box_by_name(original):
    @wraps(original)
    def find_box_by_name(boxes, names):
        try:
            prepared = _prepare(boxes, names)
            if prepared is None:
                return original(boxes, names)
            normalized, converted_names, originals = prepared
            matched = original(normalized, converted_names)
            return originals.get(id(matched), matched) if matched is not None else None
        except Exception:
            return original(boxes, names)

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
