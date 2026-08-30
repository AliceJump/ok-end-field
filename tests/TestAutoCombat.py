# Test case
import unittest
from pathlib import Path

from ok.test.TaskTestCase import TaskTestCase
from src.config import config
from src.tasks.trigger.AutoCombatTask import AutoCombatTask


class TestMyOneTimeTask(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def set_image(self, image):
        if not Path(image).exists():
            self.skipTest(f"Missing image: {image}")
        super().set_image(image)

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


if __name__ == "__main__":
    unittest.main()
