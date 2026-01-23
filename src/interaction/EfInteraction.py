import re
import traceback
import time
import win32api
import win32con
from win32api import GetCursorPos, GetSystemMetrics, SetCursorPos
import win32gui
import ctypes

from ok import PostMessageInteraction, Logger

logger = Logger.get_logger(__name__)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

class EfInteraction(PostMessageInteraction):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None

    def send(self, msg, wparam, lparam):
        win32gui.SendMessage(self.hwnd, msg, wparam, lparam)

    def activate(self):
        self.send(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    def try_activate(self):
        if not self.activated:
            if not self.hwnd_window.is_foreground():
                self.activated = True
                self.cursor_position = GetCursorPos()
                self.activate()
                time.sleep(0.01)
        self.try_unclip()

    def try_unclip(self):
        try:
            # 只有在窗口存在、处于后台且有历史坐标时才进行检查
            if not self.hwnd_window.is_foreground():
                rect = RECT()
                ctypes.windll.user32.GetClipCursor(ctypes.byref(rect))
                sx, sy = GetSystemMetrics(0), GetSystemMetrics(1)
                
                # 检查是否被限制(Clip) 或 发生长距离跳变(>200像素, 可能是游戏强制回中)
                is_clipped = (rect.right - rect.left) < sx or (rect.bottom - rect.top) < sy
                # is_jumped = (pos[0] - self.cursor_position[0])**2 + (pos[1] - self.cursor_position[1])**2 > 40000

                if is_clipped:
                    ctypes.windll.user32.ClipCursor(0)
                    if self.cursor_position:
                        SetCursorPos(self.cursor_position)
                    return  # 恢复位置后直接返回, 不更新mouse_pos
        except Exception:
            pass
        finally:
            self.cursor_position = None