# -*- coding: utf-8 -*-
import unittest

from src.data.lang import (
    get_auto_pick_terms,
    get_locale_data,
    get_item_translation_map,
    get_ocr_confusion_map,
    normalize as lang_normalize,
    ocr as lang_ocr,
    parser as lang_parser,
    get_sequence_delimiters,
    get_warehouse_location_labels,
    resolve_supported_locale,
)
from src.tasks.sequence_parser import parse_sequence


class _MockLocale:
    def __init__(self, value):
        self._value = value

    def name(self):
        return self._value


class _MockExecutor:
    def __init__(self, locale_value):
        self.locale = _MockLocale(locale_value)


class _MockContext:
    def __init__(self, locale_value):
        self.executor = _MockExecutor(locale_value)


class TestLocaleData(unittest.TestCase):
    def test_resolve_supported_locale_with_runtime_context(self):
        ctx = _MockContext("zh_TW")
        self.assertEqual(resolve_supported_locale(context=ctx), "zh_TW")

    def test_get_ocr_confusion_map_by_locale(self):
        tw_map = get_ocr_confusion_map(locale="zh_TW")
        self.assertIn("別", tw_map)
        self.assertIn("别", tw_map)

    def test_sequence_parser_uses_locale_delimiters(self):
        delimiters = get_sequence_delimiters(locale="zh_CN")
        self.assertIn("，", delimiters)
        self.assertIn("，", lang_parser.get_sequence_delimiters(locale="zh_CN"))
        self.assertEqual(parse_sequence("a， b,c"), ["a", "b", "c"])

    def test_get_warehouse_location_labels(self):
        labels = get_warehouse_location_labels(locale="zh_TW")
        self.assertEqual(labels.get("valley4"), "四號谷地")

    def test_get_auto_pick_terms(self):
        white, black = get_auto_pick_terms(locale="zh_CN")
        self.assertIn("采集", white)
        self.assertIn("协议核心", black)

    def test_lang_ocr_namespace_terms(self):
        self.assertIn("仓储节点", lang_ocr.get_terms("storage_node", locale="zh_CN"))
        self.assertTrue(lang_ocr.get_pattern("storage_node", locale="zh_TW").search("倉儲節點"))

    def test_lang_normalize_namespace_tables(self):
        confusion = lang_normalize.get_ocr_confusion_map(locale="zh_CN")
        self.assertIn("别", confusion)
        punct_map, t2s_map = lang_normalize.get_text_normalize_tables(locale="zh_CN")
        self.assertEqual(punct_map.get("·"), "：")
        self.assertEqual(t2s_map.get("質"), "质")

    def test_get_item_translation_map(self):
        tw_map = get_item_translation_map(locale="zh_TW")
        self.assertEqual(tw_map.get("藍鐵礦"), "bluesteel_ore")

    def test_locale_payload_is_layered_without_legacy_flat_keys(self):
        payload = get_locale_data(locale="zh_CN")
        self.assertIn("normalize", payload)
        self.assertIn("parser", payload)
        self.assertNotIn("ocr_confusion_map", payload)
        self.assertNotIn("sequence_delimiters", payload)

    def test_locale_terms_are_script_specific(self):
        self.assertEqual(lang_ocr.get_terms("storage_node", locale="zh_CN"), ["仓储节点"])
        self.assertEqual(lang_ocr.get_terms("storage_node", locale="zh_TW"), ["倉儲節點"])


if __name__ == "__main__":
    unittest.main()
