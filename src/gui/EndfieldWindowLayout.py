"""Responsive capture controls and a reset for the desktop window geometry."""

from ok.core.ui_config import resolve_window_size
from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt  # type: ignore
from PySide6.QtGui import QPainter, QPen  # type: ignore
from PySide6.QtWidgets import QAbstractButton, QBoxLayout, QSizePolicy, QWidget  # type: ignore
from qfluentwidgets import ExpandSettingCard, FlowLayout, SettingCard, ToolTipFilter  # type: ignore
from qframelesswindow.titlebar.title_bar_buttons import MinimizeButton  # type: ignore
from shiboken6 import isValid  # type: ignore

from src.gui.EndfieldEntryCards import style_expand_header, style_setting_header
from src.gui.EndfieldPrimitives import ui_tr


def default_window_rect(config: dict, available: QRect) -> QRect:
    size = resolve_window_size(config)
    width = min(size["width"], max(1, available.width() - 32))
    height = min(size["height"], max(1, available.height() - 48))
    rect = QRect(0, 0, width, height)
    rect.moveCenter(available.center())
    return rect


def reset_window_size(window: QWidget, config: dict) -> None:
    available = window.screen().availableGeometry()
    target = default_window_rect(config, available)
    size = resolve_window_size(config)
    window.showNormal()
    window.setMinimumSize(QSize(min(size["min_width"], target.width()), min(size["min_height"], target.height())))
    window.setGeometry(target)
    window.raise_()
    window.activateWindow()


class WindowSizeResetButton(MinimizeButton):
    """Title bar button that restores the default window size.

    It subclasses MinimizeButton so the window style sheet gives it the same colours in both themes.
    """

    def paintEvent(self, _event):
        painter = QPainter(self)
        color, background = self._getColors()
        painter.setBrush(background)
        painter.setPen(Qt.NoPen)
        painter.drawRect(self.rect())

        pen = QPen(color, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        # 四角取景框，和最大化按钮的整框区分；与 MaximizeButton 一样按设备像素取整，避免高分屏发虚
        ratio = self.devicePixelRatioF()
        painter.scale(1 / ratio, 1 / ratio)
        left, top, right, bottom, arm = (int(value * ratio) for value in (18, 11, 28, 21, 3))
        for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(x, y, x + dx * arm, y)
            painter.drawLine(x, y, x, y + dy * arm)


def install_window_size_button(window) -> None:
    """Put the default-size reset left of the minimise button, beside the other window controls."""
    from ok import og

    title_bar = window.titleBar
    button = WindowSizeResetButton(title_bar)
    button.setObjectName("windowSizeResetButton")
    button.setToolTip(ui_tr("恢复默认大小"))
    button.installEventFilter(ToolTipFilter(button))
    button.clicked.connect(lambda: reset_window_size(window, og.config or {}))
    title_bar.buttonLayout.insertWidget(0, button)
    window._endfield_size_button = button


class CaptureLayoutController(QObject):
    """Selector panels keep their content height; they stack vertically on narrow windows."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.selector = tab.selector_widget.layout()
        tab.selector_widget.setMinimumHeight(0)
        tab.selector_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for index in range(self.selector.count()):
            panel = self.selector.itemAt(index).widget()
            if panel is not None:
                self.selector.setAlignment(panel, Qt.AlignTop)
        tab.viewport().installEventFilter(self)
        self.update_layout()

    def update_layout(self):
        narrow = self.tab.viewport().width() < 1100
        self.selector.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)
        self.tab.selector_widget.updateGeometry()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.update_layout()
        return super().eventFilter(watched, event)


class ListRowFitter(QObject):
    """Size a selector list to its rows (2 to max_rows) instead of stretching it to the page height."""

    def __init__(self, view, max_rows: int = 6):
        super().__init__(view)
        self.view = view
        self.max_rows = max_rows
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        model = view.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(self.fit)
        self.fit()

    def fit(self, *_args):
        # 列表析构时模型会发出移除信号，此时视图已失效
        if not isValid(self.view):
            return
        view = self.view
        row_height = view.sizeHintForRow(0) if view.count() else 40
        rows = min(max(view.count(), 2), self.max_rows)
        view.setFixedHeight(rows * max(row_height, 1) + 2 * view.frameWidth() + 4)


def fit_list_rows(view, max_rows: int = 6) -> None:
    view._endfield_row_fitter = ListRowFitter(view, max_rows)


def _transparent_paint(_self, _event) -> None:
    return


def _owning_card(widget):
    from ok.ui.qt.widget.Card import Card

    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, Card):
        parent = parent.parentWidget()
    return parent


def wrap_capture_controls(tab) -> None:
    """Keep late-added debug actions, but allow them to wrap rather than widen the page."""
    old = tab.debug_layout
    toolbar = QWidget(tab.debug_widget)
    # Measure controls even while their page is hidden, so returning to a resized
    # page reserves the full wrapped height before it is painted.
    flow = FlowLayout(toolbar, isTight=False)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setHorizontalSpacing(8)
    flow.setVerticalSpacing(8)
    controls = []
    while old.count():
        item = old.takeAt(0)
        if item.widget() is not None:
            controls.append(item.widget())
    # 标记框开关是常驻状态，不是一次性操作：放到「开发工具」标题行右侧
    overlay_switch = getattr(tab, "overlay_switch", None)
    panel = _owning_card(tab.debug_widget)
    if overlay_switch in controls and panel is not None and panel.title_layout is not None:
        controls.remove(overlay_switch)
        panel.add_top_widget(overlay_switch)
    for control in controls:
        if isinstance(control, QAbstractButton):
            control.setFixedHeight(28)
        flow.addWidget(control)
    old.addWidget(toolbar)
    tab.debug_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    # Later controls can be appended to this same wrapping layout.
    tab.debug_layout = flow
    tab._endfield_capture_layout = CaptureLayoutController(tab)


def patch_window_layout() -> None:
    from ok.ui.qt.settings.SettingTab import SettingTab
    from ok.ui.qt.start.StartCard import StartCard
    from ok.ui.qt.start.StartTab import StartTab

    original_start = StartTab.__init__
    if not getattr(original_start, "_endfield_responsive", False):

        def _start_init(self, *args, **kwargs):
            original_start(self, *args, **kwargs)
            for view in (self.device_list, self.capture_list, self.interaction_list):
                fit_list_rows(view)
            # 品牌已在导航顶部，启动卡只保留状态和三个操作按钮
            card = self.start_card
            for label in (card.iconLabel, card.titleLabel, card.contentLabel):
                label.hide()
            card.hBoxLayout.insertStretch(card.hBoxLayout.indexOf(card.status_bar) + 1, 1)
            wrap_capture_controls(self)

        _start_init._endfield_responsive = True  # type: ignore[attr-defined]
        StartTab.__init__ = _start_init
        StartCard.paintEvent = _transparent_paint

    original_settings = SettingTab.__init__
    if not getattr(original_settings, "_endfield_responsive", False):

        def _settings_init(self, *args, **kwargs):
            original_settings(self, *args, **kwargs)
            self.basic_group.cardLayout.setSpacing(12)
            # Subclasses append their controls and trailing spacers after SettingCard.__init__.
            for entry in self.basic_group.findChildren(QWidget, options=Qt.FindDirectChildrenOnly):
                if isinstance(entry, ExpandSettingCard):
                    style_expand_header(entry)
                elif isinstance(entry, SettingCard):
                    entry.setFixedHeight(56)
                    style_setting_header(entry)
            self.basic_group.adjustSize()

        _settings_init._endfield_responsive = True  # type: ignore[attr-defined]
        SettingTab.__init__ = _settings_init
