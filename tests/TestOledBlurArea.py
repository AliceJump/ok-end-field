"""OLED 模糊遮罩的区域回调、叠加窗门禁与遮挡保持。

ok 框架只在项目配置提供 ``blur_area`` 回调时才注册「启用模糊 / 模糊算法 /
模糊间隔」这三个基础选项，并把回调返回的矩形用前景叠加窗盖在游戏画面上。
这里锁定三件事：

1. 区域回调的契约，避免以后改区域时把「比例 → 像素」算错；
2. 叠加窗门禁——只开「启用模糊」也要能把窗口建出来（否则遮挡 UID 实际不生效），
   但窗口内的调试框仍严格跟随 Debug 开关；
3. 遮挡保持——任务触发过一次之后，空闲或窗口短暂不可见都不应把遮挡撤掉。
"""

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np
from ok import App, og
from ok.feature.Box import Box

from src.config import OLED_BLUR_RECTS, blur_area, config
from src.patches.overlay_blur_gate_patch import blur_masking_requested, should_create_overlay
from src.patches.overlay_blur_persist_patch import (
    _restore_blur_images_if_cleared,
    _snapshot_blur_images,
    install_overlay_blur_persist_patch,
)


class TestOledBlurArea(unittest.TestCase):
    def test_config_exposes_the_callback(self):
        self.assertIs(config["blur_area"], blur_area)

    def test_callback_returns_boxes_inside_the_frame(self):
        boxes = blur_area(1920, 1080)
        self.assertEqual(len(boxes), len(OLED_BLUR_RECTS))
        for box in boxes:
            self.assertIsInstance(box, Box)
            self.assertGreaterEqual(box.x, 0)
            self.assertGreaterEqual(box.y, 0)
            self.assertLessEqual(box.x + box.width, 1920)
            self.assertLessEqual(box.y + box.height, 1080)

    def test_uid_region_is_the_bottom_left_corner(self):
        box = blur_area(1920, 1080)[0]
        self.assertGreater(box.y, 1080 * 0.9)
        self.assertLess(box.x, 1920 * 0.2)
        self.assertGreater(box.width, 0)
        self.assertGreater(box.height, 0)

    def test_region_scales_with_resolution(self):
        small = blur_area(1280, 720)[0]
        large = blur_area(2560, 1440)[0]
        self.assertAlmostEqual(small.x * 2, large.x, delta=2)
        self.assertAlmostEqual(small.width * 2, large.width, delta=2)
        self.assertAlmostEqual(small.y * 2, large.y, delta=2)

    def test_invalid_size_returns_empty(self):
        self.assertEqual(blur_area(0, 0), [])
        self.assertEqual(blur_area(-10, 1080), [])


class TestOverlayBlurGate(unittest.TestCase):
    """只开「启用模糊」时也要建出叠加窗，但调试框仍跟随 Debug 开关。"""

    @classmethod
    def setUpClass(cls):
        from src.patches.overlay_blur_gate_patch import install_overlay_blur_gate_patch

        install_overlay_blur_gate_patch()

    @staticmethod
    def _executor(enable_blur=True, has_blur_area=True):
        return SimpleNamespace(
            blur_overlay_processor=object() if has_blur_area else None,
            basic_options={"Enable Blur": enable_blur},
        )

    @staticmethod
    def _owner(use_overlay=False):
        return SimpleNamespace(
            ok_config={"use_overlay": use_overlay},
            overlay_window=None,
            exit_event=None,
            sync_overlay_source=lambda: None,
        )

    def test_blur_not_requested_without_executor(self):
        with mock.patch.object(og, "executor", None):
            self.assertFalse(blur_masking_requested())

    def test_blur_not_requested_without_blur_area(self):
        with mock.patch.object(og, "executor", self._executor(has_blur_area=False)):
            self.assertFalse(blur_masking_requested())

    def test_blur_not_requested_when_enable_blur_off(self):
        with mock.patch.object(og, "executor", self._executor(enable_blur=False)):
            self.assertFalse(blur_masking_requested())

    def test_blur_requested_when_configured_and_enabled(self):
        with mock.patch.object(og, "executor", self._executor()):
            self.assertTrue(blur_masking_requested())

    def test_overlay_allowed_for_blur_only(self):
        with mock.patch.object(og, "executor", self._executor()):
            self.assertTrue(should_create_overlay(self._owner(use_overlay=False)))

    def test_overlay_allowed_for_debug_switch(self):
        with mock.patch.object(og, "executor", None):
            self.assertTrue(should_create_overlay(self._owner(use_overlay=True)))

    def test_overlay_denied_when_neither(self):
        with mock.patch.object(og, "executor", None):
            self.assertFalse(should_create_overlay(self._owner(use_overlay=False)))

    def test_get_overlay_view_returns_none_when_denied(self):
        with mock.patch.object(og, "executor", None):
            self.assertIsNone(App.get_overlay_view(self._owner(use_overlay=False)))

    def test_blur_only_creates_window_with_boxes_disabled(self):
        with (
            mock.patch.object(og, "executor", self._executor()),
            mock.patch("ok.ui.overlay.Win32GdiOverlay") as overlay_cls,
        ):
            owner = self._owner(use_overlay=False)
            view = App.get_overlay_view(owner)
            self.assertIs(view, overlay_cls.return_value)
            overlay_cls.return_value.set_boxes_enabled.assert_called_once_with(False)

    def test_debug_switch_creates_window_with_boxes_enabled(self):
        with (
            mock.patch.object(og, "executor", None),
            mock.patch("ok.ui.overlay.Win32GdiOverlay") as overlay_cls,
        ):
            App.get_overlay_view(self._owner(use_overlay=True))
            overlay_cls.return_value.set_boxes_enabled.assert_called_once_with(True)

    def test_install_is_idempotent(self):
        from src.patches.overlay_blur_gate_patch import install_overlay_blur_gate_patch

        view_before = App.get_overlay_view
        install_overlay_blur_gate_patch()
        self.assertIs(App.get_overlay_view, view_before)


