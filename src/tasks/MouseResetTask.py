import ctypes
import time

from qfluentwidgets import FluentIcon
from win32api import GetCursorPos, GetSystemMetrics, SetCursorPos

from ok import Logger, TriggerTask

logger = Logger.get_logger(__name__)

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

class MouseResetTask(TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.group_name = "Diagnosis"
        self.group_icon = FluentIcon.ROBOT
        self.trigger_interval = 10
        self.name = "防止鼠标被锁定在屏幕中心"
        self.icon = FluentIcon.MOVE
        self.running_reset = False
        self.mouse_pos = None

    def run(self):
        if self.is_browser():
            return
        if self.enabled:
            if not self.running_reset:
                logger.info('start mouse reset')
                self.running_reset = True
                self.handler.post(self.mouse_reset, 0.01)
        else:
            self.running_reset = False

    def mouse_reset(self):
        try:
            pos = GetCursorPos()
            # 只有在窗口存在、处于后台且有历史坐标时才进行检查
            if self.hwnd and self.hwnd.exists and not self.hwnd.is_foreground() and self.mouse_pos:
                rect = RECT()
                ctypes.windll.user32.GetClipCursor(ctypes.byref(rect))
                sx, sy = GetSystemMetrics(0), GetSystemMetrics(1)
                
                # 检查是否被限制(Clip) 或 发生长距离跳变(>200像素, 可能是游戏强制回中)
                is_clipped = (rect.right - rect.left) < sx or (rect.bottom - rect.top) < sy
                is_jumped = (pos[0] - self.mouse_pos[0])**2 + (pos[1] - self.mouse_pos[1])**2 > 40000

                if is_clipped or is_jumped:
                    ctypes.windll.user32.ClipCursor(0)
                    SetCursorPos(self.mouse_pos)
                    return  # 恢复位置后直接返回, 不更新mouse_pos

            self.mouse_pos = pos
        except Exception:
            pass
        finally:
            if self.enabled and not self.paused:
                self.handler.post(self.mouse_reset, 0.01)
            else:
                self.running_reset = False
