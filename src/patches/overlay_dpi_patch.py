# -*- coding: utf-8 -*-
"""修复 ok 框架 OverlayWindow.update_overlay 在混合 DPI 多屏下的偏移问题。

原因
----
ok 的 OverlayWindow.update_overlay 用 `setGeometry(x / scaling, y / scaling, ...)`
把物理像素除以该屏 DPR 当作 Qt 逻辑坐标。该假设只在主屏或所有屏幕 DPR 相同
时成立。当主屏 DPR != 副屏 DPR 时（例如主屏 200%、副屏 125%），Qt 报告的屏幕
逻辑 origin 并不等于 物理origin / 该屏DPR，导致 okscripts 标记框向 x 正方向
（右侧）偏移。实测副屏 125% 时偏移达 480px。

修复
----
改用 src.core.screen_coords.physical_rect_to_logical 做物理 -> 逻辑换算。
"""
from __future__ import annotations

from ok import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False


def install_overlay_dpi_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.gui.overlay.OverlayWindow import OverlayWindow
    from src.core.screen_coords import physical_rect_to_logical

    original_update_overlay = OverlayWindow.update_overlay

    def patched_update_overlay(self, visible, x, y, window_width, window_height, width, height, scaling):
        logger.debug(f'update_overlay: {visible}, {x}, {y}, {width}, {height}, {scaling}')
        self._source_visible = visible
        if visible:
            lx, ly, lw, lh = physical_rect_to_logical(x, y, width, height, dpr_hint=scaling)
            self.setGeometry(
                int(round(lx)),
                int(round(ly)),
                max(1, int(round(lw))),
                max(1, int(round(lh))),
            )
        else:
            self.clear_blur_patches()
        self.refresh_visibility()

    OverlayWindow.update_overlay = patched_update_overlay
    _PATCH_INSTALLED = True
    logger.debug('overlay_dpi_patch installed')
