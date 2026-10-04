"""build_loadout_data 装备基础属性解析测试。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skill-data" / "build_loadout_data.py"
_spec = importlib.util.spec_from_file_location("build_loadout_data", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


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
