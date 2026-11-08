import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.core.NavConfig import NAV_CONTENT_KEY
from src.data.official_facility_source import OfficialFacilitySource
from src.data.official_map_client import MapAuthError, OfficialMapClient
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

    def test_legacy_delete_failure_retries_cleanup_without_new_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = self.make_task(root)
            old_path = root / "assets/items/map/marked_points.json"
            old_path.parent.mkdir(parents=True)
            payload = {"map01": ["point-a"]}
            old_path.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch(
                    "src.tasks.trigger.ItemNavigatorTask.Path", side_effect=lambda *parts: root.joinpath(*parts)
                ),
                mock.patch("src.tasks.trigger.ItemNavigatorTask.time.time", return_value=100),
                mock.patch.object(Path, "unlink", side_effect=OSError("file locked")),
            ):
                task._load_marked()
            self.assertTrue(task._dirty)
            self.assertTrue(old_path.exists())
            self.assertEqual(json.loads(task._marked_store.read_text(encoding="utf-8")), payload)
            task._navigator_window_missing_logged = False
            with (
                mock.patch.multiple(
                    task,
                    check_resolution=mock.Mock(),
                    _is_game_window_alive=mock.Mock(return_value=False),
                    _cleanup_navigator_runtime=mock.Mock(),
                    info_set=mock.Mock(),
                ),
                mock.patch("src.tasks.trigger.ItemNavigatorTask.time.time", return_value=104),
            ):
                ItemNavigatorTask.run.__wrapped__(task)
            self.assertFalse(task._dirty)
            self.assertFalse(old_path.exists())
            self.assertIsNone(task._legacy_marked_source)
            self.assertEqual(json.loads(task._marked_store.read_text(encoding="utf-8")), payload)

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
        self.last_error = ""

    def prepare(self):
        return True

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
        self.config = {"content": "content"}
        self.tr = lambda message: message
        self._official_facilities = OfficialFacilitySource(client_factory=lambda _: service, clock=lambda: self.now)

    def active_time(self):
        return self.now

    def log_info(self, message):
        self.logs.append(message)

    def get_runtime_position_service(self):
        return self.service

    def _official_marks_for_map(self, map_id):
        return ItemNavigatorTask._official_marks_for_map(self, map_id)

    def _use_unified_navigation(self):
        return self.unified


