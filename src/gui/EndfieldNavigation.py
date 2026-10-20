"""Navigation panel: ok-ef brand block, square items, a signal block on the current workspace."""

from typing import Any

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt  # type: ignore
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QImage, QPainter  # type: ignore
from qfluentwidgets import Theme, qconfig  # type: ignore
from qfluentwidgets.components.navigation.navigation_widget import (  # type: ignore
    NavigationPushButton,
    NavigationWidget,
)

from src.gui.EndfieldPrimitives import ThemeRepaint, cut_path, cut_polygon, draw_fluent_icon
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import field_font, ui_font

NAV_ITEM_HEIGHT = 46
BRAND_HEIGHT = 64
BRAND_ICON = 34


def _dark() -> bool:
    return qconfig.theme == Theme.DARK


def _code_font() -> QFont:
    return field_font(10, label=True)


def paint_navigation_button(self: Any, _event: Any) -> None:
    """Square hover; the current item is a signal block with a cut corner and black icon and text."""
    colors = palette(_dark())
    painter = QPainter(self)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    painter.setPen(Qt.NoPen)
    if not self.isEnabled():
        painter.setOpacity(0.4)
    margins = self._margins()
    left_margin, right_margin = margins.left(), margins.right()
    selected = self._canDrawIndicator()
    under_cursor = self.isEnter and QRect(self.mapToGlobal(QPoint()), self.size()).contains(QCursor.pos())
    hover = (under_cursor or self.isAboutSelected) and self.isEnabled()
    if selected:
        painter.setBrush(QColor(colors["signal"]))
        painter.drawPolygon(cut_polygon(self.width(), self.height(), 7))
        ink = colors["on_signal"]
    else:
        if hover:
            painter.fillRect(self.rect(), QColor(colors["hover"]))
        ink = colors["text"] if hover else colors["muted"]
    icon = self._icon if hasattr(self._icon, "render") else self.icon()
    draw_fluent_icon(painter, icon, QRectF(11.5 + left_margin, (self.height() - 16) / 2, 16, 16), ink)
    if self.isCompacted:
        painter.end()
        return
    left = 44 + left_margin if not self.icon().isNull() else left_margin + 16
    width = self.width() - 13 - left - right_margin
    code = getattr(self, "_endfield_code", "")
    painter.setPen(QColor(ink))
    if code:
        painter.setFont(_code_font())
        painter.drawText(QRectF(left, 6, width, 13), Qt.AlignLeft | Qt.AlignVCenter, code)
        painter.setFont(ui_font(14, QFont.Bold if selected else QFont.Medium))
        painter.drawText(QRectF(left, 19, width, self.height() - 25), Qt.AlignLeft | Qt.AlignVCenter, self.text())
    else:
        painter.setFont(self.font())
        painter.drawText(QRectF(left, 0, width, self.height()), Qt.AlignVCenter, self.text())
    painter.end()


def install_navigation_paint() -> None:
    if getattr(NavigationPushButton.paintEvent, "_endfield_patched", False):
        return
    paint_navigation_button._endfield_patched = True  # type: ignore[attr-defined]
    NavigationPushButton.paintEvent = paint_navigation_button


def style_navigation_item(item: Any, code: str) -> None:
    """Two-line workspace item: a numbered English code above the Chinese name."""
    button = getattr(item, "itemWidget", item)
    button._endfield_code = code
    for widget in {item, button}:
        original = widget.setCompacted

        def set_compacted(compacted: bool, widget: Any = widget, original: Any = original) -> None:
            original(compacted)
            widget.setFixedHeight(NAV_ITEM_HEIGHT)

        widget.setCompacted = set_compacted
        widget.setFixedHeight(NAV_ITEM_HEIGHT)
    original_width = getattr(item, "suitableWidth", None)
    if callable(original_width):
        code_width = QFontMetrics(_code_font()).horizontalAdvance(code)
        item.suitableWidth = lambda: max(original_width(), 44 + code_width + 24)


class EndfieldNavBrand(ThemeRepaint, NavigationWidget):
    """ok-ef window icon with the product name and version; only the icon shows when compact."""

    def __init__(self, icon: QIcon, version: str, parent: Any = None):
        super().__init__(isSelectable=False, parent=parent)
        self._icon = icon
        self._version = version or ""
        self._icon_cache: tuple[tuple, QImage] | None = None
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip(f"OK-EF {self._version}".strip())
        self.setFixedSize(40, BRAND_HEIGHT)
        qconfig.themeChanged.connect(self._repaint)

    def setCompacted(self, isCompacted: bool):
        self.isCompacted = isCompacted
        self.setFixedSize(40 if isCompacted else self.EXPAND_WIDTH, BRAND_HEIGHT)
        self.update()

    # 品牌区只展示，不响应点击，也不弹出导航菜单
    def mousePressEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def _cut_icon(self, size: int, ratio: float) -> QImage:
        key = (size, ratio, self._icon.cacheKey())
        if self._icon_cache is None or self._icon_cache[0] != key:
            pixels = round(size * ratio)
            image = QImage(pixels, pixels, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            painter.fillPath(cut_path(pixels, pixels, round(7 * ratio)), QColor("white"))
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            source = (
                self._icon.pixmap(QSize(pixels, pixels))
                .toImage()
                .scaled(pixels, pixels, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            )
            source.setDevicePixelRatio(1)
            painter.drawImage(0, 0, source)
            painter.end()
            image.setDevicePixelRatio(ratio)
            self._icon_cache = (key, image)
        return self._icon_cache[1]

    def paintEvent(self, event):
        colors = palette(_dark())
        size = 28 if self.isCompacted else BRAND_ICON
        x = (self.width() - size) // 2 if self.isCompacted else 6
        y = (self.height() - size) // 2
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        painter.drawImage(QPoint(x, y), self._cut_icon(size, self.devicePixelRatioF()))
        if not self.isCompacted:
            left = x + size + 10
            name = field_font(15, label=True)
            name.setVariableAxis(QFont.Tag("wght"), 800.0)
            painter.setFont(name)
            painter.setPen(QColor(colors["text"]))
            painter.drawText(QRectF(left, y - 1, self.width() - left, 19), Qt.AlignLeft | Qt.AlignVCenter, "OK-EF")
            painter.setFont(field_font(12))
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(
                QRectF(left, y + 18, self.width() - left, 16), Qt.AlignLeft | Qt.AlignVCenter, self._version
            )
        painter.end()
