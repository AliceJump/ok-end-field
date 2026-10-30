"""OCR 简繁归一匹配补丁的单元测试。

覆盖三层：

1. ``src.core.ocr_zh_convert``：繁体 → 简体转换、简体幂等、英文/数字/标点不被破坏。
2. ``src.patches.ocr_zh_convert_patch``：``re.Pattern`` 与 ``str`` 两种 match 都能命中，
   且返回的仍是原始 ``Box``（``box.name`` 不被改写）。
3. 安全特性：重复 install 幂等；OpenCC 不可用或内部异常时退回框架原行为。
"""

import importlib
import re
import unittest
from unittest.mock import patch

from ok.feature.Box import Box

from src.core import ocr_zh_convert
from src.patches import ocr_zh_convert_patch


def _box(name: str, y: int = 0) -> Box:
    return Box(10, y, 200, 40, 0.93, name)


class OcrZhConvertTextTestCase(unittest.TestCase):
    """纯转换函数。"""

    def test_traditional_text_converted(self):
        self.assertIn("丰碑", ocr_zh_convert.to_simplified("豐碑留名·魔林"))
        self.assertEqual(ocr_zh_convert.to_simplified("觸碰"), "触碰")
        self.assertEqual(ocr_zh_convert.to_simplified("苦難"), "苦难")
        self.assertEqual(ocr_zh_convert.to_simplified("活動期間"), "活动期间")
        self.assertEqual(ocr_zh_convert.to_simplified("確認"), "确认")

    def test_simplified_text_unchanged(self):
        for text in ("丰碑留名·魔林", "触碰", "苦难", "活动期间", "确认"):
            self.assertEqual(ocr_zh_convert.to_simplified(text), text, text)

    def test_english_digits_punctuation_preserved(self):
        self.assertEqual(ocr_zh_convert.to_simplified("ABC-123 4.5%"), "ABC-123 4.5%")
        self.assertEqual(ocr_zh_convert.to_simplified("觸碰 !?@#"), "触碰 !?@#")
        self.assertEqual(ocr_zh_convert.to_simplified("確認 4.5%"), "确认 4.5%")

    def test_empty_and_non_string_passthrough(self):
        self.assertEqual(ocr_zh_convert.to_simplified(""), "")
        self.assertIsNone(ocr_zh_convert.to_simplified(None))  # type: ignore[arg-type]

    def test_convert_pattern_keeps_flags_and_identity(self):
        converted = ocr_zh_convert.convert_pattern(re.compile("觸碰", re.IGNORECASE))
        self.assertEqual(converted.pattern, "触碰")
        self.assertTrue(converted.flags & re.IGNORECASE)

        untouched = re.compile("触碰")
        self.assertIs(ocr_zh_convert.convert_pattern(untouched), untouched)

    def test_convert_matcher_types(self):
        self.assertEqual(ocr_zh_convert.convert_matcher("確認"), "确认")

        pattern = re.compile("觸碰")
        self.assertEqual(ocr_zh_convert.convert_matcher(pattern).pattern, "触碰")

        converted = ocr_zh_convert.convert_matcher(["確認", re.compile("觸碰")])
        self.assertEqual(converted[0], "确认")
        self.assertEqual(converted[1].pattern, "触碰")

        # 无需改动时返回原对象，避免无必要的列表重建
        names = ["确认", re.compile("触碰")]
        self.assertIs(ocr_zh_convert.convert_matcher(names), names)

        # 不支持的类型原样返回
        other = object()
        self.assertIs(ocr_zh_convert.convert_matcher(other), other)


