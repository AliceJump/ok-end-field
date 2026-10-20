# Endfield Workspace Container & Segmented Navigation
# Dual-theme adaptive (Dark / Light) with WCAG 2.1 AA compliance

from collections.abc import Callable
from contextlib import suppress
from typing import Any

from ok import Logger
from PySide6.QtCore import QRectF, QSize, Qt, Signal  # type: ignore
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FlowLayout, FluentIcon, Theme, qconfig  # type: ignore

from src.gui.EndfieldEntryCards import (
    EndfieldEntryNumbering,
    EndfieldSectionBanner,
    english_title,
    remove_page_decorations,
)
from src.gui.EndfieldPrimitives import (
    KEYBOARD_REASONS,
    EndfieldActionButton,
    EndfieldHatchBand,
    ThemeRepaint,
    cut_polygon,
    draw_focus_ring,
    paint_contours,
    ui_tr,
)
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import field_font, ui_font

logger = Logger.get_logger(__name__)

# 页签眉标：短英文代号，长名称会把 9px 眉标撑出页签
TAB_CODES = {
    "截图方式": "CAPTURE",
    "全局配置": "GLOBAL",
    "账号配置": "ACCOUNTS",
    "通知": "NOTICE",
    "设置": "SETTINGS",
    "关于": "ABOUT",
    "帝江号": "DIJIANG",
    "战斗": "COMBAT",
    "奖励": "REWARDS",
    "其他": "OTHERS",
    "大世界": "WORLD",
    "自动战斗": "AUTO",
    "副本": "DUNGEON",
    "计划": "SCHEDULE",
    "计划任务": "SCHEDULE",
    "脚本": "SCRIPTS",
    "模板": "TEMPLATES",
    "调试": "DEBUG",
    "工具与调试": "TOOLS",
    "运行代码": "CODE",
}


def tab_code(name: str) -> str:
    return TAB_CODES.get(name) or english_title(name).split(" ")[0]


def tab_key(name: str, widget: QWidget) -> str:
    # 主窗口补丁给原生页面记下简体中文页签名；版面表按它查，name 是界面语言的显示文字
    return getattr(widget, "endfield_tab_key", name)


def is_dark_theme() -> bool:
    try:
        return qconfig.theme == Theme.DARK
    except Exception:
        return True


