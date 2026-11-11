"""让「启用模糊」不再依赖调试用的叠加层开关。

ok 框架的前景叠加窗有一道硬门禁：``ok_config['use_overlay']``（开始页 Debug 卡片里的
「Enable Boxes」）为假时，``get_overlay_view()`` 直接返回 ``None``，``update_overlay()``
还会主动关掉已有窗口。框架注释写明这是刻意设计——即使项目配置了 ``blur_area``
回调，也不能凭它复活一个被关掉的窗口。

后果是「OLED 遮挡 UID」实际不可用：``基础选项 → Enable Blur`` 打开后，
``BlurOverlayProcessor`` 会照常算出补丁并通过 ``communicate.blur_overlay`` 发出，
但没有任何叠加窗接收，屏幕上什么都看不到。

本补丁把「创建窗口」的条件放宽为 ``use_overlay`` **或**「模糊已开启」，而窗口内的
``boxes`` 仍严格跟随 ``use_overlay``：只开模糊时不会顺带画调试框，也不会启动
Alt+右键的坐标拾取。另外补上「运行中才打开模糊」的补丁信号处理，避免必须重启
才能看到遮挡。任何异常都退回框架原行为。

注：ok-script **v2.0.20 起上游已自行修复这道门禁**（``_OverlayConfigMixin._overlay_needed``
与 ``_on_blur_patches``）。本补丁是给当前 pin 的 ``2.0.7b1`` 做的向后移植，仓库把
ok-script 升级到 2.0.20 之后应当整体移除。
"""

from __future__ import annotations

import contextlib

from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False


def _config_bool(holder, name, fallback=False) -> bool:
    """从 Config 风格的持有者上安全读一个布尔值。"""
    getter = getattr(holder, "get", None)
    if not callable(getter):
        return fallback
    try:
        return bool(getter(name, fallback))
    except Exception:
        return fallback


def use_overlay_enabled(owner) -> bool:
    """``owner`` 的 ``ok_config`` 是否打开了叠加层（Debug 开关）。"""
    return _config_bool(getattr(owner, "ok_config", None), "use_overlay", False)


def blur_masking_requested() -> bool:
    """项目是否同时满足「配置了 blur_area」与「基础选项里开启了模糊」。

    两个条件都从运行期读取，用户在设置里开关模糊后立即生效：
    - ``blur_overlay_processor`` 只在项目配置提供可调用的 ``blur_area`` 时才存在；
    - ``Enable Blur`` 是「基础选项」里的模糊总开关。
    """
    try:
        from ok import og
    except Exception:
        return False

    executor = getattr(og, "executor", None)
    if executor is None:
        return False
    if getattr(executor, "blur_overlay_processor", None) is None:
        return False
    return _config_bool(getattr(executor, "basic_options", None), "Enable Blur", False)


def should_create_overlay(owner) -> bool:
    """是否允许存在前景叠加窗：Debug 开关打开，或模糊遮挡需要它。"""
    return use_overlay_enabled(owner) or blur_masking_requested()


def _source_hwnd():
    """当前捕获窗口，可能为 None（游戏还没连上）。"""
    try:
        from ok import og
    except Exception:
        return None
    device_manager = getattr(og, "device_manager", None)
    return getattr(device_manager, "hwnd_window", None)


def install_overlay_blur_gate_patch():
    """放宽叠加窗的创建门禁，使模糊遮挡不再依赖 Debug 开关。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import App, HeadlessApp, _OverlayConfigMixin

    def _ensure_overlay(owner):
        if owner.overlay_window is not None:
            return owner.overlay_window
        from ok.core.events import communicate
        from ok.ui.overlay import Win32GdiOverlay

        # 构造失败必须是「这次没有遮挡」而不是「启动失败」：initialize_overlay 位于
        # OK.start() 的启动路径上，异常会一路冒到主流程。
        try:
            overlay = Win32GdiOverlay(
                _source_hwnd(),
                exit_event=getattr(owner, "exit_event", None),
            )
            communicate.window.connect(overlay.update_overlay)
            # 关键：boxes 仍严格跟随 Debug 开关，模糊生效不等于要画调试框。
            overlay.set_boxes_enabled(use_overlay_enabled(owner))
        except Exception as exc:
            logger.warning(f"overlay blur: 无法创建叠加窗，本次运行退回无遮挡：{exc}")
            return None
        owner.overlay_window = overlay
        # 建窗时游戏窗口可能还没连上，补一次几何播种；连上了就是幂等的。
        sync = getattr(owner, "sync_overlay_source", None)
        if callable(sync):
            with contextlib.suppress(Exception):
                sync()
        return owner.overlay_window

    def patched_get_overlay_view(self):
        if not should_create_overlay(self):
            return None
        return _ensure_overlay(self)

    def patched_update_overlay(self, *args, **kwargs):
        if not should_create_overlay(self):
            if getattr(self, "overlay_window", None) is not None:
                self._close_overlay(wait=False)
            return None
        overlay_view = getattr(self, "overlay_window", None)
        if overlay_view is None:
            overlay_view = _ensure_overlay(self)
        if overlay_view:
            overlay_view.update_overlay(*args, **kwargs)
        return None

    def patched_initialize_overlay(self):
        """启动时按持久化状态初始化；模糊开启时也要把窗口建出来。"""
        _connect_blur_signals(self)
        if not should_create_overlay(self):
            return
        overlay = _ensure_overlay(self)
        if overlay is not None:
            overlay.set_boxes_enabled(use_overlay_enabled(self))

    def _connect_blur_signals(owner):
        """挂上模糊补丁信号，处理「运行中才打开模糊」的情况。

        用户在设置里开启模糊时，窗口几何事件不会再触发，窗口还没建出来就收不到
        补丁。这里在收到第一批补丁时补建窗口并把当前补丁直接交给它。
        """
        from ok.core.events import communicate

        if getattr(owner, "_blur_gate_listener", None) is not None:
            return

        def on_blur_patches(patches):
            if getattr(owner, "overlay_window", None) is not None:
                return
            if not should_create_overlay(owner):
                return
            overlay = _ensure_overlay(owner)
            if overlay is not None:
                overlay.update_blur_patches(patches)

        def on_clear_blur_patches():
            if not should_create_overlay(owner) and getattr(owner, "overlay_window", None) is not None:
                owner._close_overlay(wait=False)

        owner._blur_gate_listener = on_blur_patches
        owner._blur_gate_clear_listener = on_clear_blur_patches
        communicate.blur_overlay.connect(on_blur_patches)
        communicate.clear_blur_overlay.connect(on_clear_blur_patches)

    App.get_overlay_view = patched_get_overlay_view
    HeadlessApp.get_overlay_view = patched_get_overlay_view
    App.update_overlay = patched_update_overlay
    _OverlayConfigMixin.initialize_overlay = patched_initialize_overlay

    _PATCH_INSTALLED = True
