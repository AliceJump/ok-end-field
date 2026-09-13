"""浮层文字绘制：GDI 回退路径在真实内存 DC 上的渲染验证。

Qt 路径靠窗口自绘，无法在测试里断言像素；GDI 路径（headless 回退）可以直接画到
内存 DC 再回读像素，因此这里用它来锁住「底板 + 文字确实被画出来」的行为。
"""

import ctypes
import os
import time
import unittest

import numpy as np

from src.core.base_mixin.window_arrow_drawing_mixin import ArrowSpec, GdiArrowPainter, TextSpec

WIDTH, HEIGHT = 640, 240

# 文字块锚点 (0.02, 0.12)，三行；箭头画在右下角，两个区域互不重叠
TEXT_ROWS, TEXT_COLS = slice(24, 90), slice(0, 320)
ARROW_ROWS, ARROW_COLS = slice(108, 180), slice(352, 512)

BRIGHT_THRESHOLD = 200


class _FakeOverlay:
    """只提供 GdiArrowPainter 需要的几何与重绘入口。"""

    _width = WIDTH
    _height = HEIGHT

    def __init__(self):
        self.render_requests = 0

    def _schedule_render(self):
        self.render_requests += 1


def _text_spec(**overrides):
    params = {
        "text_key": "target_info",
        "lines": ["晶锥天使", "距离 12.3 · 方位 东北", "高度 上方 4.3"],
        "x_norm": 0.02,
        "y_norm": 0.12,
        "color": (255, 255, 255),
        "alpha": 235,
        "font_size_norm": 0.024,
        "line_spacing": 1.35,
        "panel_alpha": 150,
    }
    params.update(overrides)
    return TextSpec(**params)


