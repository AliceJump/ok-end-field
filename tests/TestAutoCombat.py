# Test case
import unittest
from pathlib import Path

from ok import Box
from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.tasks.trigger.AutoCombatTask import AutoCombatTask
from src.tasks.mixin.battle_mixin import BattleMixin


class TestMyOneTimeTask(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def set_image(self, image):
        if not Path(image).exists():
            self.skipTest(f"Missing image: {image}")
        super().set_image(image)

    def test_16_10_combat(self):
        self.set_image('tests/images/16_10_combat.png')
        in_team = self.task.in_team()
        self.task.screenshot("16_10_combat", show_box=True)
        count = self.task.get_skill_bar_count()
        self.task.sleep(1)
        self.assertTrue(in_team)
        self.assertEqual(count, 2)

    def test_skill_bars(self):
        self.set_image('tests/images/in_combat_5.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 1)

        self.set_image('tests/images/in_combat_1440p.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 3)

        self.set_image('tests/images/not_in_combat.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, -1)

        self.set_image('tests/images/in_combat_4.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 0)

        self.set_image('tests/images/in_combat_red_health.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 0)

        self.set_image('tests/images/in_combat_low_health.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 1)

        self.set_image('tests/images/in_combat_2_bars.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 2)

        self.set_image('tests/images/in_combat_3_bars.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 3)

        self.set_image('tests/images/in_combat_1_bars.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 1)

        self.set_image('tests/images/in_combat_3_blink.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 3)

        self.set_image('tests/images/in_combat_0_bars.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 0)

        self.set_image('tests/images/skip_quest_confirm.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, -1)

        self.set_image('tests/images/in_combat_2.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 2)

        self.set_image('tests/images/in_combat_white_red.png')
        count = self.task.get_skill_bar_count()
        self.assertEqual(count, 0)

    def test_lvs(self):
        self.set_image('tests/images/no_combat2.png')
        self.assertFalse(self.task.in_combat())

        self.set_image('tests/images/no_combat.png')
        self.assertFalse(self.task.in_combat())

        self.set_image('tests/images/in_combat_2.png')
        self.assertFalse(self.task.ocr_lv())

        self.set_image('tests/images/in_team.png')
        self.assertTrue(self.task.ocr_lv())

    def test_parse_skill_sequence_unified_for_comma_style(self):
        self.assertEqual(
            self.task._parse_skill_sequence(" ult_2， 1, , e , sleep_1 "),
            ["ult_2", "1", "e", "sleep_1"],
        )

    def test_parse_skill_sequence_normal_token(self):
        # 正常 normal_[n] 应被接受
        self.assertEqual(
            self.task._parse_skill_sequence("1, normal_5, ult_2"),
            ["1", "normal_5", "ult_2"],
        )
        # 浮点秒数
        self.assertEqual(
            self.task._parse_skill_sequence("normal_0.5"),
            ["normal_0.5"],
        )
        # n<=0 应被忽略（返回默认序列）
        self.assertEqual(
            self.task._parse_skill_sequence("normal_0"),
            ["1", "2", "3"],
        )
        self.assertEqual(
            self.task._parse_skill_sequence("normal_-1"),
            ["1", "2", "3"],
        )
        # 非数字参数应被忽略
        self.assertEqual(
            self.task._parse_skill_sequence("normal_abc"),
            ["1", "2", "3"],
        )

    def test_in_team_falls_back_to_later_skill_template(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 4)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                matches = {
                    ("skill_1", boxes[0].x): Box(101, 11, 10, 10, confidence=0.99),
                    ("skill_2", boxes[1].x): None,
                    ("skill_3", boxes[2].x): Box(301, 11, 10, 10, confidence=0.98),
                }
                return matches.get((feature, box.x))

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 3)
        self.assertIn("skill_3->框3", task.last_log)

    def test_in_team_requires_two_skill_matches(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 3)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                if feature == "skill_1" and box.x == boxes[0].x:
                    return Box(101, 11, 10, 10, confidence=0.99)
                return None

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertFalse(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 0)

    def test_in_team_accepts_skill_one_in_last_slot(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 5)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                if feature == "skill_1" and box.x == boxes[-1].x:
                    return Box(401, 11, 10, 10, confidence=0.99)
                return None

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 1)


if __name__ == '__main__':
    unittest.main()