class EndfieldTabButton(QPushButton):
    """Two-line tab: the current one is a signal block with a cut corner, the others are muted text."""

    def __init__(self, text: str, icon_obj: Any = None, parent: QWidget | None = None, key: str | None = None):
        super().__init__(text, parent)
        self.icon_obj = icon_obj
        self.code = tab_code(key or text)
        self.is_active = False
        self._keyboard_focus = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFont(ui_font(14, QFont.DemiBold))
        self._code_font = field_font(9, label=True)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        # 所有工作区共用同一宽度，按最长的中文页签名留出加粗后的空间；其他语言的长名称按自身加宽，不截断
        bold = QFontMetrics(ui_font(14, QFont.Bold))
        width = max(bold.horizontalAdvance("工具与调试"), bold.horizontalAdvance(self.text())) + 32
        return QSize(max(92, width), 46)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_active(self, active: bool):
        self.is_active = active
        self.setChecked(active)
        self.update()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in KEYBOARD_REASONS
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def paintEvent(self, event):
        colors = palette(is_dark_theme())
        w, h = self.width(), self.height()
        hover = self.underMouse() and not self.is_active
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.is_active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors["signal"]))
            painter.drawPolygon(cut_polygon(w, h, 7))
            ink = colors["on_signal"]
        else:
            ink = colors["text"] if hover else colors["muted"]
        if hover:
            painter.fillRect(0, h - 3, w, 3, QColor(colors["accent"]))
        painter.setPen(QColor(ink))
        painter.setFont(self._code_font)
        painter.drawText(QRectF(12, 6, w - 20, 12), Qt.AlignLeft | Qt.AlignVCenter, self.code)
        painter.setFont(ui_font(14, QFont.Bold if self.is_active else QFont.Medium))
        painter.drawText(QRectF(12, 18, w - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, self.text())
        if self.hasFocus() and self._keyboard_focus:
            draw_focus_ring(painter, QRectF(0, 0, w, h), ink)
        painter.end()


class EndfieldSegmentedBar(QWidget):
    """Uniform tab buttons that wrap as whole controls at narrow widths."""

    tabChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.layout = FlowLayout(self, isTight=False)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setHorizontalSpacing(2)
        self.layout.setVerticalSpacing(2)
        self.buttons: list[EndfieldTabButton] = []
        self.current_index = 0

    def sizeHint(self) -> QSize:
        widths = sum(button.sizeHint().width() for button in self.buttons)
        spacing = self.layout.horizontalSpacing() * max(0, len(self.buttons) - 1)
        # FlowLayout compares against QRect.right(), which is one pixel short of its width.
        return QSize(widths + spacing + 1, 46)

    def add_tab(self, text: str, icon_obj: Any = None, key: str | None = None) -> int:
        idx = len(self.buttons)
        btn = EndfieldTabButton(text, icon_obj, self, key)
        btn.clicked.connect(lambda: self.set_current_index(idx))
        self.buttons.append(btn)
        self.layout.addWidget(btn)
        if idx == 0:
            btn.set_active(True)
        return idx

    def set_current_index(self, index: int):
        if 0 <= index < len(self.buttons):
            self.current_index = index
            for i, b in enumerate(self.buttons):
                b.set_active(i == index)
            self.tabChanged.emit(index)


class EndfieldWorkspaceHeader(ThemeRepaint, QWidget):
    """Title row and tab row; the tabs stand on a 1px rule along its bottom edge."""

    INSET = 16

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        qconfig.themeChanged.connect(self._repaint)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(
            self.INSET, self.height() - 1, self.width() - 2 * self.INSET, 1, QColor(palette(is_dark_theme())["border"])
        )
        painter.end()


class EndfieldWorkspaceTab(QWidget):
    """Top-level workspace: title row, tab row, and a stack of the native child pages."""

    def __init__(
        self,
        category_code: str,
        category_title: str,
        tabs: list[tuple[str, Any, QWidget]],
        parent: QWidget | None = None,
        primary_action: tuple[str, Callable[[], None]] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName(f"{category_code.lower()}_workspace")
        self.category_code = category_code
        self.category_title = category_title
        self.child_tabs = tabs
        self.primary_action_button: EndfieldActionButton | None = None
        self._backdrop: QPixmap | None = None
        self._backdrop_key: tuple | None = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 16, 20, 16)
        self.main_layout.setSpacing(16)

        self.header_bar = EndfieldWorkspaceHeader(self)
        self.header_bar.setObjectName("EndfieldWorkspaceHeader")
        header = QVBoxLayout(self.header_bar)
        # Native pages inset their content by 16px; the header lines up with them.
        inset = EndfieldWorkspaceHeader.INSET
        header.setContentsMargins(inset, 0, inset, 1)
        header.setSpacing(14)

        title_row = QHBoxLayout()
        title_row.setSpacing(20)
        title_box = QWidget(self.header_bar)
        self.title_box = title_box
        title_vlayout = QVBoxLayout(title_box)
        title_vlayout.setContentsMargins(0, 0, 0, 0)
        title_vlayout.setSpacing(2)
        workspace_code = english_title(category_title)
        self.eyebrow_label = QLabel(f"// {workspace_code}" if workspace_code else "//", title_box)
        self.eyebrow_label.setObjectName("workspaceEyebrow")
        self.eyebrow_label.setFont(field_font(11, label=True))
        self.title_label = QLabel(ui_tr(category_title), title_box)
        self.title_label.setFont(ui_font(30, QFont.Black))
        title_vlayout.addWidget(self.eyebrow_label)
        title_vlayout.addWidget(self.title_label)
        title_row.addWidget(title_box, 0, Qt.AlignBottom)
        self.hatch_band = EndfieldHatchBand(self.header_bar)
        band_box = QVBoxLayout()
        band_box.setContentsMargins(0, 0, 0, 8)
        band_box.addStretch(1)
        band_box.addWidget(self.hatch_band)
        title_row.addLayout(band_box, 1)
        header.addLayout(title_row)

        self.header_layout = QBoxLayout(QBoxLayout.LeftToRight)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(12)
        self.tab_bar = EndfieldSegmentedBar(self.header_bar)
        for name, icon_obj, tab_widget in tabs:
            self.tab_bar.add_tab(name, icon_obj, tab_key(name, tab_widget))
        self.header_layout.addWidget(self.tab_bar, 0, Qt.AlignBottom)
        self.header_layout.addStretch(1)

        # Keep the workspace action separate from section navigation.
        if primary_action is not None:
            action_text, action_callback = primary_action
            action_btn = EndfieldActionButton(FluentIcon.PLAY, ui_tr(action_text), self.header_bar, primary=True)
            action_btn.setObjectName("workspacePrimaryAction")
            action_btn.setFixedSize(132, 36)
            action_btn.setIconSize(QSize(14, 14))
            action_btn.setFont(ui_font(14, QFont.DemiBold))
            action_btn.clicked.connect(lambda: action_callback())
            self.primary_action_button = action_btn
            self.header_layout.addWidget(action_btn, 0, Qt.AlignVCenter)
        header.addLayout(self.header_layout)

        self.main_layout.addWidget(self.header_bar)

        self.stack = QStackedWidget(self)
        self.stack.setObjectName("EndfieldWorkspaceStack")
        self.stack.setStyleSheet("background: transparent;")

        self._page_containers: dict[QWidget, QWidget] = {}
        # 编号页的任务行贴在一起，由 EndfieldEntryNumbering 维护间距
        row_spacing = 12 if category_code == "DAILY" else 0
        for index, (name, _, tab_widget) in enumerate(tabs, 1):
            for attr, spacing in (("vBoxLayout", 12), ("taskCardLayout", row_spacing)):
                layout = getattr(tab_widget, attr, None)
                if layout is not None:
                    layout.setSpacing(spacing)
            container = tab_widget
            key = tab_key(name, tab_widget)
            plain_page = key in ("截图方式", "关于")
            if category_code != "DAILY" and not plain_page:
                banner = EndfieldSectionBanner(f"{index:02d}", name, english_title(key), tab_widget)
                native_layout: Any = getattr(tab_widget, "vBoxLayout", None)
                if isinstance(native_layout, QVBoxLayout):
                    native_layout.insertWidget(0, banner)
                    native_title = tab_widget.findChild(QLabel, "viewTitleLabel")
                    if native_title is not None and native_title.text() == name:
                        native_title.hide()
                else:
                    container = QWidget(self.stack)
                    content = QVBoxLayout(container)
                    content.setContentsMargins(16, 16, 16, 0)
                    content.setSpacing(12)
                    content.addWidget(banner)
                    content.addWidget(tab_widget, 1)
            self._page_containers[tab_widget] = container
            self.stack.addWidget(container)
            if plain_page:
                remove_page_decorations(tab_widget)
            elif category_code != "DAILY":
                tab_widget._endfield_numbering = EndfieldEntryNumbering(tab_widget)

        self.main_layout.addWidget(self.stack, 1)

        self.tab_bar.tabChanged.connect(self.stack.setCurrentIndex)
        self.apply_theme_colors()
        with suppress(Exception):
            qconfig.themeChanged.connect(self.apply_theme_colors)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "tab_bar"):
            return
        required = self.tab_bar.sizeHint().width() + 2 * EndfieldWorkspaceHeader.INSET
        if self.primary_action_button is not None:
            required += self.primary_action_button.width() + self.header_layout.spacing()
        compact = self.header_bar.width() < required
        direction = QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight
        if self.header_layout.direction() != direction:
            self.header_layout.setDirection(direction)
            if self.primary_action_button is not None:
                self.header_layout.setAlignment(
                    self.primary_action_button, Qt.AlignRight if compact else Qt.AlignVCenter
                )

    def paintEvent(self, event):
        # 画布底色和等高线缓存成一张图，子控件重绘时直接贴图
        dark = is_dark_theme()
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), dark, ratio)
        if self._backdrop_key != key:
            backdrop = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            backdrop.setDevicePixelRatio(ratio)
            backdrop.fill(QColor(palette(dark)["canvas"]))
            painter = QPainter(backdrop)
            paint_contours(painter, self.width(), self.height(), dark)
            painter.end()
            self._backdrop, self._backdrop_key = backdrop, key
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._backdrop)
        painter.end()

    def apply_theme_colors(self, *_args):
        colors = palette(is_dark_theme())
        self.eyebrow_label.setStyleSheet(f"color: {colors['muted']};")
        self.title_label.setStyleSheet(f"color: {colors['text']};")
        for btn in self.tab_bar.buttons:
            btn.update()
        self.update()

    def has_child(self, widget: QWidget) -> bool:
        return widget in self._page_containers

    def switch_to_child(self, widget: QWidget):
        idx = self.stack.indexOf(self._page_containers.get(widget, widget))
        if idx != -1:
            self.tab_bar.set_current_index(idx)
