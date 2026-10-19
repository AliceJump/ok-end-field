# Test case
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok.test.TaskTestCase import TaskTestCase

from src.config import config
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
from src.tasks.trigger.AutoCombatTask import AutoCombatTask


class TestMyOneTimeTask(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def set_image(self, image):
        if not Path(image).exists():
            self.skipTest(f"Missing image: {image}")
        super().set_image(image)

    def test_enemy_presence_capture_option_defaults_off(self):
        self.assertIn(KEY_SAVE_ENEMY_PRESENCE_FRAMES, self.task.default_config)
        self.assertFalse(self.task.default_config[KEY_SAVE_ENEMY_PRESENCE_FRAMES])
        self.assertIn(KEY_SAVE_ENEMY_PRESENCE_FRAMES, self.task.config_description)
        description = self.task.config_description[KEY_SAVE_ENEMY_PRESENCE_FRAMES]
        self.assertIn(".inform.json", description)
        self.assertIn("screenshots/enemy_presence", description)

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



class TestResolutionMinimum(unittest.TestCase):
    def test_supported_resolution_minimum_is_900p(self):
        self.assertEqual(config["supported_resolution"]["min_size"], (1600, 900))

    def test_runtime_accepts_900p_without_warning(self):
        task = SimpleNamespace(_wait_for_stable_resolution=lambda: (1600, 900), log_info=Mock())
        with patch.object(RuntimeMixin, "_resolution_warned", False):
            RuntimeMixin.check_resolution(task)
        task.log_info.assert_not_called()

    def test_runtime_warns_below_900p(self):
        task = SimpleNamespace(_wait_for_stable_resolution=lambda: (1600, 899), log_info=Mock())
        with patch.object(RuntimeMixin, "_resolution_warned", False):
            RuntimeMixin.check_resolution(task)
        task.log_info.assert_called_once()
        self.assertIn("1600x900", task.log_info.call_args.args[0])
        self.assertTrue(task.log_info.call_args.kwargs["notify"])

    def test_auto_combat_warns_only_below_minimum(self):
        task = SimpleNamespace(width=1600, height=900, active_time=lambda: 10.0, log_warning=Mock())
        logic = AutoCombatLogic.__new__(AutoCombatLogic)
        logic.task = task
        logic._warn_low_resolution_if_due()
        task.log_warning.assert_not_called()
        task.height = 899
        logic._warn_low_resolution_if_due()
        task.log_warning.assert_called_once()
        self.assertIn("1600x900", task.log_warning.call_args.args[0])
        self.assertTrue(task.log_warning.call_args.kwargs["notify"])


if __name__ == "__main__":
    unittest.main()
