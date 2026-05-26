# -*- coding: utf-8 -*-
import unittest

from src.data.lang.lang_helpers import (
    build_pattern,
    collect_alias_terms,
    get_alias_mapped_value,
    get_locale_chain,
    merge_locale_data,
    normalize_locale,
    resolve_alias,
)


class TestLangHelpers(unittest.TestCase):
    def test_normalize_locale(self):
        self.assertEqual(normalize_locale("zh-CN"), "zh_cn")
        self.assertEqual(normalize_locale("ZH_CN"), "zh_cn")
        self.assertEqual(normalize_locale(" zh_TW "), "zh_tw")

    def test_resolve_alias(self):
        aliases = {"ocr_text_005": ["central_ring_hall"]}
        self.assertEqual(resolve_alias("ocr_text_005", aliases), "central_ring_hall")
        self.assertEqual(resolve_alias("central_ring_hall", aliases), "central_ring_hall")

    def test_collect_alias_terms(self):
        terms_map = {
            "ocr_text_005": ["中央环厅"],
            "central_ring_hall": ["中央環廳", "Central Ring Hall"],
        }
        aliases = {"ocr_text_005": ["central_ring_hall"]}
        terms = collect_alias_terms("ocr_text_005", terms_map, aliases)
        self.assertIn("中央环厅", terms)
        self.assertIn("中央環廳", terms)
        self.assertIn("Central Ring Hall", terms)

    def test_get_alias_mapped_value(self):
        mapping = {"time_remaining": "(\\d+)(天|小时)"}
        aliases = {"ocr_regex_003": ["time_remaining"]}
        self.assertEqual(get_alias_mapped_value("ocr_regex_003", mapping, aliases), "(\\d+)(天|小时)")

    def test_build_pattern(self):
        pattern = build_pattern(["中央环厅", "Central Ring Hall"])
        self.assertIsNotNone(pattern.search("Central Ring Hall"))
        self.assertIsNotNone(pattern.search("中央环厅"))
        self.assertIsNone(build_pattern([]).search("anything"))

    def test_merge_locale_data_and_chain(self):
        merged = merge_locale_data([
            {"ocr": {"terms": {"a": ["1"]}}},
            {"ocr": {"terms": {"b": ["2"]}}, "parser": {"sequence": {"delimiters": ["，"]}}},
        ])
        self.assertIn("a", merged["ocr"]["terms"])
        self.assertIn("b", merged["ocr"]["terms"])
        self.assertEqual(get_locale_chain("zh_TW", "zh_CN", ["zh_CN", "zh_TW"]), ["zh_TW", "zh_CN"])


if __name__ == "__main__":
    unittest.main()
