"""Painted Endfield controls and surfaces shared by workspaces, task rows and the drawer."""

from typing import Any

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation  # type: ignore
from PySide6.QtGui import (  # type: ignore
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget  # type: ignore
from qfluentwidgets import FluentIcon, Theme, qconfig  # type: ignore

from src.gui.EndfieldTheme import TRI_STRIP, palette
from src.gui.EndfieldTypography import field_font, ui_font

# 黄色擦入：时长与缓动取 skill 的 --ef-dur-wipe / --ef-ease-mech
WIPE_MS = 280
MECH_CURVE = QEasingCurve(QEasingCurve.BezierSpline)
MECH_CURVE.addCubicBezierSegment(QPointF(0.7, 0), QPointF(0.2, 1), QPointF(1, 1))
KEYBOARD_REASONS = (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)


def ui_tr(text: str) -> str:
    """界面文字走项目 gettext 译文（i18n/*/ok.po）；应用对象还没建起来时原样返回。"""
    from ok import og

    return og.app.tr(text) if og.app else text


def dark_theme() -> bool:
    return qconfig.theme == Theme.DARK


def cut_polygon(width: float, height: float, cut: float, left: float = 0, top: float = 0) -> QPolygonF:
    """Rectangle with a 45° cut at the bottom-right; points sit on pixel edges so straight sides stay sharp."""
    right, bottom = left + width, top + height
    return QPolygonF(
        [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, bottom - cut),
            QPointF(right - cut, bottom),
            QPointF(left, bottom),
        ]
    )


def cut_path(width: float, height: float, cut: float) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(cut_polygon(width, height, cut))
    path.closeSubpath()
    return path


def draw_focus_ring(painter: QPainter, rect: QRectF, color: Any, inset: float = 3) -> None:
    """2px ring inside the control, drawn only for keyboard focus."""
    ring = QPainterPath()
    ring.setFillRule(Qt.OddEvenFill)
    ring.addRect(rect.adjusted(inset, inset, -inset, -inset))
    ring.addRect(rect.adjusted(inset + 2, inset + 2, -inset - 2, -inset - 2))
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(ring)
    painter.restore()


def draw_fluent_icon(painter: QPainter, icon: Any, rect: QRectF, ink: str, enabled: bool = True) -> None:
    """Render a Fluent icon in black or white to match the text drawn beside it."""
    painter.save()
    if not enabled:
        painter.setOpacity(0.45)
    if hasattr(icon, "render"):
        icon.render(painter, rect, Theme.LIGHT if QColor(ink).lightness() < 128 else Theme.DARK)
    elif isinstance(icon, QIcon) and not icon.isNull():
        icon.paint(painter, rect.toRect())
    painter.restore()


class ThemeRepaint:
    """Theme slot as a bound method, so Qt drops the connection when the widget is destroyed."""

    def _repaint(self, *_args):
        self.update()


class EndfieldElidedLabel(QLabel):
    """窄布局省略单行文字，保留完整文本和悬停提示。"""

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.WindowText))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width())
        painter.drawText(self.contentsRect(), self.alignment(), text)
        painter.end()


