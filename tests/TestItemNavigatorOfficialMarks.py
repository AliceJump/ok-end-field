import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.core.NavConfig import NAV_CONTENT_KEY
from src.localization.ws_position_mixin import WsPositionMixin
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask


class TestMarkedPointMigration(unittest.TestCase):
    def test_constructor_preserves_failed_migration_for_runtime_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "assets/items/map/marked_points.json"
            old_path.parent.mkdir(parents=True)
            payload = {"map01": ["point-a"]}
            old_path.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch("src.tasks.trigger.ItemNavigatorTask.config_folder", return_value=str(root)),
                mock.patch("src.core.BaseEfTask.get_global_config", return_value={}),
                mock.patch(
                    "src.tasks.trigger.ItemNavigatorTask.Path", side_effect=lambda *parts: root.joinpath(*parts)
                ),
                mock.patch.object(Path, "replace", side_effect=OSError("disk full")),
                mock.patch.object(ItemNavigatorTask, "log_error"),
                mock.patch.object(ItemNavigatorTask, "log_info"),
                mock.patch.object(ItemNavigatorTask, "tr", side_effect=lambda message: message),
            ):
                task = ItemNavigatorTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
            self.assertTrue(task._dirty)
            self.assertEqual(task._last_save_time, 0.0)
            self.assertTrue(old_path.exists())
            task.config = {}
            with mock.patch.multiple(
                task,
                check_resolution=mock.Mock(),
                _is_game_window_alive=mock.Mock(return_value=True),
                _read_navigation_position=mock.Mock(return_value=(("map01", 0, 0, 0), "")),
                _get_candidates_for_map=mock.Mock(return_value={}),
                info_set=mock.Mock(),
                sleep=mock.Mock(),
            ):
                ItemNavigatorTask.run.__wrapped__(task)
            self.assertFalse(task._dirty)
            self.assertFalse(old_path.exists())
            self.assertEqual(json.loads(task._marked_store.read_text(encoding="utf-8")), payload)

    def make_task(self, root):
        task = object.__new__(ItemNavigatorTask)
        task._marked_store = root / "custom-config" / "marked_points.json"
        task._marked = {}
        task._marked_lock = threading.Lock()
        task._dirty = False
        task._last_save_time = 0
        task._last_marked_save_attempt = None
        task.log_info = mock.Mock()
        task.log_error = mock.Mock()
        task.tr = lambda message: message
        return task

    def test_failed_runtime_retries_are_throttled_without_game_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = self.make_task(Path(tmp))
            task._dirty = True
            task._navigator_window_missing_logged = False
            with (
                mock.patch.multiple(
                    task,
                    check_resolution=mock.Mock(),
                    _is_game_window_alive=mock.Mock(return_value=False),
                    _cleanup_navigator_runtime=mock.Mock(),
                    _save_marked=mock.Mock(return_value=False),
                    info_set=mock.Mock(),
                ),
                mock.patch("src.tasks.trigger.ItemNavigatorTask.time.time") as clock,
            ):
                for now, expected_attempts in ((100, 1), (101, 1), (103.1, 2)):
                    clock.return_value = now
                    ItemNavigatorTask.run.__wrapped__(task)
                    self.assertEqual(task._save_marked.call_count, expected_attempts)
            self.assertTrue(task._dirty)

    def test_save_failure_preserves_legacy_file_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "assets/items/map/marked_points.json"
            old_path.parent.mkdir(parents=True)
            payload = {"map01": ["point-a", "point-b"]}
            old_path.write_text(json.dumps(payload), encoding="utf-8")
            task = self.make_task(root)
            with mock.patch(
                "src.tasks.trigger.ItemNavigatorTask.Path", side_effect=lambda *parts: root.joinpath(*parts)
            ):
                with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
                    task._load_marked()
                self.assertEqual(json.loads(old_path.read_text(encoding="utf-8")), payload)
                self.assertFalse(task._marked_store.exists())
                self.assertTrue(task._dirty)
                task._load_marked()
            self.assertFalse(old_path.exists())
            self.assertFalse(task._dirty)
            self.assertEqual(
                set(json.loads(task._marked_store.read_text(encoding="utf-8"))["map01"]), set(payload["map01"])
            )
            task._marked.clear()
            task._load_marked()
            self.assertEqual(task._marked, {"map01": set(payload["map01"])})

    def test_existing_new_marks_win_without_deleting_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self.make_task(root)
            task._marked_store.parent.mkdir(parents=True)
            task._marked_store.write_text(json.dumps({"map02": ["new"]}), encoding="utf-8")
            old_path = root / "assets/items/map/marked_points.json"
            old_path.parent.mkdir(parents=True)
            old_path.write_text(json.dumps({"map01": ["old"]}), encoding="utf-8")
            with mock.patch(
                "src.tasks.trigger.ItemNavigatorTask.Path", side_effect=lambda *parts: root.joinpath(*parts)
            ):
                task._load_marked()
            self.assertEqual(task._marked, {"map02": {"new"}})
            self.assertTrue(old_path.exists())


