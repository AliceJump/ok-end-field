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


if __name__ == "__main__":
    unittest.main()
