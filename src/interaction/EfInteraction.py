import ctypes
import time

import pynput
import win32api
import win32con
import win32gui
from ok.device.intercation import PostMessageInteraction
from ok.util.logger import Logger
from win32api import GetCursorPos, GetSystemMetrics, SetCursorPos
from pynput.keyboard import Controller, Key

from src.interaction.Mouse import active_and_send_mouse_delta

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
        self.activated = False
        self.keyboard = Controller()

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.001, move=True, key="left"):
        self.try_activate()
        move_Cursor = False
        if x < 0:
            click_pos = win32api.MAKELONG(round(self.capture.width * 0.5), round(self.capture.height * 0.5))
        else:
            self.cursor_position = GetCursorPos()
            abs_x, abs_y = self.capture.get_abs_cords(x, y)
            click_pos = win32api.MAKELONG(x, y)
            win32api.SetCursorPos((abs_x, abs_y))
            move_Cursor = True
            time.sleep(0.001)
        if key == "left":
            btn_down = win32con.WM_LBUTTONDOWN
            btn_mk = win32con.MK_LBUTTON
            btn_up = win32con.WM_LBUTTONUP
        elif key == "middle":
            btn_down = win32con.WM_MBUTTONDOWN
            btn_mk = win32con.MK_MBUTTON
            btn_up = win32con.WM_MBUTTONUP
        else:
            btn_down = win32con.WM_RBUTTONDOWN
            btn_mk = win32con.MK_RBUTTON
            btn_up = win32con.WM_RBUTTONUP
        self.post(btn_down, btn_mk, click_pos
                  )
        time.sleep(down_time)
        self.post(btn_up, 0, click_pos
                  )
        if x >= 0 and move_Cursor:
            time.sleep(0.1)
            SetCursorPos(self.cursor_position)

    def send(self, msg, wparam, lparam):
        win32gui.SendMessage(self.hwnd, msg, wparam, lparam)

    def activate(self):
        self.send(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    def try_activate(self):
        if self.hwnd_window.is_foreground():
            self.activated = False
        elif not self.activated:
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
    def send_key_down(self, key, activate=True):        
        if activate:
            self.try_activate()
        active_and_send_mouse_delta(self.hwnd, only_activate=True)
        self.keyboard.press(self._convert_key(key))

    def send_key_up(self, key):
        self.keyboard.release(self._convert_key(key))

    def _convert_key(self, key: str):
        aliases = {
            # Shift
            "shift": Key.shift,
            "lshift": Key.shift_l,
            "rshift": Key.shift_r,

            # Ctrl
            "ctrl": Key.ctrl,
            "lctrl": Key.ctrl_l,
            "rctrl": Key.ctrl_r,

            # Alt
            "alt": Key.alt,
            "lalt": Key.alt_l,
            "ralt": Key.alt_r,

            # 常用
            "enter": Key.enter,
            "tab": Key.tab,
            "space": Key.space,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "esc": Key.esc,
            "escape": Key.esc,

            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,

            "home": Key.home,
            "end": Key.end,
            "pageup": Key.page_up,
            "pagedown": Key.page_down,
        }

        key = key.lower()

        if key in aliases:
            return aliases[key]

        return getattr(Key, key, key)
