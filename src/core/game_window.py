import psutil
import pywintypes
import win32gui
import win32process


def find_game_hwnd(window_config: dict) -> int:
    """Find the visible top-level game window matching the configured features."""
    exe_config = window_config.get("exe", ())
    if isinstance(exe_config, str):
        exe_config = (exe_config,)
    exe_names = {str(name).casefold() for name in exe_config if name}

    class_config = window_config.get("hwnd_class", ())
    if isinstance(class_config, str):
        class_config = (class_config,)
    class_names = {str(name) for name in class_config if name}

    if not exe_names and not class_names:
        raise RuntimeError("config.windows 未配置 exe 或 hwnd_class，无法定位游戏窗口")

    candidates = []
    foreground_hwnd = win32gui.GetForegroundWindow()

    def collect(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if class_names and win32gui.GetClassName(hwnd) not in class_names:
                return True
            if exe_names:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(pid).name().casefold() not in exe_names:
                    return True

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(0, right - left) * max(0, bottom - top)
            candidates.append((hwnd == foreground_hwnd, area, hwnd))
        except (OSError, psutil.Error, pywintypes.error):
            pass
        return True

    win32gui.EnumWindows(collect, None)
    if not candidates:
        raise RuntimeError("未找到符合 config.windows 游戏特征的窗口")
    return max(candidates)[2]
