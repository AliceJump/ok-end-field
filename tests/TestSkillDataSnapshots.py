"""技能数据生成器对官方快照的选择和缺失检查。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.data.wiki_snapshots import resolve_operator_snapshot

_REPO = Path(__file__).resolve().parents[1]


def _load_module(name: str):
    path = _REPO / "scripts" / "skill-data" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builds = _load_module("generate_character_builds")
baseline = _load_module("compute_damage_baseline")


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _complete(root: Path, name: str) -> None:
    _write(root / name / "manifest.json", {"complete": True})
    _write(root / "latest.json", {"snapshot": name})


class TestSnapshotChecks(unittest.TestCase):
    def test_snapshot_selection_rejects_partial_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "captures"
            _complete(root, "complete")
            _write(root / "partial" / "manifest.json", {"complete": False})
            self.assertEqual(resolve_operator_snapshot(root).name, "complete")
            with self.assertRaises(ValueError):
                resolve_operator_snapshot(root, "partial")
            self.assertEqual(resolve_operator_snapshot(root, "partial", allow_partial=True).name, "partial")
            with self.assertRaises(ValueError):
                resolve_operator_snapshot(root, "../outside", allow_partial=True)

    def test_equipment_selection_obeys_slots_and_preserves_three_piece_set(self):
        equipment = {
            "A甲1": {"set": "A", "part": "护甲"},
            "A甲2": {"set": "A", "part": "护甲"},
            "B甲": {"set": "B", "part": "护甲"},
            "A手": {"set": "A", "part": "护手"},
            "A配": {"set": "A", "part": "配件"},
            "B配": {"set": "B", "part": "配件"},
        }
        pieces = builds.select_official_pieces(list(equipment) + ["A甲1"], equipment)
        self.assertEqual(pieces, ["A甲1", "A手", "A配", "B配"])
        self.assertEqual(builds.select_official_pieces(["A甲1", "A甲2", "A手"], equipment), ["A甲1", "A手", None, None])

    def test_partial_operator_snapshot_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "snapshots"
            _write(snap / "partial" / "manifest.json", {"complete": False})
            output = root / "damage_baseline.json"
            output.write_text("unchanged", encoding="utf-8")
            with (
                mock.patch.object(baseline, "SNAP_ROOT", snap),
                mock.patch.object(baseline, "DATA_DIR", root),
                mock.patch.object(
                    sys, "argv", ["compute_damage_baseline.py", "--snapshot", "partial", "--out", str(output)]
                ),
            ):
                self.assertEqual(baseline.main(), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_build_generator_fails_before_writing_without_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snapshots" / "empty").mkdir(parents=True)
            _complete(root / "snapshots", "empty")
            with (
                mock.patch.object(builds, "SNAP_ROOT", root / "snapshots"),
                mock.patch.object(builds, "BUILD_DIR", root / "output"),
                mock.patch.object(sys, "argv", ["generate_character_builds.py", "--snapshot", "empty"]),
            ):
                self.assertEqual(builds.main(), 1)
            self.assertFalse((root / "output").exists())

    def test_baseline_fails_before_writing_without_both_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "snapshots" / "empty"
            snap.mkdir(parents=True)
            _complete(root / "snapshots", "empty")
            # DATA_DIR 同步 mock 到 tmp：--out 写路径限定在 DATA_DIR 下（注入防御）
            output = root / "damage_baseline.json"
            output.write_text("unchanged", encoding="utf-8")
            for present in (None, "details", "rendered_text"):
                with self.subTest(present=present):
                    if present == "details":
                        _write(snap / "details" / "1.json", {})
                    elif present == "rendered_text":
                        (snap / "details" / "1.json").unlink()
                        (snap / "rendered_text").mkdir()
                        (snap / "rendered_text" / "1.txt").write_text("text", encoding="utf-8")
                    with (
                        mock.patch.object(baseline, "SNAP_ROOT", root / "snapshots"),
                        mock.patch.object(baseline, "DATA_DIR", root),
                        mock.patch.object(
                            sys, "argv", ["compute_damage_baseline.py", "--snapshot", "empty", "--out", str(output)]
                        ),
                    ):
                        self.assertEqual(baseline.main(), 1)
                    self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_build_generator_uses_complete_pointer_and_checks_reverse_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "assets" / "data"
            snapshots = root / "tools" / "wiki_catalog" / "operator_details"
            _complete(snapshots, "20260102")
            _write(snapshots / "20260103" / "manifest.json", {"complete": False})
            _write(
                data / "weapons.json",
                {
                    "推荐武器": {
                        "item_id": "200",
                        "recommended_operator_ids": ["100"],
                        "recommended_matrix_ids": ["300"],
                    }
                },
            )
            _write(data / "characters.json", {})
            _write(data / "equipments.json", {})
            _write(data / "matrices.json", {"推荐基质": {"item_id": "300"}})
            _write(data / "character_skills" / "test.json", {"name": "管理员", "element": "未知"})
            catalog = {
                "data": {
                    "catalog": [
                        {
                            "typeSub": [
                                {
                                    "id": "1",
                                    "items": [
                                        {"itemId": "100", "name": "管理员·男"},
                                        {"itemId": "101", "name": "管理员·女"},
                                    ],
                                }
                            ]
                        }
                    ]
                }
            }
            _write(snapshots / "20260102" / "catalog.json", catalog)
            _write(
                snapshots / "20260101" / "details" / "100_测试.json",
                {"data": {"item": {"itemId": "100", "document": {"documentMap": {}}}}},
            )
            document = {
                "documentMap": {
                    "rec": {
                        "blockIds": ["note", "weapon"],
                        "blockMap": {
                            "note": {
                                "kind": "text",
                                "text": {"inlineElements": [{"text": {"text": "注：武器推荐内容"}}]},
                            },
                            "weapon": {
                                "kind": "text",
                                "text": {
                                    "inlineElements": [
                                        {"kind": "entry", "entry": {"id": "200", "showType": "card-big"}}
                                    ]
                                },
                            },
                        },
                    }
                }
            }
            _write(
                snapshots / "20260102" / "details" / "100_测试.json",
                {"data": {"item": {"itemId": "100", "document": document}}},
            )
            _write(
                snapshots / "20260102" / "details" / "101_测试.json",
                {"data": {"item": {"itemId": "101", "document": {"documentMap": {}}}}},
            )
            with (
                mock.patch.object(builds, "ROOT", root),
                mock.patch.object(builds, "DATA_DIR", data),
                mock.patch.object(builds, "CHAR_SKILLS_DIR", data / "character_skills"),
                mock.patch.object(builds, "BUILD_DIR", data / "character_builds"),
                mock.patch.object(builds, "SNAP_ROOT", snapshots),
                mock.patch.object(builds, "CURATED_BUILDS", {}),
                mock.patch.object(sys, "argv", ["generate_character_builds.py"]),
            ):
                self.assertEqual(builds.main(), 0)
            result = json.loads((data / "character_builds" / "test.json").read_text(encoding="utf-8"))
            self.assertEqual(result["weapon"]["name"], "推荐武器")
            self.assertIn("反向互证=一致", result["weapon"]["note"])
            self.assertEqual(result["matrix"]["name"], "推荐基质")

    def test_build_equipment_prefers_official_equip_page_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "assets" / "data"
            snapshots = root / "tools" / "wiki_catalog" / "operator_details"
            _complete(snapshots, "20260102")
            _write(data / "weapons.json", {})
            _write(data / "characters.json", {})
            _write(
                data / "equipments.json",
                {
                    "壤流轻甲": {
                        "item_id": "1429",
                        "set": "壤流装备组",
                        "part": "护甲",
                        "quality": "金色品质",
                        "recommended_operator_ids": ["100"],
                    },
                    "壤流护手": {
                        "item_id": "1214",
                        "set": "壤流装备组",
                        "part": "护手",
                        "quality": "金色品质",
                        "recommended_operator_ids": ["100"],
                    },
                    "壤流短棍": {
                        "item_id": "1428",
                        "set": "壤流装备组",
                        "part": "配件",
                        "quality": "金色品质",
                        "recommended_operator_ids": ["100"],
                    },
                    "无关装备": {
                        "item_id": "1999",
                        "set": "其他装备组",
                        "part": "配件",
                        "quality": "金色品质",
                        "recommended_operator_ids": ["999"],
                    },
                },
            )
            _write(data / "matrices.json", {})
            _write(data / "character_skills" / "test.json", {"name": "测试", "element": "电磁"})
            catalog = {"data": {"catalog": [{"typeSub": [{"id": "1", "items": [{"itemId": "100", "name": "测试"}]}]}]}}
            _write(snapshots / "20260102" / "catalog.json", catalog)
            _write(
                snapshots / "20260102" / "details" / "100_测试.json",
                {"data": {"item": {"itemId": "100", "document": {"documentMap": {}}}}},
            )
            with (
                mock.patch.object(builds, "ROOT", root),
                mock.patch.object(builds, "DATA_DIR", data),
                mock.patch.object(builds, "CHAR_SKILLS_DIR", data / "character_skills"),
                mock.patch.object(builds, "BUILD_DIR", data / "character_builds"),
                mock.patch.object(builds, "SNAP_ROOT", snapshots),
                mock.patch.object(sys, "argv", ["generate_character_builds.py"]),
            ):
                self.assertEqual(builds.main(), 0)
            result = json.loads((data / "character_builds" / "test.json").read_text(encoding="utf-8"))
            equip = result["equipment"]
            self.assertEqual(equip["evidence_level"], 1)
            self.assertEqual(equip["set_main"], "壤流装备组")
            self.assertIsNone(equip["set_off"])
            self.assertEqual(equip["pieces"], ["壤流轻甲", "壤流护手", "壤流短棍", None])
            self.assertIn("自由散件", equip["note"])

    def test_curated_build_keeps_duplicate_accessory_and_validates_slots(self):
        equipments = {
            "壤流轻甲": {"item_id": "1429", "set": "壤流装备组", "part": "护甲"},
            "壤流护手": {"item_id": "1214", "set": "壤流装备组", "part": "护手"},
            "壤流短棍": {"item_id": "1428", "set": "壤流装备组", "part": "配件"},
        }
        cases = (
            (["壤流轻甲", "壤流护手", "壤流短棍", "壤流短棍"], None, None, 2),
            (["壤流轻甲", "壤流护手", "壤流短棍", "壤流短棍"], None, 1, 1),
            (["壤流轻甲", "壤流短棍", "壤流护手", "壤流短棍"], "部位", None, None),
            (["壤流轻甲", "壤流护手", "壤流短棍", "未知配件"], "未知装备", None, None),
        )
        for pieces, error, explicit_level, expected_level in cases:
            with self.subTest(pieces=pieces, explicit_level=explicit_level), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                data = root / "assets" / "data"
                snapshots = root / "tools" / "wiki_catalog" / "operator_details"
                _complete(snapshots, "20260102")
                _write(data / "weapons.json", {})
                _write(data / "characters.json", {})
                _write(data / "equipments.json", equipments)
                _write(data / "matrices.json", {})
                _write(data / "character_skills" / "test.json", {"name": "测试", "element": "电磁"})
                _write(snapshots / "20260102" / "catalog.json", {"data": {"catalog": []}})
                _write(
                    snapshots / "20260102" / "details" / "100_测试.json",
                    {"data": {"item": {"itemId": "100", "document": {"documentMap": {}}}}},
                )
                curated = (pieces, "社区汇总", "壤流 8/8")
                if explicit_level is not None:
                    curated += (explicit_level,)
                with (
                    mock.patch.object(builds, "ROOT", root),
                    mock.patch.object(builds, "DATA_DIR", data),
                    mock.patch.object(builds, "CHAR_SKILLS_DIR", data / "character_skills"),
                    mock.patch.object(builds, "BUILD_DIR", data / "character_builds"),
                    mock.patch.object(builds, "SNAP_ROOT", snapshots),
                    mock.patch.object(builds, "CURATED_BUILDS", {"测试": curated}),
                    mock.patch.object(sys, "argv", ["generate_character_builds.py"]),
                ):
                    if error:
                        with self.assertRaisesRegex(SystemExit, error):
                            builds.main()
                        continue
                    self.assertEqual(builds.main(), 0)
                equip = json.loads((data / "character_builds" / "test.json").read_text(encoding="utf-8"))["equipment"]
                self.assertEqual(equip["pieces"], pieces)
                self.assertEqual(equip["set_main"], "壤流装备组")
                self.assertEqual(equip["evidence_level"], expected_level)
                self.assertEqual(equip["note"], "壤流 8/8")

    def test_spell_damage_clause_applies_to_four_spell_elements(self):
        mods = baseline._parse_stat_clauses("装备者法术伤害+16%")
        self.assertEqual({m["stat"] for m in mods}, {"elem_灼热", "elem_寒冷", "elem_电磁", "elem_自然"})
        self.assertTrue(all(m["value"] == 16.0 for m in mods))
        self.assertEqual(baseline._parse_stat_clauses("物理伤害+10%"), [baseline._parse_stat_clause("物理伤害+10%")])

    def test_baseline_defaults_to_newest_snapshot_for_secondary_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            snapshots = root / "snapshots"
            _write(data / "weapons.json", {})
            _write(data / "equipments.json", {})
            _write(data / "character_skills" / "test.json", {"name": "管理员", "element": "物理", "skills": []})
            for name, secondary in (("20260101", "智识"), ("20260102", "敏捷")):
                _complete(snapshots, name)
                _write(
                    snapshots / name / "details" / "100_测试.json",
                    {"data": {"item": {"itemId": "100", "brief": {"name": "管理员·男"}, "tagIds": ["10212"]}}},
                )
                path = snapshots / name / "rendered_text" / "100_测试.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"主能力\n力量\n副能力\n{secondary}\n", encoding="utf-8")
            _write(
                snapshots / "20260102" / "catalog.json",
                {
                    "data": {
                        "catalog": [
                            {
                                "typeSub": [
                                    {
                                        "id": "1",
                                        "filterTagTree": [
                                            {"name": "主能力", "children": [{"id": "10212", "name": "意志"}]}
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                },
            )
            # --out 放在 mock 的 DATA_DIR 内（写路径限定在 DATA_DIR 下）
            output = data / "damage_baseline.json"
            with (
                mock.patch.object(baseline, "DATA_DIR", data),
                mock.patch.object(baseline, "SNAP_ROOT", snapshots),
                mock.patch.object(baseline, "ZH_CN_DIR", root / "missing_catalog"),
                mock.patch.object(sys, "argv", ["compute_damage_baseline.py", "--out", str(output)]),
            ):
                self.assertEqual(baseline.main(), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result[0]["primary_stat"], "意志")
            self.assertEqual(result[0]["secondary_stat"], "敏捷")
            self.assertIn("官方标签", next(line for line in result[0]["trace"] if "主能力:" in line))


if __name__ == "__main__":
    unittest.main()