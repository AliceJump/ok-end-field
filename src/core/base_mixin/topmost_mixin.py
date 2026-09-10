"""临时 Windows TOPMOST 窗口置顶机制。

在任务运行期间，持续监测前台窗口，对符合条件的非游戏窗口临时设置
HWND_TOPMOST，任务结束时统一恢复为非 TOPMOST。

使用方式::

    class MyTask(BaseEfTask, TopmostMixin):
        def run(self):
            self.start_topmost_monitor()
            try:
                ...  # 任务逻辑
            finally:
                self.stop_topmost_monitor()

``stop_topmost_monitor()`` 也会在 ``on_destroy()`` 中被调用作为安全兜底。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time

import win32gui
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

# ── Win32 常量 ──────────────────────────────────────────────
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

_user32 = ctypes.windll.user32


def _is_window_topmost(hwnd: int) -> bool:
    """通过扩展窗口样式判断窗口是否处于 TOPMOST。"""
    try:
        ex_style = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
        return bool(ex_style & WS_EX_TOPMOST)
    except Exception:
        return False


def _set_window_topmost(hwnd: int) -> bool:
    """将窗口设置为 HWND_TOPMOST（不激活、不移动、不调整大小）。"""
    try:
        return bool(
            _user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        )
    except Exception:
        return False


def _remove_window_topmost(hwnd: int) -> bool:
    """将窗口恢复为非 TOPMOST。"""
    try:
        return bool(
            _user32.SetWindowPos(
                hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        )
    except Exception:
        return False


class TopmostMixin:
    """为 Task 提供临时 TOPMOST 窗口置顶能力。

    监测前台窗口变化，对同时满足以下条件的窗口设置 TOPMOST：
    - 当前为 Foreground Window
    - 窗口可见（IsWindowVisible）
    - 窗口未最小化（not IsIconic）
    - 不是本游戏窗口
    - 当前不是 TOPMOST

    仅记录本次任务实际修改过的窗口，任务结束时统一恢复为非 TOPMOST。
    """

    def _init_topmost_mixin(self) -> None:
        self._topmost_stop_event = threading.Event()
        self._topmost_lock = threading.Lock()
        self._topmost_modified: set[int] = set()
        self._topmost_thread: threading.Thread | None = None
        self._topmost_prev_fg: int = 0

    # ── 公开 API ──────────────────────────────────────────────

    def start_topmost_monitor(self) -> None:
        """启动 TOPMOST 监测线程。重复调用安全（已运行则忽略）。"""
        if self._topmost_thread is not None and self._topmost_thread.is_alive():
            return
        self._topmost_stop_event.clear()
        self._topmost_prev_fg = 0
        t = threading.Thread(
            target=self._topmost_monitor_loop,
            name="topmost-monitor",
            daemon=True,
        )
        self._topmost_thread = t
        t.start()

    def stop_topmost_monitor(self) -> None:
        """停止监测并恢复所有被本机制修改过的窗口。

        可安全重复调用。放在 try/finally 或 on_destroy 中均可靠。
        """
        self._topmost_stop_event.set()
        thread = self._topmost_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._topmost_thread = None
        self._restore_all_modified()

    def on_destroy(self) -> None:
        """框架销毁回调：安全兜底，确保监测线程和窗口状态被清理。"""
        self.stop_topmost_monitor()
        super().on_destroy()

    # ── 内部实现 ──────────────────────────────────────────────

    def _topmost_monitor_loop(self) -> None:
        """后台轮询线程：检测前台窗口变化并按需设置 TOPMOST。"""
        while not self._topmost_stop_event.is_set():
            try:
                self._topmost_check_foreground()
            except Exception as exc:
                logger.debug("topmost monitor 异常: %s", exc)
            self._topmost_stop_event.wait(timeout=0.2)

    def _topmost_check_foreground(self) -> None:
        """检测当前前台窗口，符合条件则设置 TOPMOST。"""
        try:
            fg = win32gui.GetForegroundWindow()
        except Exception:
            return

        if not fg or fg == self._topmost_prev_fg:
            return
        self._topmost_prev_fg = fg

        # 跳过游戏窗口
        try:
            game_hwnd = self.get_game_hwnd()
        except Exception:
            game_hwnd = 0
        if fg == game_hwnd:
            return

        # 跳过不可见或已最小化的窗口
        try:
            if not win32gui.IsWindowVisible(fg) or win32gui.IsIconic(fg):
                return
        except Exception:
            return

        # 已经是 TOPMOST 的窗口不修改、不记录
        if _is_window_topmost(fg):
            return

        if _set_window_topmost(fg):
            with self._topmost_lock:
                self._topmost_modified.add(fg)

    def _restore_all_modified(self) -> None:
        """将所有被本机制修改过的窗口恢复为非 TOPMOST。"""
        with self._topmost_lock:
            to_restore = list(self._topmost_modified)
            self._topmost_modified.clear()
        self._topmost_prev_fg = 0

        for hwnd in to_restore:
            try:
                if win32gui.IsWindow(hwnd):
                    _remove_window_topmost(hwnd)
            except Exception:
                pass
