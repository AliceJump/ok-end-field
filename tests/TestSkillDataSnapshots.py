"""技能数据生成器对官方快照的选择和缺失检查。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestSnapshotChecks(unittest.TestCase):
    def test_build_generator_fails_before_writing_without_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snapshots" / "empty").mkdir(parents=True)
            with mock.patch.object(builds, "SNAP_ROOT", root / "snapshots"), \
                 mock.patch.object(builds, "BUILD_DIR", root / "output"), \
                 mock.patch.object(sys, "argv", ["generate_character_builds.py", "--snapshot", "empty"]):
                self.assertEqual(builds.main(), 1)
            self.assertFalse((root / "output").exists())

    def test_baseline_fails_before_writing_without_both_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = root / "snapshots" / "empty"
            snap.mkdir(parents=True)
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
                    with mock.patch.object(baseline, "SNAP_ROOT", root / "snapshots"), \
                         mock.patch.object(baseline, "DATA_DIR", root), \
                         mock.patch.object(sys, "argv", ["compute_damage_baseline.py", "--snapshot", "empty",
                                                        "--out", str(output)]):
                        self.assertEqual(baseline.main(), 1)
                    self.assertEqual(output.read_text(encoding="utf-8"), "unchanged")

    def test_build_generator_defaults_to_newest_snapshot_and_checks_reverse_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "assets" / "data"
            snapshots = root / "tools" / "wiki_catalog" / "operator_details"
            _write(data / "weapons.json", {"推荐武器": {"item_id": "200", "recommended_operator_ids": ["100"],
                                                    "recommended_matrix_ids": ["300"]}})
            _write(data / "characters.json", {})
            _write(data / "equipments.json", {})
            _write(data / "matrices.json", {"推荐基质": {"item_id": "300"}})
            _write(data / "character_skills" / "test.json", {"name": "测试", "element": "未知"})
            catalog = {"data": {"catalog": [{"typeSub": [{"id": "1", "items": [
                {"itemId": "100", "name": "测试"}]}]}]}}
            _write(snapshots / "20260102" / "catalog.json", catalog)
            _write(snapshots / "20260101" / "details" / "100_测试.json", {"data": {"item": {
                "itemId": "100", "document": {"documentMap": {}}}}})
            document = {"documentMap": {"rec": {"blockIds": ["note", "weapon"], "blockMap": {
                "note": {"kind": "text", "text": {"inlineElements": [
                    {"text": {"text": "注：武器推荐内容"}}]}},
                "weapon": {"kind": "text", "text": {"inlineElements": [
                    {"kind": "entry", "entry": {"id": "200", "showType": "card-big"}}]}}}}}}
            _write(snapshots / "20260102" / "details" / "100_测试.json", {"data": {"item": {
                "itemId": "100", "document": document}}})
            with mock.patch.object(builds, "ROOT", root), \
                 mock.patch.object(builds, "DATA_DIR", data), \
                 mock.patch.object(builds, "CHAR_SKILLS_DIR", data / "character_skills"), \
                 mock.patch.object(builds, "BUILD_DIR", data / "character_builds"), \
                 mock.patch.object(builds, "SNAP_ROOT", snapshots), \
                 mock.patch.object(sys, "argv", ["generate_character_builds.py"]):
                self.assertEqual(builds.main(), 0)
            result = json.loads((data / "character_builds" / "test.json").read_text(encoding="utf-8"))
            self.assertEqual(result["weapon"]["name"], "推荐武器")
            self.assertIn("反向互证=一致", result["weapon"]["note"])
            self.assertEqual(result["matrix"]["name"], "推荐基质")

    def test_baseline_defaults_to_newest_snapshot_for_secondary_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            snapshots = root / "snapshots"
            _write(data / "weapons.json", {})
            _write(data / "equipments.json", {})
            _write(data / "character_skills" / "test.json", {"name": "测试", "element": "物理", "skills": []})
            for name, secondary in (("20260101", "智识"), ("20260102", "敏捷")):
                _write(snapshots / name / "details" / "100_测试.json", {"data": {"item": {
                    "itemId": "100", "brief": {"name": "测试"}, "tagIds": ["10212"]}}})
                path = snapshots / name / "rendered_text" / "100_测试.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"主能力\n力量\n副能力\n{secondary}\n", encoding="utf-8")
            _write(snapshots / "20260102" / "catalog.json", {"data": {"catalog": [{"typeSub": [{
                "id": "1", "filterTagTree": [{"name": "主能力", "children": [
                    {"id": "10212", "name": "意志"}]}]}]}]}})
            # --out 放在 mock 的 DATA_DIR 内（写路径限定在 DATA_DIR 下）
            output = data / "damage_baseline.json"
            with mock.patch.object(baseline, "DATA_DIR", data), \
                 mock.patch.object(baseline, "SNAP_ROOT", snapshots), \
                 mock.patch.object(baseline, "ZH_CN_DIR", root / "missing_catalog"), \
                 mock.patch.object(sys, "argv", ["compute_damage_baseline.py", "--out", str(output)]):
                self.assertEqual(baseline.main(), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result[0]["primary_stat"], "意志")
            self.assertEqual(result[0]["secondary_stat"], "敏捷")


if __name__ == "__main__":
    unittest.main()
