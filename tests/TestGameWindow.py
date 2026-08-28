import unittest
from unittest.mock import patch

from src.core.game_window import find_game_hwnd


class TestGameWindow(unittest.TestCase):
    @patch("src.core.game_window.psutil.Process")
    @patch("src.core.game_window.win32process.GetWindowThreadProcessId")
    @patch("src.core.game_window.win32gui")
    def test_find_game_hwnd_matches_class_and_executable(self, win32gui, get_process_id, process):
        win32gui.GetForegroundWindow.return_value = 100
        win32gui.IsWindowVisible.return_value = True
        win32gui.GetClassName.return_value = "UnityWndClass"
        win32gui.GetWindowRect.return_value = (0, 0, 1920, 1080)
        win32gui.EnumWindows.side_effect = lambda callback, context: [callback(hwnd, context) for hwnd in (100, 200)]
        get_process_id.side_effect = lambda hwnd: (1, hwnd)
        process.side_effect = lambda pid: type(
            "Process", (), {"name": lambda self: "Other.exe" if pid == 100 else "Endfield.exe"}
        )()

        hwnd = find_game_hwnd({"exe": ["Endfield.exe"], "hwnd_class": "UnityWndClass"})

        self.assertEqual(hwnd, 200)


if __name__ == "__main__":
    unittest.main()
