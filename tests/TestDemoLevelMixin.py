import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.tasks.onetime.DemoBattleTask import DemoBattleTask
from src.tasks.onetime.DemoDrawTask import DemoDrawTask


class _LevelHarness:
    screen_width = 1920

    def __init__(self):
        self.box_of_screen = Mock(return_value=object())
        self.wait_feature = Mock()
        self.find_one = Mock()
        self.log_info = Mock()
        self.mark_task_failure = Mock()

    def tr(self, text):
        return text

    def wait_until(self, condition, **kwargs):
        for _ in range(3):
            if condition():
                return True
        return None

    def tip(self, level):
        return SimpleNamespace(x=self.screen_width * (0.125 + (0.802 - 0.125) / 11 * (level + 0.5)))


class TestDemoLevelMixin(unittest.TestCase):
    def tasks(self):
        for task_type in (DemoBattleTask, DemoDrawTask):
            yield type("LevelHarness", (_LevelHarness, task_type), {})()

    def test_reads_level_at_multiple_resolutions(self):
        for task in self.tasks():
            for width in (1920, 3840):
                task.screen_width = width
                for level in (0, 5, 10):
                    with self.subTest(task=type(task).__mro__[2].__name__, width=width, level=level):
                        task.wait_feature.return_value = task.tip(level)
                        self.assertEqual(task.read_level(), level)

    def test_missing_tip_marks_failure_and_returns_negative_level(self):
        for task in self.tasks():
            task.wait_feature.return_value = None
            self.assertEqual(task.read_level(), -1)
            task.mark_task_failure.assert_called_once()

    def test_waits_through_missing_and_unchanged_tip_then_accepts_zero(self):
        for task in self.tasks():
            task.find_one.side_effect = [None, task.tip(5), task.tip(0)]
            self.assertEqual(task.wait_level_change(5), 0)
            self.assertEqual(task.find_one.call_count, 3)

    def test_unchanged_level_times_out(self):
        for task in self.tasks():
            task.find_one.return_value = task.tip(5)
            self.assertIsNone(task.wait_level_change(5))


if __name__ == "__main__":
    unittest.main()