class _Service:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self._map_ws_cred = "cred"
        self._map_ws_account = {"roleId": "r1", "serverId": "s1"}

    def _map_api_get(self, path, params):
        self.calls += 1
        self.last_path = path
        self.last_params = params
        return self.payload


class _Stub:
    def __init__(self, service):
        self.service = service
        self.unified = True
        self.now = 100.0
        self.logs = []
        self._official_marks_cache = {}
        self._official_marks_empty = set()
        self._official_marks_fetched_at = {}

    def active_time(self):
        return self.now

    def log_info(self, message):
        self.logs.append(message)

    def get_runtime_position_service(self):
        return self.service

    def _use_unified_navigation(self):
        return self.unified


class TestItemNavigatorOfficialMarks(unittest.TestCase):
    def test_legacy_position_source_reads_migrated_global_content(self):
        task = SimpleNamespace(config={})

        with mock.patch(
            "src.core.global_config_store.get_global_config",
            return_value={NAV_CONTENT_KEY: "migrated-content"},
        ):
            self.assertEqual(WsPositionMixin._get_account_map_content(task), "migrated-content")

    def test_legacy_candidate_lookup_does_not_query_official_marks(self):
        service = _Service({})
        stub = _Stub(service)
        stub.unified = False
        static = {"map01": {"晶锥天使": [{"x": 1.0, "z": 2.0}]}}

        with mock.patch(
            "src.tasks.trigger.ItemNavigatorTask.item_map_query.get_item_map",
            return_value=static,
        ):
            result = ItemNavigatorTask._get_candidates_for_map(stub, "map01", ["晶锥天使"])

        self.assertEqual(result, static["map01"])
        self.assertEqual(service.calls, 0)

    def test_fetches_and_caches_current_map_official_marks(self):
        payload = {
            "code": 0,
            "data": {
                "markTemplates": [{"id": "power", "name": "供电桩"}],
                "marks": [],
                "saveMarks": [
                    {
                        "templateId": "power",
                        "mapId": "map01",
                        "pos": {"x": 1, "y": 2, "z": 3},
                    }
                ],
            },
        }
        service = _Service(payload)
        stub = _Stub(service)

        with mock.patch("src.data.user_map_mark_store.persist_user_marks") as persist:
            first = ItemNavigatorTask._official_marks_for_map(stub, "map01")
            self.assertEqual(persist.call_count, 1)

        self.assertEqual(first, {"供电桩": [{"x": 1.0, "y": 2.0, "z": 3.0}]})
        self.assertEqual(service.calls, 1)

        stub.now += 10
        cached = ItemNavigatorTask._official_marks_for_map(stub, "map01")
        self.assertEqual(cached, first)
        self.assertEqual(service.calls, 1)

        stub.now += 30
        ItemNavigatorTask._official_marks_for_map(stub, "map01")
        self.assertEqual(service.calls, 2)


if __name__ == "__main__":
    unittest.main()
