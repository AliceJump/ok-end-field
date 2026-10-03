import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, call, patch

if sys.platform == "win32":
    from src.interaction import Mouse
    from src.tasks.mixin.login_mixin import LoginMixin


@unittest.skipUnless(sys.platform == "win32", "Login automation requires Windows desktop dependencies")
class TestLoginMixin(unittest.TestCase):
    def setUp(self):
        self.height_patcher = patch.object(LoginMixin, "height", new_callable=PropertyMock, return_value=100)
        self.height_patcher.start()
        self.addCleanup(self.height_patcher.stop)

    def _make_task(self):
        task = LoginMixin.__new__(LoginMixin)
        task.active_time = MagicMock(side_effect=(0, 3))
        task.wait_ocr = MagicMock(return_value=None)
        task.wait_feature = MagicMock(return_value=MagicMock())
        task.click = MagicMock()
        task.active_and_send_mouse_delta = MagicMock(return_value=True)
        task.wait_click_feature = MagicMock(return_value=True)
        task.click_text = MagicMock()
        task._click_account_from_recent_list = MagicMock(return_value=MagicMock())
        task._confirm_logged_in = MagicMock(return_value=True)
        task.login_ocr = MagicMock(return_value=[])
        task.log_error = MagicMock()
        task.log_info = MagicMock()
        task.sleep = MagicMock()
        task.get_game_hwnd = MagicMock(return_value=1)
        task.box = SimpleNamespace(bottom_left=MagicMock(), center=MagicMock())
        task.lang = SimpleNamespace(login_mixin=SimpleNamespace(ms=MagicMock(), k_20275ef2=MagicMock()))
        task.box_of_screen = MagicMock(return_value=MagicMock())
        return task

    @patch("src.tasks.mixin.login_mixin.pyautogui.click")
    def test_login_aborts_before_text_clicks_when_window_activation_fails(self, pyautogui_click):
        task = self._make_task()
        task.active_and_send_mouse_delta.return_value = False

        self.assertFalse(task.login_flow("13812345678"))

        task.wait_click_feature.assert_not_called()
        task.click_text.assert_not_called()
        pyautogui_click.assert_not_called()

    def test_login_searches_full_account_area_below_recent_tab(self):
        task = self._make_task()
        recent_entry = SimpleNamespace(x=10, y=40, width=30, height=20)
        account_box = MagicMock()
        task.box_of_screen.return_value = account_box
        task.click_text.side_effect = ([recent_entry], MagicMock())
        username = "13812345678"

        task.login_flow(username)

        task.box_of_screen.assert_called_once_with(0, 0.6, 1, 1)
        task._click_account_from_recent_list.assert_called_once_with(username, account_box)
        self.assertEqual(task.click_text.call_args_list[1], call("登录", box=task.box.center))

    def test_duplicate_visible_accounts_choose_non_recent_row(self):
        task = self._make_task()
        task._click_account_from_recent_list = LoginMixin._click_account_from_recent_list.__get__(task, LoginMixin)
        first = SimpleNamespace(x=10, y=10, width=80, height=10)
        second = SimpleNamespace(x=10, y=50, width=80, height=10)
        marker_box_1 = MagicMock()
        marker_box_2 = MagicMock()
        task.box_of_screen.side_effect = [marker_box_1, marker_box_2]
        task.login_ocr.side_effect = [[MagicMock()], []]

        chosen = LoginMixin._choose_account_candidate(task, [first, second])

        self.assertIs(chosen, second)
        first_recent_match = task.login_ocr.call_args_list[0].kwargs["match"]
        second_recent_match = task.login_ocr.call_args_list[1].kwargs["match"]
        self.assertIsInstance(first_recent_match, re.Pattern)
        self.assertEqual(first_recent_match.pattern, "最近")
        self.assertEqual(second_recent_match.pattern, "最近")
        self.assertEqual(task.login_ocr.call_args_list[0].kwargs["box"], marker_box_1)
        self.assertEqual(task.login_ocr.call_args_list[1].kwargs["box"], marker_box_2)

    def test_duplicate_visible_accounts_refuse_ambiguous_non_recent_rows(self):
        task = self._make_task()
        first = SimpleNamespace(x=10, y=10, width=80, height=10)
        second = SimpleNamespace(x=10, y=50, width=80, height=10)
        task.login_ocr.side_effect = [[], []]

        self.assertIsNone(LoginMixin._choose_account_candidate(task, [first, second]))
        task.log_error.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "Window activation requires Windows desktop dependencies")
class TestWindowActivation(unittest.TestCase):
    @patch("src.interaction.Mouse.win32gui.IsWindow", return_value=False)
    def test_returns_false_for_invalid_window_handle(self, is_window):
        self.assertFalse(Mouse.active_and_send_mouse_delta(100, only_activate=True))
        is_window.assert_called_once_with(100)

    @patch("win32api.keybd_event")
    @patch("src.interaction.Mouse.time.sleep")
    @patch("src.interaction.Mouse.win32gui.IsWindowVisible", return_value=True)
    @patch("src.interaction.Mouse.win32gui.IsIconic", return_value=False)
    @patch("src.interaction.Mouse.win32gui.IsWindow", return_value=True)
    @patch("src.interaction.Mouse.win32gui.SetForegroundWindow")
    @patch("src.interaction.Mouse.win32gui.GetForegroundWindow", return_value=1)
    def test_returns_false_when_foregrounding_fails(
        self, get_foreground, set_foreground, _is_window, _is_iconic, _is_visible, _sleep, _keybd_event
    ):
        set_foreground.side_effect = Mouse.win32gui.error(5, "SetForegroundWindow", "Access denied")

        self.assertFalse(Mouse.active_and_send_mouse_delta(100, only_activate=True))

        get_foreground.assert_called_once()
        self.assertEqual(set_foreground.call_args_list, [call(100), call(100)])

    @patch("src.interaction.Mouse.user32.mouse_event")
    @patch("win32api.keybd_event")
    @patch("src.interaction.Mouse.time.sleep")
    @patch("src.interaction.Mouse.win32gui.IsWindowVisible", return_value=True)
    @patch("src.interaction.Mouse.win32gui.IsIconic", return_value=False)
    @patch("src.interaction.Mouse.win32gui.IsWindow", return_value=True)
    @patch("src.interaction.Mouse.win32gui.SetForegroundWindow")
    @patch("src.interaction.Mouse.win32gui.GetForegroundWindow", return_value=1)
    def test_zero_winerror_returns_false_without_sending_mouse_events(
        self,
        _get_foreground,
        set_foreground,
        _is_window,
        _is_iconic,
        _is_visible,
        _sleep,
        _keybd_event,
        mouse_event,
    ):
        set_foreground.side_effect = Mouse.win32gui.error(0, "SetForegroundWindow", "No error message")

        self.assertFalse(Mouse.active_and_send_mouse_delta(100))

        mouse_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
