"""build_loadout_data 装备基础属性与官方推荐配装解析测试。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "skill-data" / "build_loadout_data.py"
_spec = importlib.util.spec_from_file_location("build_loadout_data", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_BUILD_SCRIPT = _REPO / "scripts" / "skill-data" / "generate_character_builds.py"
_build_spec = importlib.util.spec_from_file_location("generate_character_builds_for_loadout_test", _BUILD_SCRIPT)
builds = importlib.util.module_from_spec(_build_spec)
_build_spec.loader.exec_module(builds)


class TestParseEquip(unittest.TestCase):
    def test_lv70_flat_and_percentage_values(self):
        item = {"document": {"documentMap": {}}}
        table = [["生命值", "+1000"], ["物理伤害加成", "+5%"], ["暴击率加成", "+12.5%"]]
        with (
            mock.patch.object(mod, "_iter_widget_contents", return_value=[("基础属性", "base")]),
            mock.patch.object(mod, "_document_tables", return_value=[table]),
        ):
            parsed = mod.parse_equip(item, "1", {})
        self.assertEqual(parsed["lv70_stats"], {"生命值": 1000, "物理伤害加成": "+5%", "暴击率加成": "+12.5%"})

    def test_recommended_loadout_preserves_duplicate_accessory_slots(self):
        item = {"document": {"documentMap": {}}}
        # 取自 2026-10-07 森空岛 item/info 的险关手甲（itemId=2335）推荐表结构：
        # 提弗洛斯 2116 / 险关装甲 2334 / 险关手甲 2335 / 险关通信器 2336 ×2。
        table = [
            ["推荐干员", "推荐用途", "装备推荐", "", "", ""],
            ["[entry:2116]", "套组", "护甲", "[entry:2334]", "护手", "[entry:2335]"],
            ["", "", "配件Ⅰ", "[entry:2336]", "配件Ⅱ", "[entry:2336]"],
        ]
        with (
            mock.patch.object(mod, "_iter_widget_contents", return_value=[("推荐装备干员", "recommended")]),
            mock.patch.object(mod, "_document_tables", return_value=[table]),
        ):
            parsed = mod.parse_equip(item, "2335", {})
        self.assertEqual(parsed["recommended_operator_ids"], ["2116"])
        self.assertEqual(
            parsed["recommended_loadouts"],
            [
                {
                    "operator_id": "2116",
                    "armor_id": "2334",
                    "gloves_id": "2335",
                    "accessory_1_id": "2336",
                    "accessory_2_id": "2336",
                }
            ],
        )

    def test_recommended_loadout_does_not_scan_past_empty_slot_value(self):
        table = [
            ["推荐干员", "推荐用途", "装备推荐", "", "", ""],
            ["[entry:2116]", "套组", "护甲", "", "", "[entry:9999]"],
        ]
        parsed = mod._parse_recommended_loadout(table)
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed["armor_id"])

    def test_generator_uses_first_complete_card_without_deduplicating_accessories(self):
        equipments = {
            "险关手甲": {
                "item_id": "2335",
                "recommended_loadouts": [
                    {
                        "operator_id": "2116",
                        "armor_id": "2334",
                        "gloves_id": "2335",
                        "accessory_1_id": "2336",
                        "accessory_2_id": "2336",
                    }
                ],
            },
            "险关通信器": {
                "item_id": "2336",
                "recommended_loadouts": [
                    {
                        "operator_id": "2116",
                        "armor_id": "9991",
                        "gloves_id": "9992",
                        "accessory_1_id": "9993",
                        "accessory_2_id": "9994",
                    }
                ],
            },
        }
        id_to_name = {
            "2116": "提弗洛斯",
            "2334": "险关装甲",
            "2335": "险关手甲",
            "2336": "险关通信器",
            "9991": "后续护甲",
            "9992": "后续护手",
            "9993": "后续配件一",
            "9994": "后续配件二",
        }
        self.assertEqual(
            builds.collect_official_loadouts(equipments, id_to_name),
            {"提弗洛斯": ["险关装甲", "险关手甲", "险关通信器", "险关通信器"]},
        )


class TestSnapshotSafety(unittest.TestCase):
    def test_partial_capture_preserves_all_existing_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "snapshots" / "partial"
            snap.mkdir(parents=True)
            data = root / "data"
            data.mkdir()
            for filename in ("weapons.json", "equipments.json", "matrices.json"):
                (data / filename).write_text("unchanged", encoding="utf-8")
            manifest = {
                "subtypes": ["2"],
                "catalog_counts": {"2": 20},
                "success_count": 0,
                "failure_count": 0,
                "items": [],
                "failures": [],
            }
            (snap / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with (
                mock.patch.object(mod, "ITEM_DETAILS_ROOT", snap.parent),
                mock.patch.object(mod, "DATA_DIR", data),
                mock.patch.object(sys, "argv", ["build_loadout_data.py"]),
            ):
                self.assertEqual(mod.main(), 1)
            for path in data.iterdir():
                self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")

    def test_complete_capture_requires_all_catalog_items_and_no_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = []
            for subtype, kind in (("2", "weapon"), ("4", "equip"), ("7", "matrix")):
                filename = f"{subtype}.json"
                (root / filename).write_text("{}", encoding="utf-8")
                entries.append({"subtype": subtype, "kind": kind, "item_id": subtype, "detail_file": filename})
            manifest = {
                "subtypes": ["2", "4", "7"],
                "limit": 0,
                "complete": True,
                "catalog_counts": {"2": 1, "4": 1, "7": 1},
                "captured_counts": {"2": 1, "4": 1, "7": 1},
                "success_count": 3,
                "failure_count": 0,
                "items": entries,
                "failures": [],
            }
            mod.validate_snapshot(manifest, root)
            for change in (
                {"failure_count": 1},
                {"catalog_counts": {"2": 2, "4": 1, "7": 1}},
                {"captured_counts": {"2": 0, "4": 1, "7": 1}},
                {"success_count": 2},
                {"catalog_counts": {"2": 1, "4": 1}},
                {"complete": False},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    mod.validate_snapshot({**manifest, **change}, root)
            (root / "7.json").unlink()
            with self.assertRaises(ValueError):
                mod.validate_snapshot(manifest, root)

    def test_limited_capture_is_rejected_even_when_selected_counts_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = []
            for subtype, kind in (("2", "weapon"), ("4", "equip"), ("7", "matrix")):
                filename = f"{subtype}.json"
                (root / filename).write_text("{}", encoding="utf-8")
                entries.append({"subtype": subtype, "kind": kind, "item_id": subtype, "detail_file": filename})
            manifest = {
                "subtypes": ["2", "4", "7"],
                "limit": 1,
                "complete": False,
                "catalog_counts": {"2": 20, "4": 30, "7": 40},
                "captured_counts": {"2": 1, "4": 1, "7": 1},
                "success_count": 3,
                "failure_count": 0,
                "items": entries,
                "failures": [],
            }
            with self.assertRaisesRegex(ValueError, "--limit"):
                mod.validate_snapshot(manifest, root)

    def test_invalid_item_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = []
            for subtype, kind in (("2", "weapon"), ("4", "equip"), ("7", "matrix")):
                filename = f"{subtype}.json"
                (root / filename).write_text("{}", encoding="utf-8")
                item_id = "../7" if subtype == "7" else subtype
                entries.append({"subtype": subtype, "kind": kind, "item_id": item_id, "detail_file": filename})
            manifest = {
                "subtypes": ["2", "4", "7"],
                "limit": 0,
                "complete": True,
                "catalog_counts": {"2": 1, "4": 1, "7": 1},
                "captured_counts": {"2": 1, "4": 1, "7": 1},
                "success_count": 3,
                "failure_count": 0,
                "items": entries,
                "failures": [],
            }
            with self.assertRaises(ValueError):
                mod.validate_snapshot(manifest, root)

    def test_explicit_partial_override_can_generate_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "snapshots" / "partial"
            snap.mkdir(parents=True)
            (snap / "manifest.json").write_text(json.dumps({"success_count": 0, "items": []}), encoding="utf-8")
            with (
                mock.patch.object(mod, "ROOT", root),
                mock.patch.object(mod, "ITEM_DETAILS_ROOT", snap.parent),
                mock.patch.object(mod, "DATA_DIR", root / "data"),
                mock.patch.object(sys, "argv", ["build_loadout_data.py", "--allow-partial"]),
            ):
                self.assertEqual(mod.main(), 0)
            self.assertEqual(json.loads((root / "data" / "weapons.json").read_text()), {})


if __name__ == "__main__":
    unittest.main()
