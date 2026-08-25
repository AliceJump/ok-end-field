import unittest
from unittest.mock import MagicMock, call, patch

import win32con

from src.interaction.EfInteraction import EfInteraction


class TestEfInteraction(unittest.TestCase):
    @patch("src.interaction.EfInteraction.time.sleep")
    @patch("src.interaction.EfInteraction.active_and_send_mouse_delta")
    @patch("src.interaction.EfInteraction.win32gui")
    def test_back_posts_escape_without_forcing_foreground(self, win32gui, activate, sleep):
        interaction = EfInteraction.__new__(EfInteraction)
        interaction._last_key_log_times = {}  # 本地 ok-script BaseInteraction.send_key 新增的日志间隔表
        interaction.keyboard = MagicMock()
        interaction._game_hwnd = MagicMock(return_value=200)
        interaction._esc_hwnd = 0
        interaction.make_lparam = MagicMock(side_effect=(11, 22))

        interaction.back()

        activate.assert_not_called()
        interaction.keyboard.assert_not_called()
        win32gui.SendMessage.assert_called_once_with(200, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        self.assertEqual(
            win32gui.PostMessage.call_args_list,
            [
                call(200, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 11),
                call(200, win32con.WM_KEYUP, win32con.VK_ESCAPE, 22),
            ],
        )
        self.assertEqual(interaction._esc_hwnd, 0)
        sleep.assert_called_once_with(0.01)

    @patch("src.interaction.EfInteraction.active_and_send_mouse_delta")
    @patch("src.interaction.EfInteraction.GetCursorPos", return_value=(10, 20))
    @patch("src.interaction.EfInteraction.win32gui")
    def test_try_activate_uses_background_message_for_mouse(self, win32gui, _cursor, force_activate):
        win32gui.GetForegroundWindow.return_value = 100
        interaction = EfInteraction.__new__(EfInteraction)
        interaction._game_hwnd = MagicMock(return_value=200)
        interaction.activated = False
        interaction.cursor_position = None
        interaction.try_unclip = MagicMock()

        interaction.try_activate()

        force_activate.assert_not_called()
        win32gui.SendMessage.assert_called_once_with(200, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    @patch("src.interaction.EfInteraction.active_and_send_mouse_delta")
    @patch("src.interaction.EfInteraction.win32gui")
    def test_regular_key_down_forces_configured_window_foreground(self, win32gui, force_activate):
        interaction = EfInteraction.__new__(EfInteraction)
        interaction._game_hwnd = MagicMock(return_value=200)
        interaction.keyboard = MagicMock()
        interaction._background_key_hold_count = 0
        interaction._key_prev_hwnd = 0
        interaction._background_mode = MagicMock(return_value=False)
        win32gui.GetForegroundWindow.return_value = 200  # 游戏已在前台

        interaction.send_key_down("a")

        force_activate.assert_called_once_with(200, only_activate=True)
        interaction.keyboard.press.assert_called_once_with("a")


if __name__ == "__main__":
    unittest.main()