class TestOverlayBlurPersist(unittest.TestCase):
    """任务触发过一次遮挡后，空闲或窗口不可见都不应把它撤掉。"""

    @classmethod
    def setUpClass(cls):
        install_overlay_blur_persist_patch()

    def _processor(self, enabled=True):
        from ok.util.blur import BlurOverlayProcessor

        state = {"enabled": enabled}
        processor = BlurOverlayProcessor(
            blur_area=lambda width, height: [],
            enabled=lambda: state["enabled"],
            emit_patches=mock.Mock(),
            clear_patches=mock.Mock(),
            exit_event=threading.Event(),
            algorithm=lambda: "Blur",
            interval=lambda: 1,
        )
        self.addCleanup(processor.exit_event.set)
        return processor, state

    @staticmethod
    def _frame():
        return np.zeros((8, 8, 3), dtype=np.uint8)

    def test_hiding_window_keeps_existing_patches(self):
        processor, _ = self._processor()
        processor.visible = True
        processor.overlay_active = True
        processor.set_visible(False)
        processor.clear_patches.assert_not_called()

    def test_next_frame_without_visible_keeps_patches(self):
        processor, _ = self._processor()
        processor.visible = False
        processor.overlay_active = True
        processor.next_frame(self._frame())
        processor.clear_patches.assert_not_called()

    def test_disabling_the_feature_still_clears_patches(self):
        processor, state = self._processor()
        state["enabled"] = False
        processor.visible = True
        processor.overlay_active = True
        processor.next_frame(self._frame())
        processor.clear_patches.assert_called_once()

    def test_active_follows_feature_switch_not_visibility(self):
        processor, _ = self._processor()
        processor.visible = False
        self.assertTrue(processor._active())

    def test_snapshot_and_restore_round_trip(self):
        overlay = SimpleNamespace(blur_images=[(0, 0, 4, 4, "patch")])
        saved = _snapshot_blur_images(overlay)
        overlay.blur_images = []
        self.assertTrue(_restore_blur_images_if_cleared(overlay, saved))
        self.assertEqual(overlay.blur_images, [(0, 0, 4, 4, "patch")])

    def test_restore_keeps_newer_patches(self):
        overlay = SimpleNamespace(blur_images=["newer"])
        self.assertFalse(_restore_blur_images_if_cleared(overlay, ["older"]))
        self.assertEqual(overlay.blur_images, ["newer"])

    def test_restore_without_snapshot_is_noop(self):
        overlay = SimpleNamespace(blur_images=[])
        self.assertFalse(_restore_blur_images_if_cleared(overlay, []))
        self.assertEqual(overlay.blur_images, [])

    def test_install_is_idempotent(self):
        from ok.ui.overlay import Win32GdiOverlay

        before = Win32GdiOverlay.update_overlay
        install_overlay_blur_persist_patch()
        self.assertIs(Win32GdiOverlay.update_overlay, before)


if __name__ == "__main__":
    unittest.main()
