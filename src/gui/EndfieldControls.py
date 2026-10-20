"""Square geometry on Fluent controls while retaining native state and input handling."""

from typing import Any

from PySide6.QtCore import QRectF, Qt  # type: ignore
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QRegion  # type: ignore
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton  # type: ignore
from qfluentwidgets import LineEdit, Theme, qconfig  # type: ignore
from qfluentwidgets.common.icon import FluentIconBase  # type: ignore
from qfluentwidgets.components.widgets.button import (  # type: ignore
    HyperlinkButton,
    PrimaryPushButton,
    PushButton,
    TransparentPushButton,
)
from qfluentwidgets.components.widgets.check_box import CheckBox, CheckBoxIcon  # type: ignore
from qfluentwidgets.components.widgets.list_view import ListItemDelegate  # type: ignore
from qfluentwidgets.components.widgets.switch_button import Indicator  # type: ignore

from src.gui.EndfieldPrimitives import KEYBOARD_REASONS, cut_polygon, draw_focus_ring
from src.gui.EndfieldTheme import DARK, LIGHT, palette


def _dark() -> bool:
    return qconfig.theme == Theme.DARK


def install_control_paint() -> None:
    if getattr(Indicator._drawBackground, "_endfield_patched", False):
        return

    original_init = Indicator.__init__
    original_indicator_focus_in = Indicator.focusInEvent
    original_indicator_focus_out = Indicator.focusOutEvent

    def indicator_init(self: Any, parent: Any) -> None:
        original_init(self, parent)
        self._endfield_keyboard_focus = False
        self.setFocusPolicy(Qt.TabFocus)

    def indicator_focus_in(self: Any, event: Any) -> None:
        self._endfield_keyboard_focus = event.reason() in KEYBOARD_REASONS
        original_indicator_focus_in(self, event)

    def indicator_focus_out(self: Any, event: Any) -> None:
        self._endfield_keyboard_focus = False
        original_indicator_focus_out(self, event)

    def background(self: Any, painter: QPainter) -> None:
        colors = palette(_dark())
        enabled, checked = self.isEnabled(), self.isChecked()
        if not enabled:
            fill = colors["disabled_bg"]
        elif checked:
            fill = colors["signal"]
        else:
            fill = colors["border"] if (self.isHover or self.isPressed) else colors["chip"]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRect(self.rect())
        painter.restore()

    def slider(self: Any, painter: QPainter) -> None:
        colors = palette(_dark())
        if not self.isEnabled():
            color = colors["disabled_text"]
        elif self.isChecked():
            color = colors["on_signal"]
        else:
            # 白色旋钮在浅灰轨道上几乎看不见，关闭态统一用次要文字色
            color = colors["muted"]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        # sliderX 在 5..25 之间滑动；方块旋钮比原圆点大，左上各内缩 3px
        painter.drawRect(int(self.sliderX) - 2, 3, self.height() - 6, self.height() - 6)
        if self.hasFocus() and self.isEnabled() and getattr(self, "_endfield_keyboard_focus", False):
            draw_focus_ring(painter, QRectF(self.rect()), colors["text"], inset=0)
        painter.restore()

    def checkbox_paint(self: Any, event: Any) -> None:
        QCheckBox.paintEvent(self, event)
        painter = QPainter(self)
        option = QStyleOptionButton()
        option.initFrom(self)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter.setPen(self._borderColor())
        painter.setBrush(self._backgroundColor())
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if not self.isEnabled():
            painter.setOpacity(0.8)
        if self.checkState() == Qt.Checked:
            CheckBoxIcon.ACCEPT.render(painter, rect)
        elif self.checkState() == Qt.PartiallyChecked:
            CheckBoxIcon.PARTIAL_ACCEPT.render(painter, rect)
        if self.hasFocus() and self.isEnabled():
            painter.setPen(QPen(QColor(palette(_dark())["text"]), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
        painter.end()

    original_line_init = LineEdit.__init__

    def line_init(self: Any, parent: Any = None) -> None:
        original_line_init(self, parent)
        # The native error-state color still takes precedence over these normal focus colors.
        self.setCustomFocusedBorderColor(LIGHT["accent"], DARK["accent"])

    background._endfield_patched = True  # type: ignore[attr-defined]
    Indicator.__init__ = indicator_init
    Indicator.focusInEvent = indicator_focus_in
    Indicator.focusOutEvent = indicator_focus_out
    Indicator._drawBackground = background
    Indicator._drawCircle = slider
    CheckBox.paintEvent = checkbox_paint
    LineEdit.__init__ = line_init
    install_button_shape()
    install_list_rows()


def _cut_button(button: Any) -> bool:
    return isinstance(button, PushButton) and not isinstance(button, (TransparentPushButton, HyperlinkButton))


def install_button_shape() -> None:
    """Native push buttons: cut bottom-right corner, black icon on the yellow hover, keyboard-only focus."""
    original_post_init = PushButton._postInit
    original_resize = PushButton.resizeEvent
    original_focus_in = PushButton.focusInEvent
    original_focus_out = PushButton.focusOutEvent
    original_paint = PushButton.paintEvent

    def post_init(self: Any) -> None:
        original_post_init(self)
        self._endfield_keyboard_focus = False
        if _cut_button(self):
            self.setFocusPolicy(Qt.TabFocus)

    def resize_event(self: Any, event: Any) -> None:
        original_resize(self, event)
        if _cut_button(self) and self.height() >= 24:
            cut = 7 if self.height() <= 30 else 8
            self.setMask(QRegion(cut_polygon(self.width(), self.height(), cut).toPolygon()))

    def focus_in(self: Any, event: Any) -> None:
        self._endfield_keyboard_focus = event.reason() in KEYBOARD_REASONS
        original_focus_in(self, event)

    def focus_out(self: Any, event: Any) -> None:
        self._endfield_keyboard_focus = False
        original_focus_out(self, event)

    def paint_event(self: Any, event: Any) -> None:
        original_paint(self, event)
        if self.hasFocus() and getattr(self, "_endfield_keyboard_focus", False) and _cut_button(self):
            colors = palette(_dark())
            if self.isHover:
                ring = colors["on_signal"]
            else:
                ring = colors["on_go"] if isinstance(self, PrimaryPushButton) else colors["text"]
            painter = QPainter(self)
            draw_focus_ring(painter, QRectF(self.rect()), ring)
            painter.end()

    def hover_icon(original: Any) -> Any:
        def draw_icon(self: Any, icon: Any, painter: Any, rect: Any, state: Any = QIcon.Off) -> None:
            # 悬停底色是信号黄，图标与文字一起换成黑色
            if self.isEnabled() and (self.isHover or self.isPressed) and isinstance(icon, FluentIconBase):
                icon = icon.icon(Theme.LIGHT)
            original(self, icon, painter, rect, state)

        return draw_icon

    PushButton._postInit = post_init
    PushButton.resizeEvent = resize_event
    PushButton.focusInEvent = focus_in
    PushButton.focusOutEvent = focus_out
    PushButton.paintEvent = paint_event
    PushButton._drawIcon = hover_icon(PushButton._drawIcon)
    PrimaryPushButton._drawIcon = hover_icon(PrimaryPushButton._drawIcon)


def install_list_rows() -> None:
    """List rows: square, hairline-separated; the selected row gets a full-height 4px trace bar."""

    def draw_background(self: Any, painter: QPainter, option: Any, index: Any) -> None:
        colors = palette(_dark())
        rect = option.rect.adjusted(0, -self.margin, 0, self.margin)
        selected = index.row() in self.selectedRows
        if self.hoverRow == index.row() or self.pressedRow == index.row():
            painter.fillRect(rect, QColor(colors["row_hover"]))
        painter.fillRect(rect.left(), rect.bottom(), rect.width(), 1, QColor(colors["divider"]))
        if selected:
            painter.fillRect(rect.left(), rect.top(), 4, rect.height(), QColor(colors["accent"]))

    def draw_indicator(self: Any, painter: QPainter, option: Any, index: Any) -> None:
        return

    original_style = ListItemDelegate.initStyleOption
    original_size = ListItemDelegate.sizeHint

    def init_style(self: Any, option: Any, index: Any) -> None:
        original_style(self, option, index)
        option.rect.adjust(10, 0, 0, 0)
        if index.row() in self.selectedRows:
            font = QFont(option.font)
            font.setWeight(QFont.Bold)
            option.font = font

    def size_hint(self: Any, option: Any, index: Any) -> Any:
        size = original_size(self, option, index)
        size.setHeight(max(size.height(), 40))
        return size

    ListItemDelegate._drawBackground = draw_background
    ListItemDelegate._drawIndicator = draw_indicator
    ListItemDelegate.initStyleOption = init_style
    ListItemDelegate.sizeHint = size_hint
