"""运行时状态网关的快照、时效和复制语义测试。"""

import unittest

from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic


class TestRuntimeStateHub(unittest.TestCase):
    def setUp(self):
        self.hub = RuntimeStateHub()

    def test_publish_overwrites_latest_value_and_increments_sequence(self):
        first = self.hub.publish(
            RuntimeTopic.WORLD_POSE,
            {"x": 1.0},
            source="first",
            now=10.0,
            ttl=2.0,
        )
        second = self.hub.publish(
            RuntimeTopic.WORLD_POSE,
            {"x": 2.0},
            source="second",
            now=10.5,
            ttl=2.0,
        )

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(
            self.hub.value(RuntimeTopic.WORLD_POSE, now=10.6),
            {"x": 2.0},
        )
        self.assertIsNone(
            self.hub.value(RuntimeTopic.WORLD_POSE, now=13.0),
        )

    def test_consumer_cannot_mutate_stored_snapshot(self):
        self.hub.publish(
            RuntimeTopic.WORLD_SCENE,
            {"name": "world", "flags": ["normal"]},
            now=1.0,
        )

        value = self.hub.value(RuntimeTopic.WORLD_SCENE, now=1.0)
        value["name"] = "battle"
        value["flags"].append("mutated")

        stored = self.hub.value(RuntimeTopic.WORLD_SCENE, now=1.0)
        self.assertEqual(stored, {"name": "world", "flags": ["normal"]})

    def test_max_age_rejects_stale_snapshot(self):
        self.hub.publish(RuntimeTopic.SENSOR_WS, {"connected": True}, now=5.0)

        self.assertIsNotNone(
            self.hub.snapshot(RuntimeTopic.SENSOR_WS, max_age=0.5, now=5.4),
        )
        self.assertIsNone(
            self.hub.snapshot(RuntimeTopic.SENSOR_WS, max_age=0.5, now=5.6),
        )

    def test_clear_removes_topic_and_health_entry(self):
        self.hub.publish(RuntimeTopic.SYSTEM_HEALTH, {"ok": True}, now=1.0)

        self.hub.clear(RuntimeTopic.SYSTEM_HEALTH)

        self.assertIsNone(self.hub.snapshot(RuntimeTopic.SYSTEM_HEALTH, now=1.0))
        self.assertEqual(self.hub.health(), [])


if __name__ == "__main__":
    unittest.main()
