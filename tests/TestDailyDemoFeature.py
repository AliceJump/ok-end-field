import unittest
from unittest.mock import call, patch

from src.data.FeatureList import FeatureList as fL
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
    def test_battle_demo_confirms_third_draw_with_and_without_double_reward(self):
        task = _DemoImpl([4, 3, None, 6, 4, 3, None, 6, 6])
        feature = task
        feature.left_time = True

        with (
            patch.object(feature, "go_to_demo_graphic", return_value=True),
            patch.object(feature, "_demo_click_track_and_transfer", return_value=True),
            patch.object(feature, "enter_page", return_value=True),
            patch.object(feature, "read_level", side_effect=[5, 5, 5]),
            patch.object(
                feature, "click_random_and_wait_level_change", wraps=feature.click_random_and_wait_level_change
            ) as click_random,
            patch.object(feature, "wait_click_feature", return_value=True) as click_feature,
            patch.object(feature, "box_of_screen", return_value=object(), create=True),
            patch.object(feature, "ensure_main", create=True),
            patch.object(feature, "auto_battle", create=True),
            patch.object(feature, "log_info", create=True),
            patch.object(feature, "click_confirm", wraps=feature.click_confirm) as click_confirm,
        ):
            self.assertTrue(feature.battle_demo())

        self.assertEqual(
            click_random.call_args_list,
            [
                call(5, draw_count=1),
                call(4, draw_count=2),
                call(3, draw_count=3),
                call(5, draw_count=1),
                call(4, draw_count=2),
                call(3, draw_count=3),
                call(5, draw_count=1),
            ],
        )
        double_reward_call = call(feature=fL.demo_double_open, time_out=10, raise_if_not_found=False)
        self.assertEqual(click_feature.call_args_list.count(double_reward_call), 2)
        self.assertEqual(click_confirm.call_args_list, [call(time_out=1), call(time_out=1), call(time_out=3)])

    def test_first_two_draws_retry_without_checking_confirmation(self):
        for draw_count in (1, 2):
            with self.subTest(draw_count=draw_count):
                task = _DemoImpl([None, 6])
                self.assertEqual(task.click_random_and_wait_level_change(5, draw_count=draw_count), 6)
                self.assertEqual(task.events, ["random", "wait", "warning", "random", "wait"])

    def test_confirmed_draw_waits_for_level_before_retrying(self):
        for draw_count in (3, 4):
            with self.subTest(draw_count=draw_count):
                task = _DemoImpl([None, 6])
                self.assertEqual(task.click_random_and_wait_level_change(5, draw_count=draw_count), 6)
                self.assertEqual(task.events, ["random", "wait", "confirm", "wait"])

    def test_missing_confirmation_still_waits_before_retrying_random(self):
        task = _DemoImpl([None, 6])

        with patch.object(task, "click_confirm", return_value=False) as click_confirm:
            self.assertEqual(task.click_random_and_wait_level_change(5, draw_count=3), 6)

        click_confirm.assert_called_once_with(time_out=1)
        self.assertEqual(task.events, ["random", "wait", "wait"])

    def test_retries_when_level_stays_unchanged_after_confirmation(self):
        task = _DemoImpl([None, None, 6])
        feature = task

        with patch.object(feature, "wait_level_change", side_effect=task.wait_level_change):
            self.assertEqual(feature.click_random_and_wait_level_change(5, draw_count=3), 6)
        self.assertEqual(task.events, ["random", "wait", "confirm", "wait", "warning", "random", "wait"])


if __name__ == "__main__":
    unittest.main()
