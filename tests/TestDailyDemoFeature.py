import unittest
from unittest.mock import call, patch

from src.tasks.onetime.DemoBattleTask import DemoBattleTask


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


class _DemoImpl(_DemoTask, DemoBattleTask):
    """harness 桩方法优先，DemoBattleTask 提供被测方法。"""


class DailyDemoFeatureTests(unittest.TestCase):
    def test_battle_demo_skips_confirmation_after_double_reward_is_enabled(self):
        task = _DemoImpl([])
        feature = task
        feature.left_time = True

        with (
            patch.object(feature, "go_to_demo_graphic", return_value=True),
            patch.object(feature, "_demo_click_track_and_transfer", return_value=True),
            patch.object(feature, "enter_page", return_value=True),
            patch.object(feature, "read_level", side_effect=[5, 5, 5]),
            patch.object(feature, "click_random_and_wait_level_change", side_effect=[6, 6, 5, -1]) as click_random,
            patch.object(feature, "wait_click_feature", return_value=True),
            patch.object(feature, "box_of_screen", return_value=object(), create=True),
            patch.object(feature, "ensure_main", create=True),
            patch.object(feature, "auto_battle", create=True),
            patch.object(feature, "log_info", create=True),
            patch.object(feature, "click_confirm") as click_confirm,
        ):
            self.assertFalse(feature.battle_demo())

        click_random.assert_has_calls(
            [
                call(5, double_reward_opened=False),
                call(5, double_reward_opened=False),
                call(5, double_reward_opened=False),
                call(5, double_reward_opened=True),
            ]
        )
        click_confirm.assert_not_called()

    def test_confirmed_draw_waits_for_level_before_retrying(self):
        task = _DemoImpl([None, 6])
        feature = task

        with patch.object(feature, "wait_level_change", side_effect=task.wait_level_change):
            self.assertEqual(feature.click_random_and_wait_level_change(5), 6)
        self.assertEqual(task.events, ["random", "wait", "confirm", "wait"])

    def test_retries_when_level_stays_unchanged_after_confirmation(self):
        task = _DemoImpl([None, None, 6])
        feature = task

        with patch.object(feature, "wait_level_change", side_effect=task.wait_level_change):
            self.assertEqual(feature.click_random_and_wait_level_change(5), 6)
        self.assertEqual(task.events, ["random", "wait", "confirm", "wait", "warning", "random", "wait"])


if __name__ == "__main__":
    unittest.main()
