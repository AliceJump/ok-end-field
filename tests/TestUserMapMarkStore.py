import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.data import user_map_mark_store


class TestUserMapMarkStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patcher = mock.patch.object(
            user_map_mark_store,
            "LOG_DIR",
            Path(self.temp_dir.name) / "logs" / "user_map_marks",
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_latest_snapshot_overwrites_for_one_account(self):
        first = user_map_mark_store.write_latest_snapshot(
            "acc_1",
            "map01",
            {"供电桩": [{"x": 1.0, "y": 2.0, "z": 3.0}]},
        )
        second = user_map_mark_store.write_latest_snapshot(
            "acc_1",
            "map02",
            {"中继器": [{"x": 4.0, "y": 5.0, "z": 6.0}]},
        )

        self.assertEqual(first, second)
        self.assertEqual(first.name, "acc_1.json")
        self.assertEqual(list(first.parent.glob("*.json")), [first])

        data = json.loads(first.read_text(encoding="utf-8"))
        self.assertEqual(data["map_id"], "map02")
        self.assertEqual(data["marks"], {"中继器": [{"x": 4.0, "y": 5.0, "z": 6.0}]})

    def test_account_key_prefers_account_id_then_map_user_id(self):
        self.assertEqual(
            user_map_mark_store.resolve_user_mark_account_key(
                account_id="acc_a",
                map_user_id="uid_b",
            ),
            "acc_a",
        )
        self.assertEqual(
            user_map_mark_store.resolve_user_mark_account_key(
                account_id="",
                map_user_id="uid_b",
            ),
            "uid_b",
        )

    def test_persist_user_marks_writes_only_save_marks(self):
        payload = {
            "code": 0,
            "data": {
                "markTemplates": [
                    {"id": "power", "name": "供电桩"},
                    {"id": "relay", "name": "中继器"},
                ],
                "marks": [
                    {
                        "templateId": "power",
                        "mapId": "map01",
                        "pos": {"x": 1, "y": 2, "z": 3},
                    }
                ],
                "saveMarks": [
                    {
                        "templateId": "relay",
                        "mapId": "map01",
                        "pos": {"x": 4, "y": 5, "z": 6},
                    }
                ],
            },
        }

        path = user_map_mark_store.persist_user_marks(
            payload,
            map_id="map01",
            account_id="acc_1",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["marks"], {"中继器": [{"x": 4.0, "y": 5.0, "z": 6.0}]})


if __name__ == "__main__":
    unittest.main()
