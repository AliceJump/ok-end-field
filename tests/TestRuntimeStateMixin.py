"""任务侧运行时状态客户端的等待和定位控制面测试。"""

import unittest

from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic
from src.tasks.mixin.runtime_state_mixin import RuntimeStateMixin


class _PositionService:
    enabled = True
    minimap_position_ready = True
    _minimap_started = False

    def __init__(self, hub):
        self.hub = hub
        self.samples = 0

    def start_minimap_position(self, *, wait_stable=True):
        self._minimap_started = True
        return True

    def sample_world_pose(self, frame=None, *, now=None):
        self.samples += 1
        value = {
            "x": float(self.samples),
            "z": 2.0,
            "map_id": "map01",
            "position_trusted": self.samples >= 2,
        }
        self.hub.publish(
            RuntimeTopic.WORLD_POSE,
            value,
            source="test",
            now=now,
            ttl=2.0,
        )
        return value


class _Task(RuntimeStateMixin):
    def __init__(self, hub, position_service):
        self._t = 0.0
        self.hub = hub
        self.position_service = position_service
        self.logs = []
        self._init_runtime_state_mixin(hub=hub)
        self._runtime_position_service = position_service

    def active_time(self):
        return self._t

    def sleep(self, seconds):
        self._t += max(0.0, float(seconds))

    def log_warning(self, message, notify=False):
        self.logs.append(str(message))


class TestRuntimeStateMixin(unittest.TestCase):
    def setUp(self):
        self.hub = RuntimeStateHub()
        self.service = _PositionService(self.hub)
        self.task = _Task(self.hub, self.service)

    def test_world_pose_refreshes_owner_and_reads_snapshot(self):
        pose = self.task.world_pose(max_age=1.0)

        self.assertEqual(pose["x"], 1.0)
        self.assertEqual(self.service.samples, 1)

    def test_wait_world_pose_waits_for_predicate(self):
        pose = self.task.wait_world_pose(
            lambda value: bool(value.get("position_trusted")),
            max_age=2.0,
            timeout=1.0,
            tick=0.1,
        )

        self.assertIsNotNone(pose)
        self.assertEqual(pose["x"], 2.0)
        self.assertEqual(self.service.samples, 2)

    def test_runtime_state_rejects_topic_value_after_ttl(self):
        self.hub.publish(
            RuntimeTopic.WORLD_SCENE,
            {"name": "world"},
            now=1.0,
            ttl=0.5,
        )

        self.assertIsNone(
            self.task.runtime_state(RuntimeTopic.WORLD_SCENE, now=1.6),
        )

    def test_position_service_issue_is_notified_once_until_state_changes(self):
        self.task._runtime_position_service = None

        self.assertIsNone(self.task.ensure_runtime_position_service())
        self.assertIsNone(self.task.ensure_runtime_position_service())
        self.assertEqual(sum("未注册「小地图定位」" in message for message in self.task.logs), 1)

        self.task._runtime_position_service = self.service
        self.assertIs(self.task.ensure_runtime_position_service(), self.service)

        self.task._runtime_position_service = None
        self.assertIsNone(self.task.ensure_runtime_position_service())
        self.assertEqual(sum("未注册「小地图定位」" in message for message in self.task.logs), 2)


if __name__ == "__main__":
    unittest.main()
