"""自定义图标模块"""
import hashlib
import logging
import os
import re
import threading
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QMovie, QPixmap
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIconBase, Theme, isDarkTheme

_ICONS_DIR = Path("assets") / "ui" / "material_icons"
# 反转图标缓存目录（根级 cache/ 已在 .gitignore 中忽略）
_CACHE_DIR = Path("cache") / "icons"
_CACHE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)

_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _invert_hex_color(match):
    """把 #RGB / #RGBA / #RRGGBB / #RRGGBBAA 的颜色反转，alpha 保持不变。"""
    hex_str = match.group(0)[1:]
    if len(hex_str) in (3, 4):
        channels = [int(c, 16) for c in hex_str[:3]]
        inv = "".join(f"{15 - c:x}" for c in channels)
        alpha = hex_str[3] if len(hex_str) == 4 else ""
        return f"#{inv}{alpha}"
    r, g, b = (int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
    alpha = hex_str[6:8] if len(hex_str) == 8 else ""
    return f"#{255 - r:02x}{255 - g:02x}{255 - b:02x}{alpha}"


def _invert_svg_text(text: str) -> str:
    """反转 SVG 文本中所有十六进制颜色（如 #000000 -> #ffffff）。"""
    return _HEX_COLOR_RE.sub(_invert_hex_color, text)


def _invert_rgba_frame(frame):
    """反转 RGBA 图像的 RGB 通道，保留 alpha。"""
    from PIL import Image, ImageChops

    r, g, b, a = frame.split()
    inv = ImageChops.invert(Image.merge("RGB", (r, g, b)))
    return Image.merge("RGBA", (*inv.split(), a))


def _invert_static(source_path: str, target_path: str):
    """反转静态 PNG 等单帧图像。"""
    from PIL import Image

    img = Image.open(source_path).convert("RGBA")
    fmt = Path(source_path).suffix.lstrip(".").upper() or "PNG"
    _invert_rgba_frame(img).save(target_path, format=fmt)


def _invert_gif(source_path: str, target_path: str):
    """逐帧反转 GIF，保留透明度、每帧时长与循环次数。"""
    from PIL import Image

    src = Image.open(source_path)
    loop = src.info.get("loop", 0)
    frames, durations = [], []
    try:
        while True:
            frames.append(_invert_rgba_frame(src.convert("RGBA")))
            durations.append(src.info.get("duration", 100))
            src.seek(src.tell() + 1)
    except EOFError:
        pass
    if not frames:
        return
    frames[0].save(
        target_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=loop,
        disposal=2,
    )


def _invert_to(source_path: str, target_path: str, suffix: str):
    """按后缀把源文件颜色反转后写入 target_path。"""
    if suffix == ".svg":
        Path(target_path).write_text(
            _invert_svg_text(Path(source_path).read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    elif suffix == ".gif":
        _invert_gif(source_path, target_path)
    else:
        _invert_static(source_path, target_path)


def _inverted_cache_path(source_path: str, suffix: str) -> str:
    """返回源文件颜色反转后的缓存路径；不存在或过期时即时生成。

    - 缓存文件名为 ``{源名}_inv_{源路径md5前8位}{后缀}``，避免不同目录同名冲突。
    - 源文件 mtime 新于缓存时自动重建（先写 .tmp 再原子替换）。
    - GIF 反转代价高，写入磁盘缓存后二次加载可“秒开”。
    """
    src = Path(source_path)
    if not src.exists():
        logger.warning("图标反转源文件不存在: %s", src)
        return str(src)
    key = hashlib.md5(str(src.resolve()).encode("utf-8")).hexdigest()[:8]
    cache_path = _CACHE_DIR / f"{src.stem}_inv_{key}{suffix}"
    if not cache_path.exists() or cache_path.stat().st_mtime < src.stat().st_mtime:
        with _CACHE_LOCK:
            if not cache_path.exists() or cache_path.stat().st_mtime < src.stat().st_mtime:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp_path = cache_path.with_name(cache_path.name + ".tmp")
                _invert_to(str(src), str(tmp_path), suffix)
                os.replace(tmp_path, cache_path)
                logger.info("生成反转图标缓存: %s -> %s", src.name, cache_path.name)
    return str(cache_path)


def _draw_scaled_pixmap(painter, rect, pixmap):
    """把 pixmap 等比缩放后居中绘制到 rect 中。"""
    if pixmap is None or pixmap.isNull():
        return
    target = QRectF(rect).toRect()
    if target.isEmpty():
        return
    scaled = pixmap.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    x = target.x() + (target.width() - scaled.width()) // 2
    y = target.y() + (target.height() - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)


class _GifPlayer(QObject):
    """单个 GIF 文件的播放器。

    持有 QMovie 负责逐帧推进；凡是绘制过该 GIF 的控件都会被注册进
    ``_widgets``（弱引用），帧变化时统一调用 ``update()`` 触发重绘，
    从而让所有宿主（设置卡片、导航项等）的图标同步动起来。
    """

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self._movie = QMovie(self.path, parent=self)
        self._movie.setCacheMode(QMovie.CacheAll)
        self._widgets = weakref.WeakSet()
        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.start()

    @property
    def movie(self) -> QMovie:
        return self._movie

    def register(self, widget):
        """记录一个正在绘制本 GIF 的控件（弱引用，控件销毁后自动清理）。"""
        if isinstance(widget, QWidget):
            self._widgets.add(widget)

    def current_pixmap(self) -> QPixmap:
        return self._movie.currentPixmap()

    def start(self):
        if self._movie.state() != QMovie.Running:
            self._movie.start()

    def stop(self):
        self._movie.stop()

    def _on_frame_changed(self, _frame):
        for widget in list(self._widgets):
            widget.update()


class _GifIconEngine(QIconEngine):
    """用 QMovie 当前帧绘制的图标引擎（供 ``QIcon`` 路径使用）。"""

    def __init__(self, player: _GifPlayer):
        super().__init__()
        self._player = player

    def paint(self, painter, rect, mode, state):
        self._player.register(painter.device())
        if mode == QIcon.Disabled:
            painter.setOpacity(0.5)
        elif mode == QIcon.Selected:
            painter.setOpacity(0.7)
        _draw_scaled_pixmap(painter, rect, self._player.current_pixmap())

    def pixmap(self, size, mode, state):
        pm = self._player.current_pixmap()
        if pm.isNull():
            return QPixmap()
        return pm.scaled(QSize(size.width(), size.height()), Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def clone(self):
        return _GifIconEngine(self._player)


class GifIcon(FluentIconBase):
    """单个 GIF 文件组成的动图图标。

    渲染时自动播放动画，并驱动所有绘制它的控件（设置卡片、导航项等）跟随重绘，
    无需额外接入补丁。文件缺失时静默退化为空白。
    """

    def __init__(self, path: str):
        self._path = str(path)
        self._player = _GifPlayer(self._path)

    def path(self, theme=Theme.AUTO):
        return self._path

    def icon(self, theme=Theme.AUTO, color=None):
        return QIcon(_GifIconEngine(self._player))

    def render(self, painter, rect, theme=Theme.AUTO, indexes=None, **attributes):
        self._player.register(painter.device())
        _draw_scaled_pixmap(painter, rect, self._player.current_pixmap())

    def start(self):
        self._player.start()

    def stop(self):
        self._player.stop()


class ThemeIcon(FluentIconBase):
    """根据主题自动切换的图标，dark/light 指图标文件本身的主题色。

    只传其中一个时，另一个自动取其“颜色反转”版本（写入 cache/icons/ 缓存）。
    """

    def __init__(
        self,
        light_icon: str | None = None,
        dark_icon: str | None = None,
        *,
        suffix: str,
        base_path: Path = _ICONS_DIR,
    ):
        """
        Args:
            light_icon: 浅色主题图标文件名；省略时取 dark_icon 的颜色反转
            dark_icon: 深色主题图标文件名；省略时取 light_icon 的颜色反转
            suffix: 图标文件后缀名，允许使用 ".png"、".svg"、".gif"
            base_path: 图标文件所在目录，默认为常量 _ICONS_DIR
        """
        assert suffix in (".png", ".svg", ".gif"), "suffix must be .png, .svg or .gif"
        if light_icon is None and dark_icon is None:
            raise ValueError("ThemeIcon requires at least one of light_icon / dark_icon")
        self._is_gif = suffix == ".gif"
        if light_icon is not None:
            self._light_path = str(base_path / f"{light_icon}{suffix}")
        else:
            self._light_path = _inverted_cache_path(str(base_path / f"{dark_icon}{suffix}"), suffix)
        if dark_icon is not None:
            self._dark_path = str(base_path / f"{dark_icon}{suffix}")
        else:
            self._dark_path = _inverted_cache_path(str(base_path / f"{light_icon}{suffix}"), suffix)
        if self._is_gif:
            self._light_gif = GifIcon(self._light_path)
            self._dark_gif = GifIcon(self._dark_path)

    def _is_dark(self, theme):
        if theme == Theme.AUTO:
            return isDarkTheme()
        return theme == Theme.DARK

    def path(self, theme=Theme.AUTO):
        return self._dark_path if self._is_dark(theme) else self._light_path

    def _gif_for(self, theme):
        return self._dark_gif if self._is_dark(theme) else self._light_gif

    def icon(self, theme=Theme.AUTO, color=None):
        if self._is_gif:
            return self._gif_for(theme).icon(theme, color)
        return super().icon(theme, color)

    def render(self, painter, rect, theme=Theme.AUTO, indexes=None, **attributes):
        if self._is_gif:
            self._gif_for(theme).render(painter, rect, theme, indexes, **attributes)
        else:
            super().render(painter, rect, theme, indexes, **attributes)


class Icons:
    """自定义图标类

    - 静态图标：``ThemeIcon("xxx_black", "xxx_white", suffix=".svg")``
    - 动图图标：``ThemeIcon("xxx_black", "xxx_white", suffix=".gif")``
      浅色/深色主题各放一个 GIF 文件（如 ``xxx_black.gif``、``xxx_white.gif``），
      渲染时自动播放动画并跟随主题切换。
    - 单变体自动反转：只传一个（如 ``ThemeIcon("xxx_black", suffix=".gif")``），
      另一个变体自动生成“颜色反转”版本并缓存到 ``cache/icons/``，GIF 二次加载秒开。
    - 单文件动图：``GifIcon("assets/ui/material_icons/xxx.gif")``
    """

    Battle = ThemeIcon("swords_black", "swords_white", suffix=".svg")
    Keyboard = ThemeIcon("keyboard_alt_black", "keyboard_alt_white", suffix=".svg")
    Zipline = ThemeIcon("diagonal_line_black", "diagonal_line_white", suffix=".svg")
    Interact = ThemeIcon("touch_app_black", "touch_app_white", suffix=".svg")
    Collect = ThemeIcon("approval_delegation_black", "approval_delegation_white", suffix=".svg")
    Navigation = ThemeIcon("explore_black", "explore_white", suffix=".svg")
    Fetch = ThemeIcon("box_add_black", "box_add_white", suffix=".svg")
    Deliver = ThemeIcon("delivery_truck_speed_black", "delivery_truck_speed_white", suffix=".svg")
    ItemTransfer = ThemeIcon("folder_match_black", "folder_match_white", suffix=".svg")
    SwordChallenge = ThemeIcon("playing_cards_black", "playing_cards_white", suffix=".svg")
    YingTuo = ThemeIcon("crown_black", "crown_white", suffix=".svg")