class EndfieldActionButton(QPushButton):
    """Native push button painted as a cut-corner chip; signal yellow wipes in from the left on hover."""

    def __init__(self, icon: Any, text: str, parent=None, *, primary: bool = False, chamfered: bool = True):
        super().__init__(text, parent)
        self.primary = primary
        self.chamfered = chamfered
        self._fluent_icon = icon
        self._keyboard_focus = False
        self._wipe = 0.0
        self._wipe_ani = QVariantAnimation(self)
        self._wipe_ani.setDuration(WIPE_MS)
        self._wipe_ani.setEasingCurve(MECH_CURVE)
        self._wipe_ani.valueChanged.connect(self._set_wipe)
        self.setCursor(Qt.PointingHandCursor)
        # 鼠标点击不抢焦点，焦点框只在键盘切换时出现
        self.setFocusPolicy(Qt.TabFocus)
        self.setMinimumHeight(32)
        self.setFont(ui_font(13, QFont.DemiBold))
        self.setIconSize(QSize(16, 16))
        if icon is not None:
            self.setIcon(icon.icon() if hasattr(icon, "icon") else icon)
        qconfig.themeChanged.connect(self._update_theme)

    def _update_theme(self, *_args):
        self.update()

    def _set_wipe(self, value):
        self._wipe = float(value)
        self.update()

    def _run_wipe(self, target: float):
        self._wipe_ani.stop()
        self._wipe_ani.setStartValue(self._wipe)
        self._wipe_ani.setEndValue(target)
        self._wipe_ani.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.isEnabled():
            self._run_wipe(1.0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._run_wipe(0.0)

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in KEYBOARD_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange and not self.isEnabled():
            self._wipe_ani.stop()
            self._wipe = 0.0

    def sizeHint(self):
        icon_width = self.iconSize().width() + 8 if not self.icon().isNull() else 0
        cut_room = 8 if self.chamfered else 0
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + icon_width + 32 + cut_room, 34)

    def paintEvent(self, event):
        colors = palette(dark_theme())
        base, ink = (colors["go"], colors["on_go"]) if self.primary else (colors["chip"], colors["text"])
        wipe = self._wipe
        if not self.isEnabled():
            base, ink, wipe = colors["disabled_bg"], colors["disabled_text"], 0.0
        elif self.isDown():
            base, ink, wipe = colors["primary_pressed"], colors["on_signal"], 0.0
        elif wipe >= 1.0:
            base, ink, wipe = colors["signal"], colors["on_signal"], 0.0
        elif wipe >= 0.35:
            ink = colors["on_signal"]
        w, h = self.width(), self.height()
        shape = cut_path(w, h, 7 if h <= 30 else 8) if self.chamfered else QPainterPath()
        if not self.chamfered:
            shape.addRect(0, 0, w, h)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(base))
        painter.drawPath(shape)
        if wipe > 0:
            band = QPainterPath()
            band.addRect(QRectF(0, 0, w * wipe, h))
            painter.setBrush(QColor(colors["signal"]))
            painter.drawPath(shape.intersected(band))
        icon_width = self.iconSize().width() + 8 if not self.icon().isNull() else 0
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        left = (w - text_width - icon_width) // 2 - (2 if self.chamfered else 0)
        if icon_width:
            size = self.iconSize()
            rect = QRectF(left, (h - size.height()) / 2, size.width(), size.height())
            draw_fluent_icon(painter, self._fluent_icon or self.icon(), rect, ink, self.isEnabled())
        painter.setPen(QColor(ink))
        painter.setFont(self.font())
        painter.drawText(QRectF(left + icon_width, 0, text_width + 2, h), Qt.AlignVCenter, self.text())
        if self.hasFocus() and self._keyboard_focus:
            draw_focus_ring(painter, QRectF(0, 0, w, h), ink)
        painter.end()


class EndfieldConfigButton(EndfieldActionButton):
    """Explicit config entry with the native expandable header's input contract."""

    def __init__(self, parent=None):
        super().__init__(FluentIcon.SETTING, ui_tr("配置"), parent)
        self.setFixedSize(76, 28)
        self.setExpand(False)

    def setExpand(self, expanded: bool):
        self.setText(ui_tr("配置"))
        self.setToolTip(ui_tr("关闭配置面板" if expanded else "打开配置面板"))
        self.setAccessibleName(self.toolTip())

    def setHover(self, _hover: bool):
        self.update()

    def setPressed(self, pressed: bool):
        self.setDown(pressed)


def paint_hatch(painter: QPainter, rect: QRectF, dark: bool) -> None:
    """-45° hatch: ink at 22%, 1.5px lines with a 5px gap, as in the skill's hatch token."""
    ink = QColor(palette(dark)["text"])
    ink.setAlphaF(0.22)
    painter.save()
    painter.setClipRect(rect)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(ink, 1.5))
    h = rect.height()
    x = rect.left() - h
    while x < rect.right():
        painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + h, rect.top()))
        x += 9.2
    painter.restore()


class EndfieldHeader(ThemeRepaint, QWidget):
    """Drawer header with a quiet hatch on its unused right edge."""

    def __init__(self, parent=None, *, hatched: bool = True):
        super().__init__(parent)
        self.hatched = hatched
        qconfig.themeChanged.connect(self._repaint)

    def paintEvent(self, event):
        if not self.hatched:
            return
        painter = QPainter(self)
        paint_hatch(painter, QRectF(max(0, self.width() - 140), 0, 140, self.height()), dark_theme())
        painter.end()


class EndfieldHatchBand(ThemeRepaint, QWidget):
    """Title-row band: a 14px ink block, then hatch to the end of the row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setMinimumWidth(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        qconfig.themeChanged.connect(self._repaint)

    def paintEvent(self, event):
        dark = dark_theme()
        painter = QPainter(self)
        painter.fillRect(0, 0, 14, self.height(), QColor(palette(dark)["text"]))
        paint_hatch(painter, QRectF(14, 0, self.width() - 14, self.height()), dark)
        painter.end()


class EndfieldPanelTab(ThemeRepaint, QWidget):
    """5px ink tab over a panel's top-left edge: 14% of its width, at least 36px."""

    def __init__(self, panel: QWidget):
        super().__init__(panel)
        self.setObjectName("panelTab")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        panel.installEventFilter(self)
        qconfig.themeChanged.connect(self._repaint)
        self._place()

    def _place(self):
        panel = self.parentWidget()
        self.setGeometry(0, 0, max(36, round(panel.width() * 0.14)), 5)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.ChildAdded):
            self._place()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette(dark_theme())["text"]))
        painter.end()