@unittest.skipUnless(os.name == "nt", "GDI 叠层仅在 Windows 可用")
class TestGdiOverlayTextRendering(unittest.TestCase):
    def setUp(self):
        from ok.ui.overlay.win32_gdi import (
            BI_RGB,
            BITMAPINFO,
            BITMAPINFOHEADER,
            DIB_RGB_COLORS,
            TRANSPARENT,
            GdiCanvas,
            gdi32,
            user32,
        )

        self.gdi32 = gdi32
        self.user32 = user32
        self.canvas_cls = GdiCanvas

        self.screen_dc = user32.GetDC(None)
        self.memory_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = WIDTH
        info.bmiHeader.biHeight = -HEIGHT  # top-down BGRA
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(
            self.memory_dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        self.old_bitmap = gdi32.SelectObject(self.memory_dc, self.bitmap)
        gdi32.SetBkMode(self.memory_dc, TRANSPARENT)

        buffer = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte)), shape=(HEIGHT * WIDTH * 4,))
        self.pixels = buffer.reshape((HEIGHT, WIDTH, 4))

        self.overlay = _FakeOverlay()
        self.painter = GdiArrowPainter(self.overlay)
        self.canvas = GdiCanvas(self.memory_dc, 1.0)

    def tearDown(self):
        self.gdi32.SelectObject(self.memory_dc, self.old_bitmap)
        self.gdi32.DeleteObject(self.bitmap)
        self.gdi32.DeleteDC(self.memory_dc)
        self.user32.ReleaseDC(None, self.screen_dc)

    # --- helpers ---
    def _clear(self):
        self.pixels.fill(0)

    def _paint(self):
        self.painter.paint(self.canvas, self.overlay)

    def _region(self, rows, cols):
        return self.pixels[rows, cols, :3]

    @staticmethod
    def _non_black(region):
        return int(np.any(region != 0, axis=2).sum())

    @staticmethod
    def _bright(region):
        return int((region.max(axis=2) > BRIGHT_THRESHOLD).sum())

    # --- tests ---
    def test_paint_does_nothing_without_content(self):
        self._paint()
        self.assertEqual(self._non_black(self.pixels[:, :, :3]), 0)

    def test_text_paints_panel_and_glyphs(self):
        self.painter.add_text(_text_spec())
        self._paint()

        region = self._region(TEXT_ROWS, TEXT_COLS)
        self.assertGreater(self._non_black(region), 0, "文字区域应被绘制")
        self.assertGreater(self._bright(region), 0, "应画出白色字形而不只是底板")
        self.assertEqual(self._non_black(self._region(ARROW_ROWS, ARROW_COLS)), 0, "未添加箭头时不应画箭头")

    def test_panel_colour_is_not_pure_black(self):
        """GDI 叠层把纯黑像素当作透明，底板必须是非纯黑的深色，否则整块文字不可见。"""
        self.painter.add_text(_text_spec())
        self._paint()

        region = self._region(TEXT_ROWS, TEXT_COLS)
        colors, counts = np.unique(region.reshape(-1, 3), axis=0, return_counts=True)
        keep = np.any(colors != 0, axis=1)
        self.assertTrue(keep.any(), "应存在非纯黑像素（纯黑在叠层里会变成透明）")

        dominant = colors[keep][int(np.argmax(counts[keep]))]
        # panel_alpha=150 时底板色是「PANEL_COLOR 与中性底色按 alpha 混合」的结果
        np.testing.assert_array_equal(dominant, np.array(GdiArrowPainter.gdi_panel_color(150)))
        self.assertTrue(np.any(dominant != 0), "底板色不能是纯黑")

    def test_panel_alpha_zero_skips_the_panel_entirely(self):
        """panel_alpha=0 时不应画底板——该路径画不出半透明，画了反而是一块不透明方块。

        不能用「是否存在等于底板色的像素」判定：文字与箭头都走抗锯齿，中间色会
        零星撞上底板色。底板是一整块填充矩形，画了就一定是区域内的主色，
        因此这里判主色（与 test_panel_colour_is_not_pure_black 对称）。
        """
        self.painter.add_text(_text_spec(panel_alpha=0))
        self._paint()

        region = self._region(TEXT_ROWS, TEXT_COLS)
        colors, counts = np.unique(region.reshape(-1, 3), axis=0, return_counts=True)
        keep = np.any(colors != 0, axis=1)
        self.assertTrue(keep.any(), "应存在非纯黑像素")

        panel_colour = np.array(GdiArrowPainter.gdi_panel_color(150))
        dominant = colors[keep][int(np.argmax(counts[keep]))]
        self.assertFalse(
            np.array_equal(dominant, panel_colour),
            f"panel_alpha=0 时底板色不应成为区域主色，实际主色为 {dominant.tolist()}",
        )
        # 字形仍然要画出来
        self.assertGreater(self._bright(region), 0)

    def test_text_alpha_zero_skips_glyphs_but_keeps_the_panel(self):
        self.painter.add_text(_text_spec(alpha=0))
        self._paint()

        region = self._region(TEXT_ROWS, TEXT_COLS)
        self.assertGreater(self._non_black(region), 0, "文字透明时仍应绘制底板")
        self.assertEqual(self._bright(region), 0, "alpha=0 时不应绘制字形")

    def test_text_color_uses_opaque_alpha_blending(self):
        opaque = GdiArrowPainter.gdi_text_color((255, 128, 64), 255)
        self.assertEqual(opaque, (255, 128, 64))

        blended = GdiArrowPainter.gdi_text_color((255, 128, 64), 128)
        expected = tuple(
            round(channel * (128 / 255) + bg * (1 - 128 / 255))
            for channel, bg in zip((255, 128, 64), GdiArrowPainter.PANEL_BLEND_BG, strict=True)
        )
        self.assertEqual(blended, expected)

    def test_panel_darkness_follows_alpha(self):
        """GDI 无逐像素 alpha，用混合色近似：alpha 越低颜色越浅，且永远不会是纯黑。"""
        opaque = np.array(GdiArrowPainter.gdi_panel_color(255))
        np.testing.assert_array_equal(opaque, np.array(GdiArrowPainter.PANEL_COLOR))

        light = np.array(GdiArrowPainter.gdi_panel_color(40))
        self.assertTrue(np.all(light > opaque), "低透明度应得到更浅的底板色")
        for alpha in (1, 40, 150, 255):
            with self.subTest(alpha=alpha):
                self.assertTrue(np.any(np.array(GdiArrowPainter.gdi_panel_color(alpha)) != 0), "不能是纯黑")

    def test_panel_alpha_is_reflected_in_the_painted_pixels(self):
        self.painter.add_text(_text_spec(panel_alpha=40))
        self._paint()

        region = self._region(TEXT_ROWS, TEXT_COLS)
        colors, counts = np.unique(region.reshape(-1, 3), axis=0, return_counts=True)
        keep = np.any(colors != 0, axis=1)
        dominant = colors[keep][int(np.argmax(counts[keep]))]
        np.testing.assert_array_equal(dominant, np.array(GdiArrowPainter.gdi_panel_color(40)))
        self.assertFalse(
            np.array_equal(dominant, np.array(GdiArrowPainter.gdi_panel_color(150))),
            "不同 panel_alpha 必须画出不同深浅的底板",
        )

    def test_arrows_and_text_are_painted_in_the_same_pass(self):
        self.painter.add_arrow(ArrowSpec("default", 0.60, 0.70, 0.75, 0.50, (0, 255, 0), 0.005, None))
        self.painter.add_text(_text_spec())
        self._paint()

        self.assertGreater(self._non_black(self._region(TEXT_ROWS, TEXT_COLS)), 0)
        self.assertGreater(self._non_black(self._region(ARROW_ROWS, ARROW_COLS)), 0)

    def test_text_expires_when_not_refreshed(self):
        self.painter.add_text(_text_spec())
        # 把时间戳推到 FRESH_SECONDS 之前，模拟停止刷新
        stale = time.monotonic() - GdiArrowPainter.FRESH_SECONDS - 1
        self.painter._texts["target_info"] = (self.painter._texts["target_info"][0], stale)

        self._paint()

        self.assertEqual(self.painter._texts, {})
        self.assertEqual(self._non_black(self.pixels[:, :, :3]), 0)

    def test_clear_texts_keeps_arrows(self):
        self.painter.add_arrow(ArrowSpec("default", 0.60, 0.70, 0.75, 0.50, (0, 255, 0), 0.005, None))
        self.painter.add_text(_text_spec())

        self.painter.clear_texts()
        self._paint()

        self.assertEqual(self._non_black(self._region(TEXT_ROWS, TEXT_COLS)), 0)
        self.assertGreater(self._non_black(self._region(ARROW_ROWS, ARROW_COLS)), 0)

    def test_clear_all_removes_everything(self):
        self.painter.add_arrow(ArrowSpec("default", 0.60, 0.70, 0.75, 0.50, (0, 255, 0), 0.005, None))
        self.painter.add_text(_text_spec())

        self.painter.clear_all()
        self._paint()

        self.assertEqual(self._non_black(self.pixels[:, :, :3]), 0)

    def test_same_key_keeps_only_the_latest_text(self):
        self.painter.add_text(_text_spec(lines=["旧"]))
        self.painter.add_text(_text_spec(lines=["新"]))

        self.assertEqual(len(self.painter._texts), 1)
        self.assertEqual(self.painter._texts["target_info"][0].lines, ["新"])

    def test_blank_lines_are_skipped(self):
        self.painter.add_text(_text_spec(lines=["", "只有一行", ""]))
        self._paint()
        self.assertGreater(self._non_black(self._region(TEXT_ROWS, TEXT_COLS)), 0)

    def test_render_is_requested_on_update(self):
        before = self.overlay.render_requests
        self.painter.add_text(_text_spec())
        self.assertGreater(self.overlay.render_requests, before)


