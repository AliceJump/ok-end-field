# Test case
import unittest

from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.tasks.AutoCombatTask import AutoCombatTask


class TestMyOneTimeTask(TaskTestCase):
    task_class = AutoCombatTask

    config = config

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

    def test_parse_skill_sequence_legacy_123(self):
        self.assertEqual(self.task._parse_skill_sequence("123"), ["1", "2", "3"])
        self.assertEqual(self.task._parse_skill_sequence("1a2b3"), ["1", "2", "3"])

    def test_parse_skill_sequence_unified_for_comma_style(self):
        self.assertEqual(
            self.task._parse_skill_sequence(" ult_2， 1, , e , sleep_1 "),
            ["ult_2", "1", "e", "sleep_1"],
        )


if __name__ == '__main__':
    unittest.main()