def attach_panel_tab(panel: QWidget) -> EndfieldPanelTab:
    tab = getattr(panel, "_endfield_panel_tab", None)
    if tab is None:
        tab = EndfieldPanelTab(panel)
        panel._endfield_panel_tab = tab
    return tab


class EndfieldTag(ThemeRepaint, QWidget):
    """Cut-corner tag: an ink number plate, or a chip that splits "key: value" into two cells."""

    def __init__(self, text: str, parent=None, *, ink: bool = False):
        super().__init__(parent)
        self.ink = ink
        self.setFont(field_font(12) if ink else ui_font(12))
        self._value_font = ui_font(12, QFont.DemiBold)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._text = ""
        self._parts = ("", "")
        self.set_text(text)
        qconfig.themeChanged.connect(self._repaint)

    def text(self) -> str:
        return self._text

    def set_text(self, text: str):
        self._text = text
        key, sep, value = text.partition(": ")
        self._parts = (key, value) if sep and not self.ink else (text, "")
        self.setFixedSize(self.sizeHint())
        self.update()

    def sizeHint(self):
        key, value = self._parts
        width = 7 + self.fontMetrics().horizontalAdvance(key) + 13
        if value:
            width = (
                8
                + self.fontMetrics().horizontalAdvance(key)
                + 8
                + 1
                + 8
                + QFontMetrics(self._value_font).horizontalAdvance(value)
                + 14
            )
        return QSize(width, 20)

    def paintEvent(self, event):
        colors = palette(dark_theme())
        key, value = self._parts
        h = self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colors["tag_bg"] if self.ink else colors["chip"]))
        painter.drawPolygon(cut_polygon(self.width(), h, 7))
        painter.setFont(self.font())
        if not value:
            painter.setPen(QColor(colors["tag_fg"] if self.ink else colors["text"]))
            painter.drawText(QRectF(7, 0, self.width() - 7, h), Qt.AlignVCenter, key)
            painter.end()
            return
        key_width = self.fontMetrics().horizontalAdvance(key)
        painter.setPen(QColor(colors["muted"]))
        painter.drawText(QRectF(8, 0, key_width + 1, h), Qt.AlignVCenter, key)
        rule = QColor(colors["text"])
        rule.setAlphaF(0.16)
        divider = 8 + key_width + 8
        painter.fillRect(QRectF(divider, 0, 1, h), rule)
        painter.setFont(self._value_font)
        painter.setPen(QColor(colors["text"]))
        painter.drawText(QRectF(divider + 9, 0, self.width() - divider - 9, h), Qt.AlignVCenter, value)
        painter.end()


class EndfieldTriStrip(QWidget):
    """3px magenta / yellow / green strip along the top edge of the window."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setObjectName("endfieldTriStrip")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        window.installEventFilter(self)
        self._place()

    def _place(self):
        self.setGeometry(0, 0, self.parentWidget().width(), 3)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self._place()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        width = self.width()
        for index, color in enumerate(TRI_STRIP):
            left, right = width * index // 3, width * (index + 1) // 3
            painter.fillRect(left, 0, right - left, self.height(), QColor(color))
        painter.end()


_CONTOUR_PATH: QPainterPath | None = None


def _contour_path() -> QPainterPath:
    global _CONTOUR_PATH
    if _CONTOUR_PATH is None:
        # 同一条闭合曲线按比例缩放旋转，叠成地形等高线（坐标取自画布 940x700 视框）
        path = QPainterPath(QPointF(450, 110))
        path.cubicTo(610, 90, 770, 180, 790, 320)
        path.cubicTo(810, 470, 680, 590, 510, 600)
        path.cubicTo(350, 610, 190, 540, 150, 400)
        path.cubicTo(110, 250, 290, 130, 450, 110)
        _CONTOUR_PATH = path
    return _CONTOUR_PATH


def paint_contours(painter: QPainter, width: int, height: int, dark: bool) -> None:
    """Two contour clusters, top-right and bottom-left, in ink at 9%."""
    ink = QColor(palette(dark)["text"])
    ink.setAlphaF(0.09)
    pen = QPen(ink, 1)
    pen.setCosmetic(True)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    clusters = (
        (width + 200 - 940, -140, 1.0, [(4 * i, 0.06 + 0.16 * i) for i in range(1, 9)]),
        (-260, height + 330 - 566, 566 / 700, [(-6 * i, 0.1 + 0.2 * i) for i in range(1, 6)]),
    )
    for left, top, fit, rings in clusters:
        for angle, scale in rings:
            transform = QTransform()
            transform.translate(left, top)
            transform.scale(fit, fit)
            transform.translate(470, 355)
            transform.rotate(angle)
            transform.scale(scale, scale)
            transform.translate(-470, -355)
            painter.drawPath(transform.map(_contour_path()))
    painter.restore()
