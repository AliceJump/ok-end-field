"""自定义图标模块"""
import re
import tempfile
from enum import Enum
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import FluentIconBase, Theme, isDarkTheme

_ICONS_DIR = Path("assets") / "icons"


def _get_icon_path(name: str) -> str:
    """获取图标文件路径"""
    return str(_ICONS_DIR / f"{name}.svg")


def load_svg_icon(name: str, color: str = None) -> QIcon:
    """
    加载 SVG 图标，可选自定义颜色

    Args:
        name: 图标名称 (不含 .svg 后缀)
        color: 可选颜色，如 '#FF5722' 或 'red'

    Returns:
        QIcon 对象
    """
    path = _get_icon_path(name)
    if not Path(path).exists():
        return QIcon()

    if color is None:
        return QIcon(path)

    renderer = QSvgRenderer(path)
    pixmap = QPixmap(renderer.defaultSize())
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()

    return QIcon(pixmap)


def load_png_icon(name: str) -> QIcon:
    """
    加载 PNG 图标

    Args:
        name: 图标名称 (不含 .png 后缀)

    Returns:
        QIcon 对象
    """
    path = str(_ICONS_DIR / f"{name}.png")
    if not Path(path).exists():
        return QIcon()
    return QIcon(path)


def load_theme_png_icon(light_name: str, dark_name: str) -> QIcon:
    """
    根据当前主题加载对应的 PNG 图标

    Args:
        light_name: 浅色主题图标名称 (不含 .png 后缀)
        dark_name: 深色主题图标名称 (不含 .png 后缀)

    Returns:
        QIcon 对象
    """
    if isDarkTheme():
        name = dark_name
    else:
        name = light_name
    path = str(_ICONS_DIR / f"{name}.png")
    if not Path(path).exists():
        return QIcon()
    return QIcon(path)


class ThemeSvgIcon(FluentIconBase):
    """根据主题自动切换的 SVG 图标，传入深色图标名，浅色主题自动内存生成反色版本"""

    def __init__(self, dark_icon: str):
        """
        Args:
            dark_icon: 深色主题 SVG 图标文件名 (不含 .svg 后缀)
        """
        self._dark_path = str(_ICONS_DIR / f"{dark_icon}.svg")
        self._inverted_path: str | None = None

    def path(self, theme=Theme.AUTO):
        if theme == Theme.AUTO:
            is_dark = isDarkTheme()
        else:
            is_dark = theme == Theme.DARK
        if is_dark:
            return self._dark_path
        if self._inverted_path is None:
            self._inverted_path = self._invert_svg_colors(self._dark_path)
        return self._inverted_path

    @staticmethod
    def _invert_svg_colors(svg_path: str) -> str:
        """读取 SVG 文件，反转所有十六进制颜色值，写入临时文件并返回路径"""
        with open(svg_path, "r", encoding="utf-8") as f:
            content = f.read()

        def _invert_hex(m: re.Match) -> str:
            hex_str = m.group(1)
            length = len(hex_str)
            if length in (3, 4):
                r, g, b = (int(c * 2, 16) for c in hex_str[:3])
                r, g, b = 255 - r, 255 - g, 255 - b
                inverted = f"{r:02x}{g:02x}{b:02x}"
                if length == 4:
                    inverted += hex_str[3] * 2
            else:
                r, g, b = (int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
                r, g, b = 255 - r, 255 - g, 255 - b
                inverted = f"{r:02x}{g:02x}{b:02x}"
                if length == 8:
                    inverted += hex_str[6:8]
            return f"#{inverted}"

        inverted_content = re.sub(r"#([0-9a-fA-F]{3,8})", _invert_hex, content)

        tmp = tempfile.NamedTemporaryFile(
            suffix=".svg", prefix="battle_light_", delete=False
        )
        tmp.write(inverted_content.encode("utf-8"))
        tmp.close()
        return tmp.name


class ThemePngIcon(FluentIconBase):
    """根据主题自动切换的 PNG 图标，dark/light 指图标文件本身的主题色"""

    def __init__(self, light_icon: str, dark_icon: str):
        """
        Args:
            light_icon: 浅色图标文件名 (不含 .png 后缀)
            dark_icon: 深色图标文件名 (不含 .png 后缀)
        """
        self._light_path = str(_ICONS_DIR / f"{light_icon}.png")
        self._dark_path = str(_ICONS_DIR / f"{dark_icon}.png")

    def path(self, theme=Theme.AUTO):
        if theme == Theme.AUTO:
            is_dark = isDarkTheme()
        else:
            is_dark = theme == Theme.DARK
        return self._dark_path if is_dark else self._light_path


# ===== 预定义图标 =====
class Icons:
    BATTLE = ThemeSvgIcon("battle")
    DELIVERY = ThemePngIcon("delivery_dark", "delivery_light")
    ItemNavigator = ThemePngIcon("itemNavigator_dark", "itemNavigator_light")
