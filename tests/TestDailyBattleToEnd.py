import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks.daily.daily_battle_mixin import BattleContext, DailyBattleFeature


class _ToEndTaskHarness:
    def __init__(self):
        self.events = []
        self.box = SimpleNamespace(bottom_right=object())
        self.lang = SimpleNamespace(
            daily_battle_mixin=SimpleNamespace(
                k_b8a81b7a="abandon",
                k_39d12e73_1="claim",
            )
        )

    def wait_ocr(self, **kwargs):
        self.events.append("ocr_miss")
        return None

    def click(self, *args, **kwargs):
        self.events.append(("click", kwargs.get("key")))

    def yolo_detect(self, *args, **kwargs):
        self.events.append("yolo_hit")
        return [object()]

    def box_of_screen(self, *args, **kwargs):
        return object()

    def align_ocr_or_find_target_to_center(self, *args, **kwargs):
        self.events.append("align")
        return False

    def move_keys(self, *args, **kwargs):
        self.events.append("move")

    def sleep(self, timeout):
        self.events.append(("sleep", timeout))


class TestDailyBattleToEnd(unittest.TestCase):
    def test_yolo_hit_disables_subsequent_middle_clicks(self):
        task = _ToEndTaskHarness()
        feature = DailyBattleFeature.__new__(DailyBattleFeature)
        feature._task = task
        feature.battle_ctx = BattleContext(category_name="normal")

        with patch(
            "src.tasks.daily.daily_battle_mixin.is_world_map_text",
            return_value=False,
        ):
            result = feature.to_end()

        self.assertTrue(result)
        middle_click_indexes = [
            index
            for index, event in enumerate(task.events)
            if event == ("click", "middle")
        ]
        self.assertEqual(1, len(middle_click_indexes))
        self.assertLess(middle_click_indexes[0], task.events.index("yolo_hit"))
        self.assertEqual(3, task.events.count("ocr_miss"))

    def test_normal_reward_search_has_no_redundant_one_second_sleep(self):
        task = _ToEndTaskHarness()
        task.yolo_detect = lambda *args, **kwargs: []
        feature = DailyBattleFeature.__new__(DailyBattleFeature)
        feature._task = task
        feature.battle_ctx = BattleContext(category_name="normal")

        with patch(
            "src.tasks.daily.daily_battle_mixin.is_world_map_text",
            return_value=False,
        ):
            result = feature.to_end()

        self.assertTrue(result)
        self.assertNotIn(("sleep", 1), task.events)


if __name__ == "__main__":
    unittest.main()
