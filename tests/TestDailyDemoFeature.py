import unittest
from unittest.mock import patch

from src.tasks.daily.daily_demo_mixin import DailyDemoFeature


class _DemoTask:
    def __init__(self, levels):
        self.default_config = {}
        self.config_description = {}
        self.levels = iter(levels)
        self.events = []

    def wait_click_feature(self, **kwargs):
        self.events.append("random")
        return True

    def wait_level_change(self, previous_level, time_out):
        self.events.append("wait")
        return next(self.levels)

    def click_confirm(self, time_out):
        self.events.append("confirm")
        return True

    def log_warning(self, message):
        self.events.append("warning")


class DailyDemoFeatureTests(unittest.TestCase):
    def test_confirmed_draw_waits_for_level_before_retrying(self):
        task = _DemoTask([None, 6])
        feature = DailyDemoFeature(task)

        with patch.object(feature, "wait_level_change", side_effect=task.wait_level_change):
            self.assertEqual(feature.click_random_and_wait_level_change(5), 6)
        self.assertEqual(task.events, ["random", "wait", "confirm", "wait"])

    def test_retries_when_level_stays_unchanged_after_confirmation(self):
        task = _DemoTask([None, None, 6])
        feature = DailyDemoFeature(task)

        with patch.object(feature, "wait_level_change", side_effect=task.wait_level_change):
            self.assertEqual(feature.click_random_and_wait_level_change(5), 6)
        self.assertEqual(task.events, ["random", "wait", "confirm", "wait", "warning", "random", "wait"])


if __name__ == "__main__":
    unittest.main()
