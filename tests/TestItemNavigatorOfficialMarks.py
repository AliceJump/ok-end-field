import unittest
from unittest import mock

from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask


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


class TestItemNavigatorOfficialMarks(unittest.TestCase):
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
