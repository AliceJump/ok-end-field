import time

import win32api
import win32con
import win32gui


def make_lparam(vk_code: int, is_up: bool = False) -> int:
    scan_code = win32api.MapVirtualKey(vk_code, 0)
    lparam = (scan_code << 16) | 1
    if is_up:
        lparam |= (1 << 30) | (1 << 31)
    return lparam


def post_esc(hwnd: int, down_time: float = 0.01):
    # 对应 EfInteraction.activate()
    win32gui.SendMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    vk_code = win32con.VK_ESCAPE

    # 对应 PostMessageInteraction.send_key_down("esc")
    win32gui.PostMessage(
        hwnd,
        win32con.WM_KEYDOWN,
        vk_code,
        make_lparam(vk_code, is_up=False),
    )

    time.sleep(down_time)

    # 对应 PostMessageInteraction.send_key_up("esc")
    win32gui.PostMessage(
        hwnd,
        win32con.WM_KEYUP,
        vk_code,
        make_lparam(vk_code, is_up=True),
    )


# Unity 游戏窗口类名，和项目配置里的 hwnd_class 一致
hwnd = win32gui.FindWindow("UnityWndClass", None)
if not hwnd:
    raise RuntimeError("未找到 UnityWndClass 窗口")

post_esc(hwnd)