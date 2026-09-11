import ctypes
import time

import win32api
import win32con
import win32gui
from ok.device.intercation import PostMessageInteraction
from ok.util.logger import Logger
from pynput.keyboard import Controller, Key
from win32api import GetCursorPos, GetSystemMetrics, SetCursorPos

from src.core.game_window import find_game_hwnd
from src.interaction.Mouse import active_and_send_mouse_delta

logger = Logger.get_logger(__name__)

# 真实鼠标事件标志（pywin32 未提供常量）
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class EfInteraction(PostMessageInteraction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_position = None
        self.activated = False
        self._esc_hwnd = 0
        self._pressed_keys = {}  # 已成功按下的按键计数映射（规范化身份 -> 次数）
        self.keyboard = Controller()
        self.click_pos = None
        self.move_Cursor = False
        self._mouse_button_down = False

    def _get_mouse_button_messages(self, key):
        """获取鼠标按键对应的 Windows 消息。"""
        if key == "left":
            return (
                win32con.WM_LBUTTONDOWN,
                win32con.MK_LBUTTON,
                win32con.WM_LBUTTONUP,
            )

        return (
            win32con.WM_RBUTTONDOWN,
            win32con.MK_RBUTTON,
            win32con.WM_RBUTTONUP,
        )

    def _prepare_mouse_position(self, x, y):
        """准备鼠标点击位置，并记录原始鼠标位置。"""
        if x < 0:
            return (
                win32api.MAKELONG(
                    round(self.capture.width * 0.5),
                    round(self.capture.height * 0.5),
                ),
                False,
            )

        self.cursor_position = GetCursorPos()

        abs_x, abs_y = self.capture.get_abs_cords(x, y)
        click_pos = win32api.MAKELONG(x, y)

        win32api.SetCursorPos((abs_x, abs_y))
        time.sleep(0.001)

        return click_pos, True

    def _restore_cursor(self):
        """恢复调用前的鼠标位置。"""
        if self.move_Cursor:
            time.sleep(0.1)
            SetCursorPos(self.cursor_position)
            self.move_Cursor = False

    def click(
        self,
        x=-1,
        y=-1,
        move_back=False,
        name=None,
        down_time=0.001,
        move=True,
        key="left",
    ):
        if key == "middle":
            self._click_middle(x, y, down_time)
            return

        self.try_activate()

        self.click_pos, self.move_Cursor = self._prepare_mouse_position(x, y)
        btn_down, btn_mk, btn_up = self._get_mouse_button_messages(key)

        self.post(btn_down, btn_mk, self.click_pos)
        time.sleep(down_time)
        self.post(btn_up, 0, self.click_pos)

        if x >= 0:
            self._restore_cursor()

    def mouse_down(self, x=-1, y=-1, name=None, key="right"):
        self.try_activate()

        self.click_pos, self.move_Cursor = self._prepare_mouse_position(x, y)
        btn_down, btn_mk, _ = self._get_mouse_button_messages(key)

        self.post(btn_down, btn_mk, self.click_pos)
        self._mouse_button_down = True

    def mouse_up(self, name=None, key="right"):
        if not self._mouse_button_down:
            return
        _, _, btn_up = self._get_mouse_button_messages(key)

        self.post(btn_up, 0, self.click_pos)
        self._restore_cursor()
        self._mouse_button_down = False

    def _click_middle(self, x=-1, y=-1, down_time=0.001):
        """真实鼠标事件点击中键。

        PostMessage 的鼠标消息在游戏窗口未真实激活时可能被游戏丢弃，
        真实鼠标事件直接投递到当前前台窗口，可靠性更高。
        游戏通常处于鼠标捕获模式，点击后无需恢复光标位置。
        """
        if not active_and_send_mouse_delta(self._game_hwnd(), only_activate=True):
            return
        if x < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)
        abs_x, abs_y = self.capture.get_abs_cords(x, y)
        win32api.SetCursorPos((abs_x, abs_y))
        time.sleep(0.001)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
        time.sleep(max(down_time, 0.02))
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)

    def send(self, msg, wparam, lparam):
        win32gui.SendMessage(self.hwnd, msg, wparam, lparam)

    def _game_hwnd(self):
        # Lazy import avoids the config -> EfInteraction import cycle.
        from src.config import config

        return find_game_hwnd(config.get("windows", {})) or getattr(self.hwnd_window, "hwnd", 0)

    def activate(self, hwnd=None):
        win32gui.SendMessage(hwnd or self._game_hwnd(), win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)

    def try_activate(self):
        hwnd = self._game_hwnd()
        if win32gui.GetForegroundWindow() == hwnd:
            self.activated = False
        elif not self.activated:
            self.activated = True
            self.cursor_position = GetCursorPos()
            self.activate(hwnd)
            time.sleep(0.01)
        self.try_unclip(hwnd)

    def try_unclip(self, hwnd=None):
        try:
            # 只有在窗口存在、处于后台且有历史坐标时才进行检查
            if win32gui.GetForegroundWindow() != (hwnd or self._game_hwnd()):
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

    def _normalize_key(self, key) -> str:
        """规范化按键身份：esc/escape 归一为同一身份。"""
        k = str(key).lower()
        if k in ("esc", "escape"):
            return "esc"
        return k

    def send_key_down(self, key, activate=True, foreground=False):
        """发送按键按下。返回 True 表示按键已成功按下，False 表示未按下（如置顶失败）。"""
        # ESC 默认走 PostMessage（后台可用）；foreground=True 时走前置+pynput（主界面可靠返回）
        if str(key).lower() in ("esc", "escape") and not foreground:
            self._esc_hwnd = self._game_hwnd()
            vk_code = win32con.VK_ESCAPE
            win32gui.SendMessage(self._esc_hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32gui.PostMessage(
                self._esc_hwnd,
                win32con.WM_KEYDOWN,
                vk_code,
                self.make_lparam(vk_code, is_up=False),
            )
            return True
        if activate:
            hwnd = self._game_hwnd()
            fg_before = win32gui.GetForegroundWindow()
            was_foreground = fg_before == hwnd
            if not active_and_send_mouse_delta(hwnd, only_activate=True):
                return False
            if not was_foreground:
                # 等待窗口真正成为前台，并给游戏处理焦点切换的时间后再按键
                start = time.monotonic()
                while time.monotonic() - start < 1.0:
                    if win32gui.GetForegroundWindow() == hwnd:
                        break
                    time.sleep(0.02)
                else:
                    if win32gui.GetForegroundWindow() != hwnd:
                        logger.warning(
                            f"按键置顶失败: key={key} 游戏={hwnd} 当前前台={win32gui.GetForegroundWindow()}"
                        )
                        return False
                time.sleep(0.3)
        self.keyboard.press(self._convert_key(key))
        norm = self._normalize_key(key)
        self._pressed_keys[norm] = self._pressed_keys.get(norm, 0) + 1
        return True

    def send_key_up(self, key, foreground=False):
        key_lower = str(key).lower()
        if key_lower in ("esc", "escape") and not foreground:
            hwnd = self._esc_hwnd or self._game_hwnd()
            vk_code = win32con.VK_ESCAPE
            win32gui.PostMessage(
                hwnd,
                win32con.WM_KEYUP,
                vk_code,
                self.make_lparam(vk_code, is_up=True),
            )
            self._esc_hwnd = 0
            return
        # 配对保护：仅释放实际按下过的按键，避免向原前台应用发送未配对释放
        norm = self._normalize_key(key)
        if self._pressed_keys.get(norm, 0) <= 0:
            return
        self._pressed_keys[norm] -= 1
        if self._pressed_keys[norm] <= 0:
            del self._pressed_keys[norm]
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
