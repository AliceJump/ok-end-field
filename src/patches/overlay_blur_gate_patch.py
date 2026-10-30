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
Alt+右键的坐标拾取。任何异常都退回框架原行为。
"""

from __future__ import annotations

import contextlib

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

        owner.overlay_window = Win32GdiOverlay(
            _source_hwnd(),
            exit_event=getattr(owner, "exit_event", None),
        )
        communicate.window.connect(owner.overlay_window.update_overlay)
        # 关键：boxes 仍严格跟随 Debug 开关，模糊生效不等于要画调试框。
        owner.overlay_window.set_boxes_enabled(use_overlay_enabled(owner))
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
        if not should_create_overlay(self):
            return
        overlay = _ensure_overlay(self)
        if overlay is not None:
            overlay.set_boxes_enabled(use_overlay_enabled(self))

    App.get_overlay_view = patched_get_overlay_view
    HeadlessApp.get_overlay_view = patched_get_overlay_view
    App.update_overlay = patched_update_overlay
    _OverlayConfigMixin.initialize_overlay = patched_initialize_overlay

    _PATCH_INSTALLED = True