class OcrZhConvertPatchTestCase(unittest.TestCase):
    """匹配层补丁行为（安装一次，类结束后还原全局函数）。"""

    @classmethod
    def setUpClass(cls):
        cls._box_module = importlib.import_module("ok.feature.Box")
        cls._task_module = importlib.import_module("ok.task.task")
        cls._saved = {
            (cls._box_module, "find_boxes_by_name"): cls._box_module.find_boxes_by_name,
            (cls._box_module, "find_box_by_name"): cls._box_module.find_box_by_name,
            (cls._task_module, "find_boxes_by_name"): cls._task_module.find_boxes_by_name,
            (cls._task_module, "find_box_by_name"): cls._task_module.find_box_by_name,
        }
        cls._installed_flag = ocr_zh_convert_patch._PATCH_INSTALLED
        cls.addClassCleanup(cls._restore)
        ocr_zh_convert_patch._PATCH_INSTALLED = False
        ocr_zh_convert_patch.install_ocr_zh_convert_patch()

    @classmethod
    def _restore(cls):
        for (module, name), value in cls._saved.items():
            setattr(module, name, value)
        ocr_zh_convert_patch._PATCH_INSTALLED = cls._installed_flag

    def test_regex_simplified_pattern_matches_traditional_box(self):
        boxes = [_box("豐碑留名·魔林")]
        matched = self._box_module.find_boxes_by_name(boxes, re.compile("丰碑"))
        self.assertEqual(len(matched), 1)
        self.assertIs(matched[0], boxes[0])
        # 业务输出不被改写
        self.assertEqual(matched[0].name, "豐碑留名·魔林")

    def test_traditional_pattern_matches_traditional_box(self):
        boxes = [_box("活動期間")]
        self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("活動")), boxes)

    def test_string_match_uses_simplified_equality(self):
        boxes = [_box("確認")]
        matched = self._box_module.find_boxes_by_name(boxes, "确认")
        self.assertEqual(matched, boxes)
        self.assertEqual(matched[0].name, "確認")

    def test_match_list_and_no_match(self):
        boxes = [_box("觸碰"), _box("苦難", y=50)]
        self.assertEqual(len(self._box_module.find_boxes_by_name(boxes, [re.compile("触碰"), re.compile("苦难")])), 2)
        self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("丰碑")), [])

    def test_ascii_name_matching_unchanged(self):
        boxes = [_box("confirm_btn")]
        self.assertEqual(self._box_module.find_boxes_by_name(boxes, "confirm_btn"), boxes)
        self.assertEqual(self._box_module.find_boxes_by_name(boxes, "确认"), [])

    def test_task_module_binding_is_patched(self):
        # ok.task.task 在导入时绑定了同名函数，OCR.ocr / BaseTask.find_boxes 走这里
        self.assertIs(self._task_module.find_boxes_by_name, self._box_module.find_boxes_by_name)
        self.assertIs(self._task_module.find_box_by_name, self._box_module.find_box_by_name)
        self.assertTrue(getattr(self._box_module.find_boxes_by_name, "_ocr_zh_convert_patched", False))

    def test_find_box_by_name_returns_original_box(self):
        boxes = [_box("苦難")]
        matched = self._box_module.find_box_by_name(boxes, re.compile("苦难"))
        self.assertIs(matched, boxes[0])

    def test_install_is_idempotent(self):
        patched = self._box_module.find_boxes_by_name
        ocr_zh_convert_patch.install_ocr_zh_convert_patch()
        self.assertIs(self._box_module.find_boxes_by_name, patched)
        self.assertIs(self._task_module.find_boxes_by_name, patched)

    def test_fallback_when_conversion_unavailable(self):
        # OpenCC 不可用：补丁退化为原有精确匹配，不会误命中
        with patch.object(ocr_zh_convert, "_load_converters", return_value=None):
            ocr_zh_convert.reload_converters()
            try:
                boxes = [_box("豐碑留名·魔林")]
                self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("丰碑")), [])
                self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("豐碑")), boxes)
            finally:
                ocr_zh_convert.reload_converters()
        self.assertIn("丰碑", ocr_zh_convert.to_simplified("豐碑留名·魔林"))

    def test_fallback_when_patch_raises(self):
        # 补丁内部异常：直接退回框架原实现
        boxes = [_box("豐碑留名·魔林")]
        with patch.object(ocr_zh_convert_patch, "_prepare", side_effect=RuntimeError("boom")):
            self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("丰碑")), [])
            self.assertEqual(self._box_module.find_boxes_by_name(boxes, re.compile("豐碑")), boxes)


if __name__ == "__main__":
    unittest.main()
