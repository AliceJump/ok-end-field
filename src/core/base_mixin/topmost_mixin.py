"""临时 Windows TOPMOST 窗口置顶机制。

任务 ``run()`` 启动时自动监测前台窗口，对符合条件的非游戏窗口临时设置
HWND_TOPMOST，任务销毁时统一恢复为非 TOPMOST。

无需手动调用，Mixin 在 ``run()`` 中自动启动监测，
``disable()`` / ``on_destroy()`` 中自动停止并恢复。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import functools
import threading

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
_user32.SetWindowPos.argtypes = (
    ctypes.wintypes.HWND,
    ctypes.wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.wintypes.UINT,
)
_user32.SetWindowPos.restype = ctypes.wintypes.BOOL

# Windows 系统窗口类名——这些窗口不应被置顶
_SYSTEM_CLASS_NAMES: frozenset[str] = frozenset(
    {
        "Shell_TrayWnd",  # 任务栏
        "Shell_SecondaryTrayWnd",  # 多显示器副任务栏
        "Progman",  # 桌面
        "WorkerW",  # 桌面壁纸宿主
        "SysListView32",  # 桌面图标列表（资源管理器子窗口）
        "SHELLDLL_DefView",  # 桌面视图
        "Windows.UI.Core.CoreWindow",  # UWP 系统 UI（开始菜单/Cortana 等）
        "Shell_InputPanel_Host",  # 托盘输入法面板
        "IME",  # 输入法窗口
        "MSCTFIME UI",  # 输入法 UI
        "NVIDIA GeForce Overlay",  # NVIDIA 覆盖层
        "TextInputHost",  # Windows 输入体验
        "Program Manager",  # Program Manager 桌面管理器
        "NotifyIconOverflowWindow",  # 系统托盘溢出窗口
        "tooltips_class32",  # 系统 Tooltip 弹出窗口
        "ToolbarWindow32",  # 工具栏弹出窗口
    }
)


def _is_system_window(hwnd: int) -> bool:
    """判断窗口是否属于 Windows 系统级窗口（任务栏、桌面等）。"""
    try:
        cls = win32gui.GetClassName(hwnd)
        return cls in _SYSTEM_CLASS_NAMES
    except Exception:
        return False


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
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
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
                hwnd,
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
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

    # 触发式任务的 monitor 延迟启动阈值（秒）
    # run() 执行超过此时间才启动 monitor，避免触发式任务的快速扫描 cycle 反复启停
    _TOPMOST_START_DELAY: float = 2.0

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # 自动包装子类的 run()，使其运行期间自动启动/停止 TOPMOST 监测
        if "run" in cls.__dict__:
            original_run = cls.run

            @functools.wraps(original_run)
            def _wrapped_run(self_inner: TopmostMixin, *args: object, **kw: object) -> object:
                # 仅当 monitor 尚未运行时才启动（避免嵌套子类重复启停）
                already_running = (
                    getattr(self_inner, "_topmost_thread", None) is not None and self_inner._topmost_thread.is_alive()
                )  # type: ignore[union-attr]
                if already_running:
                    return original_run(self_inner, *args, **kw)

                # 延迟启动：等 _TOPMOST_START_DELAY 秒后再启动 monitor，
                # 若 run() 在延迟期内返回则取消启动（适用于触发式任务的快速扫描 cycle）
                delay_timer: threading.Timer | None = None
                timer_fired = threading.Event()

                def _delayed_start() -> None:
                    timer_fired.set()
                    try:
                        self_inner.start_topmost_monitor()
                    except Exception:
                        pass

                delay_timer = threading.Timer(self_inner._TOPMOST_START_DELAY, _delayed_start)
                delay_timer.daemon = True
                delay_timer.start()

                try:
                    return original_run(self_inner, *args, **kw)
                finally:
                    # 取消延迟 timer（若尚未触发）
                    if delay_timer is not None:
                        delay_timer.cancel()
                    # 等待 timer 线程结束，避免竞态
                    delay_timer.join(timeout=1.0) if delay_timer is not None else None
                    # 仅在 monitor 已启动时才停止（快速扫描 cycle 不会启动，此处为 no-op）
                    if timer_fired.is_set():
                        self_inner.stop_topmost_monitor()

            cls.run = _wrapped_run  # type: ignore[attr-defined]

    def _init_topmost_mixin(self) -> None:
        self._topmost_stop_event = threading.Event()
        self._topmost_lock = threading.Lock()
        self._topmost_modified: set[int] = set()
        self._topmost_thread: threading.Thread | None = None
        self._topmost_prev_fg: int = 0

    # ── 生命周期 ────────────────────────────────────────────

    def on_create(self) -> None:
        """任务框架初始化：仅初始化状态，不启动监测线程。"""
        self._init_topmost_mixin()
        super().on_create()

    def on_destroy(self) -> None:
        """框架销毁回调：安全兜底，确保监测线程和窗口状态被清理。"""
        self.stop_topmost_monitor()
        super().on_destroy()

    def disable(self) -> None:
        """任务禁用时停止监测（一次性任务 run() 结束后自动调用）。"""
        self.stop_topmost_monitor()
        super().disable()

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
        logger.info("topmost monitor 已启动")

    def stop_topmost_monitor(self) -> None:
        """停止监测并恢复所有被本机制修改过的窗口。

        可安全重复调用。放在 try/finally 或 on_destroy 中均可靠。
        """
        self._topmost_stop_event.set()
        thread = self._topmost_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        if thread is None or not thread.is_alive():
            self._topmost_thread = None
        self._restore_all_modified()

    # ── 内部实现 ──────────────────────────────────────────────

    def _topmost_monitor_loop(self) -> None:
        """后台轮询线程：检测前台窗口变化并按需设置 TOPMOST。"""
        while not self._topmost_stop_event.is_set():
            try:
                self._topmost_check_foreground()
            except Exception as exc:
                logger.debug(f"topmost monitor 异常: {exc}")
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

        # 跳过 Windows 系统窗口（任务栏、桌面等）
        if _is_system_window(fg):
            return

        # 已经是 TOPMOST 的窗口不修改、不记录
        if _is_window_topmost(fg):
            return

        with self._topmost_lock:
            if self._topmost_stop_event.is_set() or not _set_window_topmost(fg):
                return
            self._topmost_modified.add(fg)

        try:
            cls_name = win32gui.GetClassName(fg)
            win_title = win32gui.GetWindowText(fg)
            logger.info(f"topmost 已置顶: hwnd=0x{fg:X}  class={cls_name}  title={win_title}")
        except Exception:
            logger.info(f"topmost 已置顶: hwnd=0x{fg:X}")

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
                    cls_name = win32gui.GetClassName(hwnd)
                    win_title = win32gui.GetWindowText(hwnd)
                    logger.info(f"topmost 已恢复: hwnd=0x{hwnd:X}  class={cls_name}  title={win_title}")
            except Exception:
                pass
        if to_restore:
            logger.info(f"topmost 恢复完成，共 {len(to_restore)} 个窗口")
