import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import json5

from src.data import lang


class TestLangLoading(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.lang_root = self.folder / "assets" / "lang"
        self.lang_root.mkdir(parents=True)
        self.path = self.lang_root / "sample.json"

    def _accessor(self, locale="zh_CN"):
        accessor = lang.LangAccessor(locale)
        accessor._repo_root = self.folder
        return accessor

    def _write_json(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _load_counting_reads(self, text):
        self.path.write_text(text, encoding="utf-8")
        original_open = Path.open
        files = []

        def open_file(path, *args, **kwargs):
            self.assertEqual(path, self.path)
            self.assertEqual(kwargs.get("encoding"), "utf-8")
            file = original_open(path, *args, **kwargs)
            files.append(file)
            return file

        with patch.object(Path, "open", autospec=True, side_effect=open_file):
            result = self._accessor()._load_module("sample")
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].closed)
        return result

    def test_standard_json_uses_fast_path_and_keeps_node_values(self):
        data = {
            "name": {"zh_CN": {"string": "中文 / 😀"}, "zh_TW": {"string": "繁體"}},
            "pattern": {"zh_CN": {"pattern": r"^name\d+$"}},
            "terms": {"zh_CN": {"terms": ["alpha", "中文"]}},
            "scalar": {"zh_CN": 7},
            "nested": {"zh_CN": {"child": {"string": "value"}}},
        }
        text = json.dumps(data, ensure_ascii=False)
        expected = {key: value["zh_CN"] for key, value in json5.loads(text).items()}
        with patch.object(lang.json5, "loads", side_effect=AssertionError("unexpected JSON5 parse")) as fallback:
            loaded = self._load_counting_reads(text)
        fallback.assert_not_called()
        self.assertEqual(loaded, expected)

        module = lang.LangModule(loaded)
        self.assertEqual(module.name, "中文 / 😀")
        self.assertIsInstance(module.pattern, re.Pattern)
        self.assertTrue(module.pattern.fullmatch("name123"))
        self.assertEqual(module.terms, ["alpha", "中文"])
        self.assertEqual(module.nested.child, "value")

    def test_json5_extensions_fall_back_using_the_same_single_read(self):
        texts = (
            "// comment\n{entry: {zh_CN: {string: 'single quoted',},},}",
            "{/* block */ entry: {zh_CN: {terms: [0x10, +2, .5, 1.,],},},}",
            "{entry: {zh_CN: {string: 'line\\\ncontinued'}}}",
        )
        for text in texts:
            with self.subTest(text=text):
                expected = json5.loads(text)["entry"]["zh_CN"]
                with patch.object(lang.json5, "loads", wraps=json5.loads) as fallback:
                    loaded = self._load_counting_reads(text)
                fallback.assert_called_once_with(text)
                self.assertEqual(loaded, {"entry": expected})

    def test_utf8_bom_keeps_existing_json5_behavior(self):
        text = '\ufeff{"entry": {"zh_CN": {"string": "value"}}}'
        expected = json5.loads(text)["entry"]["zh_CN"]
        with patch.object(lang.json5, "loads", wraps=json5.loads) as fallback:
            loaded = self._load_counting_reads(text)
        fallback.assert_called_once_with(text)
        self.assertEqual(loaded, {"entry": expected})

    def test_ordinary_unicode_escapes_and_duplicate_keys_match_json5(self):
        text = (
            r'{"entry":{"zh_CN":{"string":"discard"}},'
            r'"second":{"en_US":{"string":"first locale"},"ja_JP":{"string":"later locale"}},'
            r'"entry":{"zh_CN":{"string":"\u4e2d\u6587\n\t\/"}}}'
        )
        self.path.write_text(text, encoding="utf-8")
        old_data = json5.loads(text)
        with patch.object(lang.json5, "loads", side_effect=AssertionError("unexpected JSON5 parse")) as fallback:
            loaded = self._accessor()._load_module("sample")
        fallback.assert_not_called()
        self.assertEqual(list(loaded), list(old_data))
        self.assertEqual(loaded["entry"], old_data["entry"]["zh_CN"])
        self.assertEqual(loaded["second"], old_data["second"]["en_US"])

    def test_surrogate_escape_values_and_key_collisions_keep_json5_code_points(self):
        text = (
            r'{"\ud83d\ude00":{"zh_CN":{"pattern":"\ud83d\ude00"}},'
            '"😀":{"zh_CN":{"string":"literal key"}}}'
        )
        old_data = json5.loads(text)
        self.assertEqual(len(old_data), 2)
        self.assertEqual(len(json.loads(text)), 1, "fixture must expose the parsers' different key handling")
        with patch.object(lang.json5, "loads", wraps=json5.loads) as fallback:
            loaded = self._load_counting_reads(text)
        fallback.assert_called_once_with(text)
        self.assertEqual(loaded, {key: value["zh_CN"] for key, value in old_data.items()})
        surrogate_key = next(iter(old_data))
        self.assertEqual([ord(char) for char in surrogate_key], [0xD83D, 0xDE00])
        matcher = lang.LangModule(loaded).get(surrogate_key)
        self.assertEqual(matcher.pattern, surrogate_key)
        self.assertIsNone(matcher.fullmatch("😀"))

    def test_literal_line_and_paragraph_separators_keep_json5_failure(self):
        for separator in ("\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                text = '{"entry":{"zh_CN":{"string":"before' + separator + 'after"}}}'
                json.loads(text)  # Valid standard JSON that the old parser rejects.
                with self.assertRaises(ValueError):
                    json5.loads(text)
                with patch.object(lang.json5, "loads", wraps=json5.loads) as fallback:
                    self.assertEqual(self._load_counting_reads(text), {})
                fallback.assert_called_once_with(text)

    def test_escaped_line_and_paragraph_separators_still_use_standard_json(self):
        text = r'{"entry":{"zh_CN":{"string":"before\u2028middle\u2029after"}}}'
        expected = json5.loads(text)["entry"]["zh_CN"]
        with patch.object(lang.json5, "loads", side_effect=AssertionError("unexpected JSON5 parse")) as fallback:
            loaded = self._load_counting_reads(text)
        fallback.assert_not_called()
        self.assertEqual(loaded, {"entry": expected})

    def test_locale_normalization_and_fallback_order_are_unchanged(self):
        data = {
            "primary": {"zh_CN": {"string": "cn"}, "zh_TW": {"string": "tw"}},
            "fallback": {"en_US": {"string": "first"}, "zh_CN": {"string": "cn"}},
            "null_primary": {"zh_TW": None, "en_US": {"string": "later"}},
            "not_a_locale_mapping": "ignored",
        }
        self._write_json(data)
        for locale in ("zh_CN", "ZH-cn", "en_US", "unknown", None):
            with self.subTest(locale=locale):
                accessor = self._accessor(locale)
                self.assertEqual(accessor.locale, "zh_CN")
                self.assertEqual(accessor.sample.primary, "cn")
                self.assertEqual(accessor.sample.fallback, "cn")
                self.assertIsNone(accessor.sample.null_primary)
                self.assertIsNone(accessor.sample.not_a_locale_mapping)
        for locale in ("zh_TW", "ZH-tw", lang.LocaleCode.zh_TW):
            with self.subTest(locale=locale):
                accessor = self._accessor(locale)
                self.assertEqual(accessor.locale, "zh_TW")
                self.assertEqual(accessor.sample.primary, "tw")
                # The original algorithm picks the first item after a missing
                # primary/fallback; it does not search for zh_CN here.
                self.assertEqual(accessor.sample.fallback, "first")
                self.assertIsNone(accessor.sample.null_primary)

    def test_each_accessor_owns_its_cached_mutable_data(self):
        self._write_json({"entry": {"zh_CN": {"terms": ["original"]}}})
        first = self._accessor()
        second = self._accessor()
        first_module = first.sample
        second_module = second.sample
        self.assertIs(first.sample, first_module)
        self.assertIsNot(first_module, second_module)
        first_module.entry.append("local mutation")
        self.assertEqual(second_module.entry, ["original"])

        self._write_json({"entry": {"zh_CN": {"terms": ["new file"]}}})
        self.assertEqual(first.sample.entry, ["original", "local mutation"])
        self.assertEqual(second.sample.entry, ["original"])
        self.assertEqual(self._accessor().sample.entry, ["new file"])

    def test_invalid_json_keeps_existing_accessor_cache_but_not_a_global_failure_cache(self):
        self.path.write_text("{ broken", encoding="utf-8")
        accessor = self._accessor()
        cached_empty = accessor.sample
        self.assertEqual(cached_empty._data, {})
        self._write_json({"entry": {"zh_CN": {"string": "repaired"}}})
        self.assertIs(accessor.sample, cached_empty)
        self.assertIsNone(accessor.sample.entry)
        self.assertEqual(accessor._load_module("sample"), {"entry": {"string": "repaired"}})
        self.assertEqual(self._accessor().sample.entry, "repaired")
        self.assertIs(accessor.sample, cached_empty, "direct loading must not replace the existing attribute cache")

    def test_missing_file_does_not_block_a_new_accessor_after_creation(self):
        first = self._accessor()
        self.assertEqual(first.sample._data, {})
        self._write_json({"entry": {"zh_CN": {"string": "created"}}})
        self.assertEqual(first.sample._data, {})
        self.assertEqual(self._accessor().sample.entry, "created")

    def test_invalid_utf8_and_read_errors_keep_empty_result_without_direct_load_cache(self):
        self.path.write_bytes(b'\xff{"entry":{}}')
        accessor = self._accessor()
        self.assertEqual(accessor._load_module("sample"), {})
        self.assertNotIn("sample", accessor._cache)
        self._write_json({"entry": {"zh_CN": {"string": "valid"}}})
        with patch.object(Path, "open", side_effect=PermissionError("injected read failure")):
            self.assertEqual(accessor._load_module("sample"), {})
        self.assertEqual(accessor._load_module("sample"), {"entry": {"string": "valid"}})
        self.assertNotIn("sample", accessor._cache)

    def test_wrong_root_type_preserves_existing_shape_error_and_can_be_retried(self):
        self.path.write_text("[]", encoding="utf-8")
        accessor = self._accessor()
        with self.assertRaises(AttributeError):
            _ = accessor.sample
        self.assertNotIn("sample", accessor._cache)
        self._write_json({"entry": {"zh_CN": {"string": "valid"}}})
        self.assertEqual(accessor.sample.entry, "valid")


if __name__ == "__main__":
    unittest.main()
