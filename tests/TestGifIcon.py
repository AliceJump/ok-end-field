# -*- coding: utf-8 -*-
"""GIF 动图图标支持测试：GifIcon / ThemeIcon(.gif) 的渲染与帧推进、缺失变体颜色反转缓存。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, Theme
from qfluentwidgets.components.widgets.icon_widget import IconWidget

import src.icons as icons_module
from src.icons import GifIcon, ThemeIcon


def _make_gif(path: Path, n=3):
    frames = []
    for i in range(n):
        img = Image.new("RGBA", (32, 32), (40 * i, 80 * i, 255 - 40 * i, 255))
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
    return str(path)


def _make_solid_gif(path: Path, color, n=2):
    frames = []
    for i in range(n):
        img = Image.new("RGBA", (16, 16), (*color, 255))
        if i:
            img.putpixel((0, 0), (0, 0, 0, 255))  # 让各帧有差异，避免 Pillow 保存时去重
        frames.append(img)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
    return str(path)


class GifIconTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp(prefix="gif_icon_"))
        cls.single_path = _make_gif(cls.tmp / "single.gif")
        cls.light_path = _make_gif(cls.tmp / "light.gif")
        cls.dark_path = _make_gif(cls.tmp / "dark.gif")

    def test_gif_icon_path_and_icon(self):
        icon = GifIcon(self.single_path)
        self.assertEqual(icon.path(), self.single_path)
        qicon = icon.icon()
        self.assertFalse(qicon.isNull())
        # 引擎图标没有预置尺寸，但可通过 pixmap() 取当前帧
        pm = qicon.pixmap(32, 32)
        self.assertFalse(pm.isNull())
        self.assertEqual(pm.width(), 32)
        self.assertEqual(pm.height(), 32)

    def test_icon_widget_registers_and_animates(self):
        icon = GifIcon(self.single_path)
        widget = IconWidget(icon)
        widget.setFixedSize(32, 32)
        widget.show()
        self.app.processEvents()
        pm = widget.grab()  # 强制同步渲染，触发 render() 把控件注册进播放器
        self.assertFalse(pm.isNull())
        self.assertIn(widget, icon._player._widgets)

        # 事件循环跑一会，验证帧确实在推进（frameChanged 至少触发过 1 次）
        loop = QEventLoop()
        counter = {"n": 0}
        icon._player.movie.frameChanged.connect(lambda _f: counter.__setitem__("n", counter["n"] + 1))
        QTimer.singleShot(250, loop.quit)
        loop.exec()
        self.assertGreater(counter["n"], 0)
        widget.close()

    def test_theme_icon_gif(self):
        icon = ThemeIcon("light", "dark", suffix=".gif", base_path=self.tmp)
        self.assertTrue(icon.path().endswith("light.gif") or icon.path().endswith("dark.gif"))
        qicon = icon.icon()
        self.assertFalse(qicon.isNull())
        # 主题图标应能正常挂到 IconWidget 上
        widget = IconWidget(icon)
        widget.setFixedSize(32, 32)
        widget.show()
        self.app.processEvents()
        widget.grab()
        self.assertTrue(any(widget in player._widgets for player in
                            (icon._light_gif._player, icon._dark_gif._player)))
        widget.close()

    def test_theme_icon_static_suffixes_still_work(self):
        # png/svg 走原有渲染路径，不应被 GIF 逻辑影响
        png = ThemeIcon("a", "b", suffix=".png", base_path=self.tmp)
        self.assertFalse(png._is_gif)
        self.assertEqual(png.path(Theme.LIGHT), str(self.tmp / "a.png"))
        self.assertEqual(png.path(Theme.DARK), str(self.tmp / "b.png"))

    def test_theme_icon_rejects_bad_suffix(self):
        with self.assertRaises(AssertionError):
            ThemeIcon("a", "b", suffix=".webp")

    def test_missing_file_degrades_gracefully(self):
        icon = GifIcon(str(self.tmp / "not_exists.gif"))
        qicon = icon.icon()
        # 文件缺失时引擎仍在（QIcon 非空），但当前帧为空，且不会崩溃
        self.assertFalse(qicon.isNull())
        self.assertTrue(icon._player.current_pixmap().isNull())
        self.assertEqual(qicon.pixmap(32, 32).isNull(), True)


class ThemeIconInvertTestCase(unittest.TestCase):
    """缺失变体自动取颜色反转版本，并写入 cache/icons 磁盘缓存。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = Path(tempfile.mkdtemp(prefix="icon_invert_"))
        cls.red_gif = _make_solid_gif(cls.tmp / "red.gif", (255, 0, 0))
        cls.red_png = cls.tmp / "red.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(cls.red_png)
        cls.black_svg = cls.tmp / "black.svg"
        cls.black_svg.write_text(
            '<svg width="10" height="10" viewBox="0 0 10 10" fill="#000000">'
            '<path d="M0 0h10v10z"/></svg>',
            encoding="utf-8",
        )

    def setUp(self):
        # 每个用例独立缓存目录，隔离测试
        self.cache = Path(tempfile.mkdtemp(prefix="inv_cache_"))
        self._orig_cache = icons_module._CACHE_DIR
        icons_module._CACHE_DIR = self.cache

    def tearDown(self):
        icons_module._CACHE_DIR = self._orig_cache

    def test_gif_missing_dark_derives_inverted(self):
        icon = ThemeIcon("red", suffix=".gif", base_path=self.tmp)
        # dark 变体指向缓存目录中的反转 GIF，且文件已生成
        self.assertTrue(icon._dark_path.startswith(str(self.cache)))
        self.assertNotEqual(icon._dark_path, self.red_gif)
        self.assertTrue(Path(icon._dark_path).exists())
        # 是合法多帧动图，且颜色已反转：红色 -> 青色(0,255,255)，透明度保持
        im = Image.open(icon._dark_path)
        self.assertGreater(im.n_frames, 1)
        im.seek(0)
        px = im.convert("RGBA").getpixel((8, 8))
        self.assertEqual(px[3], 255)
        self.assertLessEqual(abs(px[0] - 0), 8)
        self.assertGreaterEqual(px[1], 247)
        self.assertGreaterEqual(px[2], 247)

    def test_light_can_be_derived(self):
        icon = ThemeIcon(dark_icon="red", suffix=".gif", base_path=self.tmp)
        self.assertTrue(icon._light_path.startswith(str(self.cache)))
        self.assertEqual(icon._dark_path, self.red_gif)

    def test_gif_cache_reused(self):
        icon1 = ThemeIcon("red", suffix=".gif", base_path=self.tmp)
        icon2 = ThemeIcon("red", suffix=".gif", base_path=self.tmp)
        self.assertEqual(icon1._dark_path, icon2._dark_path)
        # 缓存只生成一份
        cache_files = list(self.cache.glob("*"))
        self.assertEqual(len(cache_files), 1)
        self.assertEqual(cache_files[0].suffix, ".gif")

    def test_svg_inversion(self):
        icon = ThemeIcon("black", suffix=".svg", base_path=self.tmp)
        self.assertNotEqual(icon._dark_path, str(self.black_svg))
        text = Path(icon._dark_path).read_text(encoding="utf-8")
        self.assertIn("#ffffff", text)
        self.assertNotIn("#000000", text)

    def test_png_inversion_keeps_alpha(self):
        icon = ThemeIcon("red", suffix=".png", base_path=self.tmp)
        out = Image.open(icon._dark_path).convert("RGBA")
        center = out.getpixel((8, 8))
        self.assertLessEqual(abs(center[0] - 0), 8)
        self.assertGreaterEqual(center[1], 247)
        self.assertEqual(center[3], 255)

    def test_both_provided_no_cache(self):
        icon = ThemeIcon("a", "b", suffix=".png", base_path=self.tmp)
        self.assertEqual(icon._light_path, str(self.tmp / "a.png"))
        self.assertEqual(icon._dark_path, str(self.tmp / "b.png"))
        self.assertEqual(list(self.cache.glob("*")), [])

    def test_neither_provided_raises(self):
        with self.assertRaises(ValueError):
            ThemeIcon(suffix=".png", base_path=self.tmp)

    def test_missing_source_warns_and_falls_back(self):
        # 源文件缺失：不崩溃，路径回落到源路径（渲染为空）
        icon = ThemeIcon("nope", suffix=".png", base_path=self.tmp)
        self.assertEqual(icon._dark_path, str(self.tmp / "nope.png"))
        self.assertEqual(list(self.cache.glob("*")), [])

    def test_cache_invalidated_when_source_newer(self):
        src = self.tmp / "blue.png"
        Image.new("RGBA", (16, 16), (0, 0, 255, 255)).save(src)
        icon1 = ThemeIcon("blue", suffix=".png", base_path=self.tmp)
        # 源文件比缓存更新：同名缓存被重建，内容变为“蓝 -> 黄(255,255,0)”
        time.sleep(0.01)
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(src)
        os.utime(src, None)
        icon2 = ThemeIcon("blue", suffix=".png", base_path=self.tmp)
        self.assertEqual(icon1._dark_path, icon2._dark_path)
        # 缓存已重建：源变红 -> 反转后为青色(0,255,255)
        out = Image.open(icon2._dark_path).convert("RGBA").getpixel((8, 8))
        self.assertLessEqual(abs(out[0] - 0), 8)
        self.assertGreaterEqual(out[1], 247)
        self.assertGreaterEqual(out[2], 247)


if __name__ == "__main__":
    unittest.main()
