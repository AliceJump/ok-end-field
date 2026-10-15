import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import polib

from scripts.i18n import sync_official_i18n_langs as sync
from scripts.i18n._lang_sync_common import sync_po_entries, write_json
from scripts.i18n.sync_official_i18n_langs import (
    guard_anchor_official_match,
    official_vals_multi,
    rank_keys_for_node,
)


class AnchorGuardTests(unittest.TestCase):
    def test_width_equivalents_match_without_accepting_derived_prefix(self):
        ranked = [("key", 1)]
        self.assertTrue(guard_anchor_official_match("储藏箱Ａ１", ranked, {"zh_CN": {"key": "储藏箱A1"}}))
        self.assertTrue(guard_anchor_official_match("储藏箱Ａ１", ranked, {"zh_CN": {"key": "协议空间·储藏箱A1"}}))
        self.assertTrue(guard_anchor_official_match("干员经验", ranked, {"zh_CN": {"key": "协议空间·干员经验"}}))
        self.assertFalse(guard_anchor_official_match("干员经验", ranked, {"zh_CN": {"key": "干员经验素材"}}))

    def test_same_chinese_selects_existing_context_regardless_of_key_order(self):
        texts = {
            "en_US": {"take": "Take", "collect": "Collect"},
            "ja_JP": {"take": "取る", "collect": "収集"},
        }
        node = {"en_US": {"pattern": "Take"}, "ja_JP": {"pattern": "取る"}}
        for order in ([("take", 1), ("collect", 1)], [("collect", 1), ("take", 1)]):
            ranked = rank_keys_for_node(order, texts, node)
            self.assertEqual(official_vals_multi(texts, ranked, node), {"en_US": "Take", "ja_JP": "取る"})

    def test_ambiguous_missing_translation_is_not_chosen_by_order(self):
        texts = {"en_US": {"a": "Go", "b": "Go"}, "ja_JP": {"a": "行く", "b": "移動"}}
        node = {"en_US": {"string": "Go"}}
        for order in ([("a", 1), ("b", 1)], [("b", 1), ("a", 1)]):
            ranked = rank_keys_for_node(order, texts, node)
            self.assertEqual(official_vals_multi(texts, ranked, node), {"en_US": "Go"})


