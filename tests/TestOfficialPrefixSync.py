import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.i18n import sync_official_i18n_langs as sync
from scripts.i18n._lang_sync_common import write_json
from scripts.i18n._official_prefix_sync import build_prefixed_nodes, load_prefix_overrides, sync_prefix_overrides
from src.data.world_map import stages_dict


class OfficialPrefixSyncTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "north": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "North Wuling EZ"}},
            "same_name": {"zh_CN": {"pattern": "北部禁区"}, "en_US": {"pattern": "North Wuling Exclusion Zone"}},
        }
        self.rules = {"north": ("能量淤积点", "重度能量淤积点")}
        self.tables = {
            "zh_CN": {
                "area": "北部禁区",
                "normal": "能量淤积点·北部禁区",
                "severe": "重度能量淤积点·北部禁区",
                "unrelated": "其他·南部禁区",
            },
            "en_US": {
                "area": "North Wuling Exclusion Zone",
                "normal": "Energy Alluvium: North Wuling EZ",
                "severe": "Severe Energy Alluvium: North Wuling EZ",
                "unrelated": "Energy Alluvium: North Wuling EZ",
            },
        }

    def test_valid_core_is_preserved_and_rule_is_limited_to_key(self):
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)
        self.data["north"]["en_US"]["pattern"] = "North Wuling Exclusion Zone"
        result = build_prefixed_nodes(self.tables, self.data, self.rules)
        self.assertEqual(result["north"]["en_US"]["pattern"], "North Wuling EZ")
        self.assertEqual(result["north"]["zh_CN"], self.data["north"]["zh_CN"])
        self.assertEqual(result["same_name"], self.data["same_name"])

    def test_wrong_chinese_anchor_is_never_replaced_by_identical_english(self):
        self.data["north"]["en_US"]["pattern"] = "Wrong"
        del self.tables["zh_CN"]["normal"]
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)

    def test_partial_core_or_prefix_match_is_replaced_by_complete_name(self):
        for pattern in ("Z", "EZ", "Energy", "energy", "north"):
            with self.subTest(pattern=pattern):
                self.data["north"]["en_US"]["pattern"] = pattern
                result = build_prefixed_nodes(self.tables, self.data, self.rules)
                self.assertEqual(result["north"]["en_US"]["pattern"], "North Wuling EZ")

    def test_case_variant_adds_matching_flag_without_rewriting_core_text(self):
        for key in ("normal", "severe"):
            self.tables["en_US"][key] = self.tables["en_US"][key].lower()
        result = build_prefixed_nodes(self.tables, self.data, self.rules)
        pattern = result["north"]["en_US"]["pattern"]
        self.assertEqual(pattern, "(?i)North Wuling EZ")
        for key in ("normal", "severe"):
            self.assertRegex(self.tables["en_US"][key], re.compile(pattern))
        self.assertRegex("Energy Alluvium: North Wuling EZ", re.compile(pattern))

    def test_missing_prefix_or_locale_keeps_existing_matcher(self):
        self.data["north"]["en_US"]["pattern"] = "Wrong"
        del self.tables["en_US"]["severe"]
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)
        del self.tables["zh_CN"]["severe"]
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)

    def test_ambiguous_core_and_custom_regex_are_not_overwritten(self):
        self.data["north"]["en_US"]["pattern"] = "Wrong"
        self.tables["zh_CN"]["variant"] = self.tables["zh_CN"]["normal"]
        self.tables["en_US"]["variant"] = "Energy Alluvium: Another name"
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)
        self.data["north"]["en_US"]["pattern"] = "(?i)Custom|Other"
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)

    def test_protocol_uses_chinese_complete_title_not_material_name(self):
        data = {"exp": {"zh_CN": {"pattern": "干员经验"}, "ja_JP": {"pattern": "経験値素材"}}}
        tables = {
            "zh_CN": {"stage": "协议空间·干员经验", "item": "干员经验素材"},
            "ja_JP": {"stage": "協約空間・経験値", "item": "経験値素材"},
            "ru_RU": {"stage": "Протокольный разлом «Опыт оперативника»", "item": "Wrong"},
        }
        result = build_prefixed_nodes(tables, data, {"exp": ("协议空间",)})
        self.assertEqual(result["exp"]["ja_JP"]["pattern"], "経験値")
        self.assertRegex("Протокольный разлом «Опыт оперативника»", re.compile(result["exp"]["ru_RU"]["pattern"]))

    def test_unsupported_title_structure_preserves_current_value(self):
        self.tables["en_US"]["normal"] = "Unknown full title"
        self.data["north"]["en_US"]["pattern"] = "Wrong"
        self.assertEqual(build_prefixed_nodes(self.tables, self.data, self.rules), self.data)

    def test_sync_dry_run_and_repeat_preserve_bytes_and_mtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.data["north"]["en_US"]["pattern"] = "Wrong"
            path = root / "world_map.json"
            write_json(path, self.data)
            overrides = {"world_map": self.rules}
            before = path.read_bytes(), path.stat().st_mtime_ns
            self.assertTrue(sync_prefix_overrides(self.tables, root, overrides, True, write_json))
            actual = path.read_bytes(), path.stat().st_mtime_ns
            self.assertEqual(actual, before)
            self.assertTrue(sync_prefix_overrides(self.tables, root, overrides, False, write_json))
            before = path.read_bytes(), path.stat().st_mtime_ns
            self.assertEqual(sync_prefix_overrides(self.tables, root, overrides, False, write_json), [])
            actual = path.read_bytes(), path.stat().st_mtime_ns
            self.assertEqual(actual, before)
            self.assertEqual(list(root.iterdir()), [path])

    def test_config_keys_belong_to_declared_chinese_categories(self):
        root = Path.cwd()
        config = json.loads((root / "scripts/i18n/official_prefix_overrides.json").read_text(encoding="utf-8"))
        data = json.loads((root / "assets/lang/world_map.json").read_text(encoding="utf-8"))
        rules = load_prefix_overrides(root)["world_map"]
        self.assertEqual(len(rules), 25)
        self.assertEqual(rules["k_beibujinqu"], ("重度能量淤积点",))
        for category, spec in config["world_map"].items():
            for key in spec["keys"]:
                self.assertIn(data[key]["zh_CN"]["pattern"], stages_dict[category])
        self.assertNotIn("危境预演", config["world_map"])

    def test_full_sync_does_not_fall_back_to_standalone_name_for_configured_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official = root / "official"
            official.mkdir()
            lang_dir = root / "lang"
            lang_dir.mkdir()
            path = lang_dir / "world_map.json"
            write_json(path, self.data)
            for code, locale in (("CN", "zh_CN"), ("EN", "en_US")):
                write_json(official / f"{code}.json", self.tables[locale])
            before = path.read_bytes(), path.stat().st_mtime_ns
            with (
                patch.object(sync, "LANG_DIR", lang_dir),
                patch.object(sync, "PO_DIR", root / "i18n"),
                patch.object(sync, "load_prefix_overrides", return_value={"world_map": self.rules}),
                patch("sys.argv", ["sync", "--i18n-dir", str(official)]),
            ):
                self.assertEqual(sync.main(), 0)
                actual = path.read_bytes(), path.stat().st_mtime_ns
                self.assertEqual(actual, before)


if __name__ == "__main__":
    unittest.main()
