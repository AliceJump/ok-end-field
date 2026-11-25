"""SearchMixin 的移动搜索顺序回归测试。"""

import unittest
from types import SimpleNamespace

from src.tasks.mixin.search_mixin import SearchMixin


class TestStrafeSearch(unittest.TestCase):
    def test_each_move_is_followed_by_recognition_even_when_timeout_is_reached(self):
        clock = {"t": 0.0}
        events = []

        def move(key, duration):
            events.append(("move", key))
            clock["t"] += duration

        def check():
            events.append(("check", clock["t"]))
            return "hit" if len(events) == 3 else None

        task = SimpleNamespace(
            active_time=lambda: clock["t"],
            move_keys=move,
        )

        result = SearchMixin.strafe_search(
            task,
            check,
            passes=1,
            duration=0.2,
            keys=("s",),
            time_out=0.1,
        )

        self.assertEqual(result, "hit")
        self.assertEqual(
            events,
            [("check", 0.0), ("move", "s"), ("check", 0.2)],
        )

    def test_first_recognition_hit_does_not_move(self):
        events = []
        task = SimpleNamespace(
            active_time=lambda: 0.0,
            move_keys=lambda key, duration: events.append(("move", key)),
        )

        result = SearchMixin.strafe_search(
            task,
            lambda: "hit",
            passes=1,
            duration=0.2,
            keys=("s", "w"),
        )

        self.assertEqual(result, "hit")
        self.assertEqual(events, [])


class TestHoldAndDetect(unittest.TestCase):
    def _task(self, clock, events):
        def sleep(seconds):
            events.append(("sleep", seconds))
            clock["t"] += seconds

        return SimpleNamespace(
            active_time=lambda: clock["t"],
            sleep=sleep,
            send_key_down=lambda key: events.append(("down", key)),
            send_key_up=lambda key: events.append(("up", key)),
        )

    def test_poll_interval_never_exceeds_remaining_hold_time(self):
        """间隔大于按住时长时，按键必须按时长收敛，而不是等满一个间隔。"""
        clock = {"t": 0.0}
        events = []

        result = SearchMixin.hold_and_detect(
            self._task(clock, events),
            "d",
            lambda: None,
            duration=0.5,
            poll_interval=5,
        )

        self.assertIsNone(result)
        self.assertLessEqual(clock["t"], 0.5 + 1e-9)
        self.assertEqual(events[-1], ("up", "d"))

    def test_hit_releases_key_without_waiting_for_full_duration(self):
        clock = {"t": 0.0}
        events = []
        calls = {"count": 0}

        def check():
            calls["count"] += 1
            return "hit" if calls["count"] == 2 else None

        result = SearchMixin.hold_and_detect(
            self._task(clock, events),
            "d",
            check,
            duration=3.0,
            poll_interval=0.2,
        )

        self.assertEqual(result, "hit")
        self.assertAlmostEqual(clock["t"], 0.2)
        self.assertEqual([event for event in events if event[0] in ("down", "up")], [("down", "d"), ("up", "d")])


if __name__ == "__main__":
    unittest.main()
