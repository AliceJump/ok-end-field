"""让已生成的遮挡在空闲时保持显示。

ok 框架的模糊遮挡只在「有新捕获帧」时刷新，且一旦窗口可见性变化或捕获停止，
``BlurOverlayProcessor`` 会主动发出 ``clear_blur_overlay`` 把遮挡撤掉：任务结束、
切出游戏、长时间空闲之后，被遮住的那些固定界面元素就重新露出来了。

本补丁把清理条件收窄成一条：**只有功能被关掉时才撤掉遮挡**。

- ``next_frame``：没有新帧只表示这一轮不刷新，不再顺带清空；
- ``set_visible``：窗口短暂不可见时保留上一次的补丁（叠加窗本身会跟着隐藏，
  不需要清数据）；
- ``_active``：只跟随功能开关，不再因窗口不可见而丢弃补丁；
- ``Win32GdiOverlay.update_overlay``：窗口不可见时框架会清空 ``blur_images``，
  这里在清空后把上一次的补丁放回去，恢复可见即可立即继续显示，无需等待新帧。

补丁幂等；任何异常都退回框架原行为。
"""

from __future__ import annotations

_PATCH_INSTALLED = False


def _snapshot_blur_images(overlay) -> list:
    """取一份补丁快照，供窗口不可见时恢复。"""
    return list(getattr(overlay, "blur_images", None) or ())


def _restore_blur_images_if_cleared(overlay, saved: list) -> bool:
    """框架刚把补丁清空时把快照放回去；返回是否恢复。"""
    if not saved:
        return False
    if getattr(overlay, "blur_images", None):
        return False
    overlay.blur_images = saved
    return True


def install_overlay_blur_persist_patch():
    """让遮挡在空闲/窗口不可见时保持，只在功能关闭时才撤掉。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.overlay import Win32GdiOverlay
    from ok.util.blur import BlurOverlayProcessor

    original_next_frame = BlurOverlayProcessor.next_frame
    original_update_overlay = Win32GdiOverlay.update_overlay

    def next_frame(self, frame):
        """功能关掉才清；只是没有新帧或窗口不可见时保留上一次的遮挡。"""
        if not self.enabled():
            self._clear_overlay()
            return
        if not self.visible:
            return
        original_next_frame(self, frame)

    def set_visible(self, visible, *args):
        """窗口可见性变化只记录状态，不丢弃已有遮挡。"""
        visible = bool(visible)
        if self.visible == visible:
            return
        self.visible = visible

    def _active(self):
        """只跟随功能开关：窗口不可见不会让已生成的补丁失效。"""
        return self.enabled()

    def update_overlay(self, visible, *args, **kwargs):
        """窗口不可见时框架会清空补丁，清空后把上一次的放回去。"""
        saved = _snapshot_blur_images(self)
        original_update_overlay(self, visible, *args, **kwargs)
        _restore_blur_images_if_cleared(self, saved)

    BlurOverlayProcessor.next_frame = next_frame
    BlurOverlayProcessor.set_visible = set_visible
    BlurOverlayProcessor._active = _active
    Win32GdiOverlay.update_overlay = update_overlay

    _PATCH_INSTALLED = True