class TestQtOverlayTextRendering(unittest.TestCase):
    """Qt 路径的像素验证：离屏把文字块画到 QImage，不依赖游戏窗口与前台状态。"""

    SIZE = (400, 200)

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _render(self, spec):
        from PySide6.QtGui import QColor, QImage, QPainter

        from src.core.base_mixin.window_arrow_drawing_mixin import WindowArrowOverlay

        # hwnd=0 时 _sync_geometry 会直接返回，不需要真实窗口
        overlay = WindowArrowOverlay(0)
        overlay._sync_timer.stop()
        try:
            image = QImage(self.SIZE[0], self.SIZE[1], QImage.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            overlay._paint_text(painter, spec, *self.SIZE)
            painter.end()
        finally:
            overlay.deleteLater()
        return image

    @staticmethod
    def _alpha(image):
        buffer = np.frombuffer(image.constBits(), dtype=np.uint8)
        return buffer.reshape(image.height(), image.width(), 4)[:, :, 3]

    def test_text_paints_opaque_pixels(self):
        image = self._render(_text_spec())
        self.assertGreater(int((self._alpha(image) > 0).sum()), 0, "文字块应画出像素")

    def test_panel_is_semi_transparent_and_anchored_at_requested_position(self):
        image = self._render(_text_spec())

        # 锚点 (0.02, 0.12) → (8, 24)，底板 alpha 应为 panel_alpha(150)
        panel = image.pixelColor(10, 30)
        self.assertEqual(panel.alpha(), 150)
        self.assertEqual((panel.red(), panel.green(), panel.blue()), (0, 0, 0))

        # 底板左侧/上方之外应保持全透明
        self.assertEqual(image.pixelColor(2, 30).alpha(), 0)
        self.assertEqual(image.pixelColor(10, 5).alpha(), 0)

    def test_panel_alpha_zero_still_draws_glyphs(self):
        image = self._render(_text_spec(panel_alpha=0))
        self.assertGreater(int((self._alpha(image) > 0).sum()), 0)

    def test_empty_lines_draw_nothing(self):
        image = self._render(_text_spec(lines=["", ""]))
        self.assertEqual(int((self._alpha(image) > 0).sum()), 0)


if __name__ == "__main__":
    unittest.main()
