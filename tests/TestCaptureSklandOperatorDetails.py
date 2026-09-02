"""森空岛干员快照抓取 helper 测试。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "data-capture" / "capture_skland_operator_details.py"
)
_SPEC = importlib.util.spec_from_file_location("capture_skland_operator_details", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class _FakeResponse:
    def __init__(self, url: str, body: str):
        self.url = url
        self._body = body

    def text(self) -> str:
        return self._body


def _catalog_payload(item_ids: list[str]) -> dict:
    return {
        "data": {
            "catalog": [
                {
                    "typeSub": [
                        {
                            "id": 1,
                            "items": [{"itemId": item_id, "name": f"Operator {item_id}"} for item_id in item_ids],
                        }
                    ]
                }
            ]
        }
    }


def _write_snapshot_files(snapshot_dir: Path, item_ids: list[str]) -> list[dict]:
    _MODULE._write_text(snapshot_dir / "catalog.json", json.dumps(_catalog_payload(item_ids)))
    _MODULE._write_text(snapshot_dir / "char_pool.json", "{}")
    _MODULE._write_text(snapshot_dir / "weapon_pool.json", "{}")
    operators = []
    for item_id in item_ids:
        detail_file = f"details/{item_id}.json"
        rendered_file = f"rendered_text/{item_id}.txt"
        _MODULE._write_text(snapshot_dir / detail_file, "{}")
        _MODULE._write_text(snapshot_dir / rendered_file, "text")
        operators.append(
            {
                "item_id": item_id,
                "name": f"Operator {item_id}",
                "detail_file": detail_file,
                "rendered_text_file": rendered_file,
                "related_item_files": [],
                "detail_bytes": 2,
                "rendered_text_chars": 4,
            }
        )
    return operators


def _manifest(catalog_count: int, operators: list[dict], failures: list[dict] | None = None) -> dict:
    failures = failures or []
    return {
        "catalog_operator_count": catalog_count,
        "operator_count": len(operators) + len(failures),
        "success_count": len(operators),
        "failure_count": len(failures),
        "global_files": ["char_pool.json", "weapon_pool.json"],
        "operators": operators,
        "failures": failures,
    }


class TestCaptureSklandOperatorDetails(unittest.TestCase):
    def test_detail_response_id_matches_exact_query_value(self):
        self.assertFalse(_MODULE._has_expected_item_id(f"{_MODULE.DETAIL_API}?id=10", "1"))
        self.assertTrue(_MODULE._has_expected_item_id(f"{_MODULE.DETAIL_API}?lang=zh-CN&id=1", "1"))

    def test_snapshot_directory_conflict_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "20260830_221449")
            self.assertTrue(snapshot_dir.is_dir())
            with self.assertRaisesRegex(FileExistsError, "同秒并发抓取"):
                _MODULE._create_snapshot_dir(out_root, "20260830_221449")

    def test_saves_warm_up_global_responses_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_dir = Path(temp_dir)
            _MODULE.ROOT = snapshot_dir
            saved = set()
            responses = [
                _FakeResponse(
                    "https://zonai.skland.com/web/v1/wiki/char-pool?lang=zh-CN",
                    '{"data":"warm-up"}',
                ),
                _FakeResponse(
                    "https://zonai.skland.com/web/v1/wiki/item/list?id=1",
                    '{"data":"operator"}',
                ),
            ]
            _MODULE._save_global_responses(snapshot_dir, responses, saved)
            self.assertEqual(saved, {"char_pool.json"})
            self.assertEqual((snapshot_dir / "char_pool.json").read_text(encoding="utf-8"), '{"data":"warm-up"}')
            self.assertFalse((snapshot_dir / "item_list.json").exists())

    def test_failure_snapshot_does_not_replace_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            latest = out_root / "latest.json"
            latest.write_text('{"snapshot":"previous"}', encoding="utf-8")
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "failed")
            operators = _write_snapshot_files(snapshot_dir, ["1", "2"])
            (snapshot_dir / "details" / "2.json").unlink()
            (snapshot_dir / "rendered_text" / "2.txt").unlink()
            failure = {"item_id": "2", "error": "timeout"}

            result = _MODULE._finalize_snapshot(
                snapshot_dir,
                out_root,
                _manifest(2, operators[:1], [failure]),
            )

            self.assertFalse(result["complete"])
            self.assertTrue(any("operator_failures" in reason for reason in result["incomplete_reasons"]))
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"snapshot": "previous"})

    def test_limited_subset_does_not_replace_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            latest = out_root / "latest.json"
            latest.write_text('{"snapshot":"previous"}', encoding="utf-8")
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "limited")
            operators = _write_snapshot_files(snapshot_dir, ["1", "2"])
            (snapshot_dir / "details" / "2.json").unlink()
            (snapshot_dir / "rendered_text" / "2.txt").unlink()

            result = _MODULE._finalize_snapshot(snapshot_dir, out_root, _manifest(2, operators[:1]))

            self.assertFalse(result["complete"])
            self.assertTrue(any("catalog_subset" in reason for reason in result["incomplete_reasons"]))
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"snapshot": "previous"})

    def test_complete_snapshot_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "complete")
            operators = _write_snapshot_files(snapshot_dir, ["1", "2"])

            result = _MODULE._finalize_snapshot(snapshot_dir, out_root, _manifest(2, operators))

            self.assertTrue(result["complete"])
            latest = json.loads((out_root / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["snapshot"], "complete")
            self.assertTrue(latest["complete"])

    def test_capture_error_marks_snapshot_incomplete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            latest = out_root / "latest.json"
            latest.write_text('{"snapshot":"previous"}', encoding="utf-8")
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "aborted")
            manifest = _manifest(0, [])
            manifest["capture_errors"] = [{"stage": "capture", "error": "browser failed"}]

            result = _MODULE._finalize_snapshot(snapshot_dir, out_root, manifest)

            self.assertFalse(result["complete"])
            self.assertIn("capture_errors: 1", result["incomplete_reasons"])
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"snapshot": "previous"})

    def test_write_text_rejects_path_outside_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            _MODULE.ROOT = base
            _MODULE._write_text(base / "ok.txt", "x")
            self.assertEqual((base / "ok.txt").read_text(encoding="utf-8"), "x")
            with self.assertRaisesRegex(ValueError, "路径包含越界片段|拒绝写入"):
                _MODULE._write_text(base / ".." / "escape.txt", "x")
            with self.assertRaisesRegex(ValueError, "路径包含越界片段|拒绝写入"):
                _MODULE._write_text(base.parent / "escape.txt", "x")
            self.assertFalse((base.parent / "escape.txt").exists())

    def test_missing_global_and_unindexed_file_prevent_latest_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_root = Path(temp_dir)
            _MODULE.ROOT = out_root
            snapshot_dir = _MODULE._create_snapshot_dir(out_root, "inconsistent")
            operators = _write_snapshot_files(snapshot_dir, ["1"])
            (snapshot_dir / "weapon_pool.json").unlink()
            _MODULE._write_text(snapshot_dir / "details" / "unindexed.json", "{}")

            result = _MODULE._finalize_snapshot(snapshot_dir, out_root, _manifest(1, operators))

            self.assertFalse(result["complete"])
            self.assertTrue(any("missing_global_files" in reason for reason in result["incomplete_reasons"]))
            self.assertTrue(any("details_file_mismatch" in reason for reason in result["incomplete_reasons"]))
            self.assertFalse((out_root / "latest.json").exists())


if __name__ == "__main__":
    unittest.main()