class SemanticWriteTests(unittest.TestCase):
    def test_json_whitespace_and_key_order_do_not_cause_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lang.json"
            original = b'{ "b": 2, "a": {"string":"Activate"} }\n'
            path.write_bytes(original)
            os.utime(path, ns=(1000000000, 1000000000))
            before = path.stat().st_mtime_ns
            write_json(path, {"a": {"string": "Activate"}, "b": 2})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(path.stat().st_mtime_ns, before)
            write_json(path, {"a": {"string": "Changed"}, "b": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["a"]["string"], "Changed")

    def make_catalog(self, root, fuzzy=False):
        path = root / "en_US" / "LC_MESSAGES" / "ok.po"
        path.parent.mkdir(parents=True)
        original = (
            '# Keep this header exactly\nmsgid ""\nmsgstr ""\n'
            '"Language: en_US\\n"\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            "#. Keep comment and occurrence layout\n#: source.py:12 other.py:23\n"
            + ("#, fuzzy\n" if fuzzy else "")
            + 'msgid "取消"\nmsgstr "Cancel"\n\n'
            '# Keep an intentionally split untranslated field\nmsgctxt "menu"\n'
            'msgid "前往"\nmsgstr ""\n"Go "\n"there"\n'
        ).encode("utf-8")
        path.write_bytes(original)
        path.with_suffix(".mo").write_bytes(polib.pofile(str(path)).to_binary())
        for file in (path, path.with_suffix(".mo")):
            os.utime(file, ns=(1000000000, 1000000000))
        return path, original

    def test_json_translation_change_preserves_unrelated_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lang.json"
            original = '{\n "node": {"en_US":{"pattern":"Old"}}\n,\n "list": ["Keep", "Keep"]\n}\n'
            path.write_bytes(original.encode("utf-8"))
            write_json(path, {"list": ["Keep", "Keep"], "node": {"en_US": {"pattern": "New"}}})
            self.assertEqual(path.read_bytes(), original.replace('"Old"', '"New"').encode("utf-8"))

    def test_po_noop_and_dry_run_preserve_bytes_and_mtimes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, original = self.make_catalog(root)
            mo = path.with_suffix(".mo")
            mo_before = mo.read_bytes()
            for value, dry in (("Cancel", False), ("Changed", True)):
                sync_po_entries({"取消": {"en_US": value}}, ("en_US",), root, dry_run=dry)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(mo.read_bytes(), mo_before)
                self.assertEqual(path.stat().st_mtime_ns, 1000000000)
                self.assertEqual(mo.stat().st_mtime_ns, 1000000000)

    def test_po_change_only_replaces_target_translation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, original = self.make_catalog(root)
            sync_po_entries({"取消": {"en_US": "Changed"}}, ("en_US",), root)
            self.assertEqual(path.read_bytes(), original.replace(b'msgstr "Cancel"', b'msgstr "Changed"'))
            self.assertEqual(polib.mofile(str(path.with_suffix(".mo"))).find("取消").msgstr, "Changed")

    def test_uncompiled_fuzzy_translation_does_not_rewrite_mo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, original = self.make_catalog(root, fuzzy=True)
            mo = path.with_suffix(".mo")
            mo_before = mo.read_bytes()
            sync_po_entries({"取消": {"en_US": "Changed"}}, ("en_US",), root)
            self.assertEqual(path.read_bytes(), original.replace(b'msgstr "Cancel"', b'msgstr "Changed"'))
            self.assertEqual(mo.read_bytes(), mo_before)
            self.assertEqual(mo.stat().st_mtime_ns, 1000000000)

    def test_po_append_preserves_existing_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, original = self.make_catalog(root)
            sync_po_entries({"新条目": {"en_US": "New entry"}}, ("en_US",), root)
            self.assertTrue(path.read_bytes().startswith(original))
            self.assertEqual(polib.mofile(str(path.with_suffix(".mo"))).find("新条目").msgstr, "New entry")


class FullSyncTests(unittest.TestCase):
    def test_valid_stage_suffix_is_preserved_while_wrong_and_display_text_are_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official = root / "official"
            official.mkdir()
            tables = {
                "CN": {"area": "北部禁区", "stage": "能量淤积点·北部禁区", "other": "其他·南部禁区"},
                "EN": {
                    "area": "North Wuling Exclusion Zone",
                    "stage": "Energy Alluvium: North Wuling EZ",
                    "other": "Other: Wrong suffix",
                },
            }
            for code, table in tables.items():
                (official / f"{code}.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
            lang_dir = root / "lang"
            lang_dir.mkdir()
            data = {
                "stage": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "North Wuling EZ"}},
                "wrong": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "Wrong suffix"}},
                "short": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "Z"}},
                "end_word": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "EZ"}},
                "display": {"zh_CN": {"string": "北部禁区"}, "en_US": {"string": "North Wuling EZ"}},
            }
            path = lang_dir / "test.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with (
                patch.object(sync, "LANG_DIR", lang_dir),
                patch.object(sync, "PO_DIR", root / "i18n"),
                patch.object(sync, "OFFICIAL_TO_REPO", {"CN": "zh_CN", "EN": "en_US"}),
                patch.object(sync, "REPO_LANGS", ("zh_CN", "en_US")),
                patch.object(sync, "PO_LANGS", ("en_US",)),
                patch("sys.argv", ["sync", "--i18n-dir", str(official)]),
            ):
                self.assertEqual(sync.main(), 0)
                result = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(result["stage"]["en_US"]["pattern"], "North Wuling EZ")
                self.assertEqual(result["wrong"]["en_US"]["pattern"], "North Wuling Exclusion Zone")
                self.assertEqual(result["short"]["en_US"]["pattern"], "North Wuling Exclusion Zone")
                self.assertEqual(result["end_word"]["en_US"]["pattern"], "North Wuling Exclusion Zone")
                self.assertEqual(result["display"]["en_US"]["string"], "North Wuling Exclusion Zone")
                before = path.read_bytes(), path.stat().st_mtime_ns
                self.assertEqual(sync.main(), 0)
                actual = path.read_bytes(), path.stat().st_mtime_ns
                self.assertEqual(actual, before)

    def test_ui_uses_its_own_context_and_repeated_sync_does_not_write(self):
        for ui_values in (("Collect", "収集"), ("Custom GUI text", "取る")):
            with self.subTest(ui_values=ui_values), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                official = root / "official"
                official.mkdir()
                lang_dir = root / "lang"
                lang_dir.mkdir()
                tables = {
                    "CN": {"collect": "收取", "take": "收取"},
                    "EN": {"collect": "Collect", "take": "Take"},
                    "JP": {"collect": "収集", "take": "取る"},
                }
                for code, table in tables.items():
                    (official / f"{code}.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
                matcher = {
                    "collect": {
                        "zh_CN": {"pattern": "收取"},
                        "en_US": {"pattern": "Take"},
                        "ja_JP": {"pattern": "取る"},
                    }
                }
                lang_path = lang_dir / "test.json"
                lang_path.write_text(json.dumps(matcher, ensure_ascii=False), encoding="utf-8")
                po_dir = root / "i18n"
                for locale, value in zip(("en_US", "ja_JP"), ui_values, strict=True):
                    catalog = polib.POFile()
                    catalog.metadata = {"Language": locale, "Content-Type": "text/plain; charset=UTF-8"}
                    catalog.append(polib.POEntry(msgid="收取", msgstr=value))
                    path = po_dir / locale / "LC_MESSAGES" / "ok.po"
                    path.parent.mkdir(parents=True)
                    catalog.save(str(path), newline="\n")
                    catalog.save_as_mofile(str(path.with_suffix(".mo")))
                targets = [lang_path, *po_dir.glob("*/LC_MESSAGES/ok.*")]
                for path in targets:
                    os.utime(path, ns=(1000000000, 1000000000))
                before = {path: path.read_bytes() for path in targets}
                with (
                    patch.object(sync, "LANG_DIR", lang_dir),
                    patch.object(sync, "PO_DIR", po_dir),
                    patch.object(sync, "OFFICIAL_TO_REPO", {"CN": "zh_CN", "EN": "en_US", "JP": "ja_JP"}),
                    patch.object(sync, "REPO_LANGS", ("zh_CN", "en_US", "ja_JP")),
                    patch.object(sync, "PO_LANGS", ("en_US", "ja_JP")),
                    patch("sys.argv", ["sync", "--i18n-dir", str(official)]),
                ):
                    for _ in range(2):
                        self.assertEqual(sync.main(), 0)
                        for path in targets:
                            self.assertEqual(path.read_bytes(), before[path])
                            self.assertEqual(path.stat().st_mtime_ns, 1000000000)


if __name__ == "__main__":
    unittest.main()