class TestItemNavigatorOfficialMarks(unittest.TestCase):
    def test_marking_requires_known_same_height(self):
        task = object.__new__(ItemNavigatorTask)
        task._near_xz_threshold = 2
        point = {"x": 1, "y": 10, "z": 1}
        self.assertFalse(task._can_mark_target(point, "矿石", None))
        self.assertFalse(task._can_mark_target(point, "矿石", 0))
        self.assertTrue(task._can_mark_target(point, "矿石", 10))

    def test_http_facility_client_needs_no_ws_position_service(self):
        consumer = SimpleNamespace(config={"content": "content"})
        client = OfficialMapClient(consumer)
        client._resolve_auth_bundle = mock.Mock(return_value={"cred": "cred", "sign_token": "token"})
        client._resolve_map_account_from_cred = mock.Mock(return_value={"roleId": "r1", "serverId": "s1"})
        self.assertTrue(client.prepare())
        self.assertTrue(client.prepare())
        client._resolve_auth_bundle.assert_called_once_with("content")
        self.assertFalse(hasattr(client, "_start_map_ws_client"))
        self.assertFalse(hasattr(client, "_recv_ws_position_payload"))
        consumer.config["content"] = "other-content"
        self.assertTrue(client.prepare())
        self.assertEqual(client._resolve_auth_bundle.call_count, 2)

    def test_http_auth_failure_cools_down_and_does_not_reuse_old_account(self):
        client = OfficialMapClient(SimpleNamespace(config={"content": "content"}))
        client._map_ws_account = {"roleId": "old"}
        client._resolve_auth_bundle = mock.Mock(side_effect=MapAuthError("offline"))
        self.assertFalse(client.prepare())
        self.assertFalse(client.prepare())
        self.assertEqual(client._resolve_auth_bundle.call_count, 1)
        self.assertIsNone(client._map_ws_account)

    def test_auth_failure_logs_once_per_retry_without_response_credentials(self):
        task = SimpleNamespace(config={"content": "content"}, log_info=mock.Mock(), tr=lambda message: message)
        client = OfficialMapClient(task)
        client._resolve_auth_bundle = mock.Mock(side_effect=MapAuthError("cred=private-token response body"))
        now = [10]
        source = task._official_facilities = OfficialFacilitySource(
            client_factory=lambda _: client, clock=lambda: now[0]
        )
        with mock.patch("src.data.official_map_client.time.monotonic", return_value=10):
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(task, "map01"), {})
            source._pending[2].result(timeout=3)
            for _ in range(3):
                self.assertEqual(ItemNavigatorTask._official_marks_for_map(task, "map01"), {})
        self.assertEqual(task.log_info.call_count, 1)
        self.assertEqual(client.last_error, "MapAuthError")
        self.assertNotIn("private-token", task.log_info.call_args.args[0])
        self.assertNotIn("response body", task.log_info.call_args.args[0])
        now[0] = 41
        with mock.patch("src.data.official_map_client.time.monotonic", return_value=41):
            ItemNavigatorTask._official_marks_for_map(task, "map01")
            source._pending[2].result(timeout=3)
            ItemNavigatorTask._official_marks_for_map(task, "map01")
        self.assertEqual(task.log_info.call_count, 2)
        client._resolve_auth_bundle.side_effect = None
        client._resolve_auth_bundle.return_value = {"cred": "cred", "sign_token": "token"}
        client._resolve_map_account_from_cred = mock.Mock(return_value={"roleId": "r1", "serverId": "s1"})
        client._map_api_get = mock.Mock(return_value={"code": 0, "data": {"saveMarks": []}})
        now[0] = 72
        with (
            mock.patch("src.data.official_map_client.time.monotonic", return_value=72),
            mock.patch("src.data.user_map_mark_store.persist_user_marks"),
        ):
            ItemNavigatorTask._official_marks_for_map(task, "map01")
            source._pending[2].result(timeout=3)
            ItemNavigatorTask._official_marks_for_map(task, "map01")
        self.assertEqual(client.last_error, "")
        self.assertEqual(source.last_error, "")

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
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), {})
            stub._official_facilities._pending[2].result(timeout=3)
            first = ItemNavigatorTask._official_marks_for_map(stub, "map01")
            self.assertEqual(persist.call_count, 1)

        self.assertEqual(first, {"供电桩": [{"x": 1.0, "y": 2.0, "z": 3.0}]})
        self.assertEqual(service.calls, 1)

        stub.now += 10
        cached = ItemNavigatorTask._official_marks_for_map(stub, "map01")
        self.assertEqual(cached, first)
        self.assertEqual(service.calls, 1)

        stub.now += 30
        with mock.patch("src.data.user_map_mark_store.persist_user_marks"):
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), {})
            stub._official_facilities._pending[2].result(timeout=3)
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), first)
        self.assertEqual(service.calls, 2)

    def test_slow_authentication_returns_local_points_without_waiting(self):
        service = _Service({"code": 0, "data": {"saveMarks": []}})
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def prepare():
            entered.set()
            self.assertTrue(release.wait(3))
            return True

        service.prepare = prepare
        stub = _Stub(service)
        local = {"矿石": [{"x": 1, "z": 2}]}
        with (
            mock.patch(
                "src.tasks.trigger.ItemNavigatorTask.item_map_query.get_item_map", return_value={"map01": local}
            ),
            mock.patch("src.data.user_map_mark_store.persist_user_marks"),
        ):
            self.assertEqual(ItemNavigatorTask._get_candidates_for_map(stub, "map01", ["矿石"]), local)
            self.assertTrue(entered.wait(1))
            pending = stub._official_facilities._pending
            for _ in range(3):
                self.assertEqual(ItemNavigatorTask._get_candidates_for_map(stub, "map01", ["矿石"]), local)
                self.assertIs(stub._official_facilities._pending, pending)
            self.assertEqual(service.calls, 0)
            release.set()
            pending[2].result(timeout=3)

    def test_context_changes_and_cleanup_discard_late_results(self):
        source = OfficialFacilitySource()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        stale = {"旧账号": [{"x": 1, "z": 2}]}
        calls = []

        def fetch(context):
            calls.append(context)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
                return stale, ""
            return {"新账号": []}, ""

        source._fetch = fetch
        self.assertEqual(source.poll("map01", "old", account_id="a"), {})
        self.assertTrue(entered.wait(1))
        first = source._pending
        source.invalidate()
        self.assertEqual(source.poll("map02", "new", account_id="b"), {})
        self.assertIs(source._pending, first)
        release.set()
        first[2].result(timeout=3)
        self.assertEqual(source.poll("map02", "new", account_id="b"), {})
        source._pending[2].result(timeout=3)
        self.assertEqual(source.poll("map02", "new", account_id="b"), {"新账号": []})
        self.assertEqual(calls, [("map01", "old", "a"), ("map02", "new", "b")])

    def test_failed_query_and_successful_empty_query_keep_retry_interval(self):
        service = _Service({"code": 0, "data": {"saveMarks": []}})
        service._map_api_get = mock.Mock(side_effect=TimeoutError("private response body"))
        stub = _Stub(service)
        source = stub._official_facilities
        ItemNavigatorTask._official_marks_for_map(stub, "map01")
        source._pending[2].result(timeout=3)
        for _ in range(3):
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), {})
        self.assertEqual(service._map_api_get.call_count, 1)
        self.assertEqual(source.last_error, "TimeoutError")
        self.assertEqual(len(stub.logs), 1)
        self.assertNotIn("private response", stub.logs[0])
        stub.now += 31
        service._map_api_get.side_effect = None
        service._map_api_get.return_value = service.payload
        with mock.patch("src.data.user_map_mark_store.persist_user_marks", side_effect=OSError("disk full")):
            ItemNavigatorTask._official_marks_for_map(stub, "map01")
            source._pending[2].result(timeout=3)
            self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), {})
        self.assertEqual(source.last_error, "")
        stub.now += 10
        self.assertEqual(ItemNavigatorTask._official_marks_for_map(stub, "map01"), {})
        self.assertEqual(service._map_api_get.call_count, 2)

    def test_completed_result_does_not_gain_freshness_while_navigation_is_idle(self):
        now = [100]
        source = OfficialFacilitySource(clock=lambda: now[0])
        source._fetch = mock.Mock(return_value=({"old": []}, ""))
        self.assertEqual(source.poll("map01", "content"), {})
        source._pending[2].result(timeout=3)
        now[0] = 131
        source._fetch.return_value = ({"new": []}, "")
        self.assertEqual(source.poll("map01", "content"), {})
        source._pending[2].result(timeout=3)
        self.assertEqual(source.poll("map01", "content"), {"new": []})


if __name__ == "__main__":
    unittest.main()
