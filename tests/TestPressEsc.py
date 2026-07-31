import unittest
from unittest.mock import MagicMock

from src.core.base_mixin.game_flow_mixin import GameFlowMixin


class _PressEscHarness(GameFlowMixin):
    def __init__(self):
        self.keyboard = MagicMock()


class TestPressEsc(unittest.TestCase):
    def test_press_esc_uses_task_keyboard_controller(self):
        task = _PressEscHarness()

        task.press_esc()

        task.keyboard.press.assert_called_once()
        task.keyboard.release.assert_called_once_with(task.keyboard.press.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
