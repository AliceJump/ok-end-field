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
        self.height_patcher = patch.object(
            LoginMixin, "height", new_callable=PropertyMock, return_value=100
        )
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
        task._confirm_logged_in = MagicMock(return_value=True)
        task.log_error = MagicMock()
        task.box = SimpleNamespace(bottom_left=MagicMock(), center=MagicMock())
        task.lang = SimpleNamespace(login_mixin=SimpleNamespace(ms=MagicMock(), k_20275ef2=MagicMock()))
        task.box_of_screen = MagicMock(return_value=MagicMock())
        return task

    @patch("src.tasks.mixin.login_mixin.pyautogui.click")
    def test_login_aborts_before_text_clicks_when_window_activation_fails(self, pyautogui_click):
        task = self._make_task()
        task.active_and_send_mouse_delta.return_value = False

        self.assertFalse(task.login_flow("1234567890"))

        task.wait_click_feature.assert_not_called()
        task.click_text.assert_not_called()
        pyautogui_click.assert_not_called()

    def test_login_escapes_recent_account_suffix(self):
        task = self._make_task()
        recent_entry = MagicMock(y=40, height=20)
        task.click_text.side_effect = ([recent_entry], MagicMock(), MagicMock())
        username = "ab.+"

        task.login_flow(username)

        account_match = task.click_text.call_args_list[1].args[0]
        self.assertIsInstance(account_match, re.Pattern)
        self.assertEqual(account_match.pattern, re.escape(username[-4:]))
        self.assertIsNotNone(account_match.fullmatch(username[-4:]))
        self.assertEqual(task.click_text.call_args_list[2], call("登录", box=task.box.center))


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
