"""Shared side panel for daily tasks and native configuration cards."""

from typing import Any

from ok import Logger, og
from ok.ui.qt.tasks.ConfigCard import ConfigContentMixin  # type: ignore
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal  # type: ignore
from PySide6.QtGui import QColor, QFont, QPainter  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, Theme, qconfig  # type: ignore
from shiboken6 import isValid  # type: ignore

from src.gui.EndfieldConfigContent import has_operation_row, has_task_configuration
from src.gui.EndfieldPrimitives import EndfieldActionButton, EndfieldHeader, ui_tr
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import field_font, ui_font

logger = Logger.get_logger(__name__)


def is_dark() -> bool:
    return qconfig.theme == Theme.DARK


class ConfigSyncBus(QObject):
    config_changed = Signal(str)


sync_bus = ConfigSyncBus()


class TacticalDrawerMask(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        self.clicked.emit()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(palette(is_dark())["overlay"])
        color.setAlpha(160 if is_dark() else 100)
        painter.fillRect(self.rect(), color)


class DrawerConfigForm(ConfigContentMixin, QWidget):
    def __init__(self, task, parent=None, hidden_keys=()):
        QWidget.__init__(self, parent)
        self.setObjectName("drawerConfigForm")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.viewLayout = QVBoxLayout(self)
        config_type = dict(task.config_type or {})
        for key in hidden_keys:
            config_type[key] = {**config_type.get(key, {}), "hidden": True}
        self._init_config_content(
            task,
            task.config,
            getattr(task, "default_config", {}),
            task.config_description or {},
            config_type,
        )
        self.viewLayout.setContentsMargins(1, 1, 1, 1)

    def reset_clicked(self):
        super().reset_clicked()
        # 重置也会改到表单里隐藏的字段（如一条龙的子任务清单），通知页面重读
        sync_bus.config_changed.emit(getattr(self.task, "name", ""))

    def tr(self, text, disambiguation=None, n=-1):
        # ok 的 Qt 译文登记在 ConfigCard 上下文；按本类名查找会退回英文原文
        return QCoreApplication.translate("ConfigCard", text, disambiguation, n)


_DRAWERS: dict[int, tuple["EndfieldTacticalDrawer", TacticalDrawerMask]] = {}


def drawer_for(host):
    window = host.window()
    key = id(window)
    if key not in _DRAWERS:
        drawer = EndfieldTacticalDrawer(window)
        mask = TacticalDrawerMask(window)
        mask.clicked.connect(drawer.close_drawer)
        drawer.drawer_closed.connect(mask.hide)
        _DRAWERS[key] = (drawer, mask)
        window.destroyed.connect(lambda: _DRAWERS.pop(key, None))
    return _DRAWERS[key]


def close_page_drawer(page, *, save=True):
    # 账号页的清空逻辑也会在非控件对象上调用（如测试替身）；没有窗口就没有打开的面板
    if not isinstance(page, QWidget):
        return
    entry = _DRAWERS.get(id(page.window()))
    source = entry[0]._source_card if entry else None
    if entry is not None and source is not None and page.isAncestorOf(source):
        entry[0].close_drawer(save=save)


def show_drawer(host, opener):
    drawer, mask = drawer_for(host)
    if not opener(drawer):
        return None
    mask.setGeometry(host.window().rect())
    mask.show()
    mask.raise_()
    drawer.raise_()
    return drawer


def card_has_configuration(card):
    if hasattr(card, "config_widgets"):
        return bool(card.config_widgets) or has_operation_row(card.default_config, card.task)
    return bool(card.view.findChildren(QAbstractButton))


def open_card_drawer(card):
    if not card_has_configuration(card) or not card.isEnabled():
        return None
    return show_drawer(card, lambda drawer: drawer.open_card(card))


class EndfieldTacticalDrawer(QFrame):
    drawer_closed = Signal()

    def __init__(self, parent: QWidget | None = None, width: int = 520):
        super().__init__(parent)
        self.target_width = width
        self.current_task: Any | None = None
        self.task_name = ""
        self._source_card = None
        self._commit = None
        self._view_constraints: Any = None
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._sync_source_layout)
        self.setFocusPolicy(Qt.StrongFocus)
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()
        self.setObjectName("EndfieldTacticalDrawer")
        self.setFrameShape(QFrame.NoFrame)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.header_widget = EndfieldHeader(self)
        self.header_widget.setFixedHeight(56)
        self.header_layout = QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(18, 0, 14, 0)
        self.header_layout.setSpacing(10)
        self.title_box = QVBoxLayout()
        self.title_box.setSpacing(2)
        self.sub_label = QLabel("// TASK CONFIG", self.header_widget)
        self.sub_label.setObjectName("drawerEyebrow")
        self.sub_label.setFont(field_font(11, label=True))
        self.title_label = QLabel(ui_tr("配置"), self.header_widget)
        self.title_label.setFont(ui_font(18, QFont.DemiBold))
        self.title_box.addWidget(self.sub_label)
        self.title_box.addWidget(self.title_label)
        self.header_layout.addLayout(self.title_box, 1)
        self.close_btn = QPushButton("✕", self.header_widget)
        self.close_btn.setAccessibleName(ui_tr("关闭配置面板"))
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_drawer)
        self.header_layout.addWidget(self.close_btn)
        self.main_layout.addWidget(self.header_widget)
        self.sep_line = QFrame(self)
        self.sep_line.setFixedHeight(1)
        self.main_layout.addWidget(self.sep_line)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form_container = QWidget()
        self.form_container.setObjectName("drawerFormContainer")
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setContentsMargins(16, 16, 16, 16)
        self.form_layout.setSpacing(12)
        self.form_layout.setAlignment(Qt.AlignTop)
        self.scroll_area.setWidget(self.form_container)
        self.main_layout.addWidget(self.scroll_area, 1)
        self.bottom_sep = QFrame(self)
        self.bottom_sep.setFixedHeight(1)
        self.main_layout.addWidget(self.bottom_sep)

        self.footer_widget = QWidget(self)
        self.footer_widget.setFixedHeight(64)
        self.footer_layout = QHBoxLayout(self.footer_widget)
        self.footer_layout.setContentsMargins(18, 10, 18, 12)
        self.footer_layout.setSpacing(12)
        self.save_btn = EndfieldActionButton(FluentIcon.SAVE, ui_tr("保存并同步"), self.footer_widget, primary=True)
        self.save_btn.setFixedHeight(38)
        self.save_btn.clicked.connect(self.save_and_sync)
        self.run_once_btn = EndfieldActionButton(FluentIcon.PLAY, ui_tr("立即单测运行"), self.footer_widget)
        self.run_once_btn.setFixedHeight(38)
        self.run_once_btn.clicked.connect(self.run_subtask_now)
        self.footer_layout.addWidget(self.save_btn, 1)
        self.footer_layout.addWidget(self.run_once_btn, 1)
        self.main_layout.addWidget(self.footer_widget)
        self.apply_theme_style()
        qconfig.themeChanged.connect(self._on_theme_changed)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            width = min(self.target_width, watched.width())
            self.setGeometry(watched.width() - width, 0, width, watched.height())
        source = getattr(self, "_source_card", None)
        if (
            source is not None
            and event.type() in (QEvent.Resize, QEvent.LayoutRequest)
            and (watched is source.view or (isinstance(watched, QWidget) and watched.parentWidget() is source.view))
        ):
            self._layout_timer.start(0)
        if watched is source:
            if event.type() in (QEvent.Hide, QEvent.DeferredDelete):
                self.close_drawer()
            elif event.type() == QEvent.EnabledChange:
                watched.view.setEnabled(watched.isEnabled())
                self.save_btn.setEnabled(watched.isEnabled())
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_drawer()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _on_theme_changed(self, *_args):
        self.apply_theme_style()

    def apply_theme_style(self):
        colors = palette(is_dark())
        bg, surface, border = colors["panel"], colors["canvas"], colors["border"]
        text, muted = colors["text"], colors["muted"]
        self.setStyleSheet(f"""
            #EndfieldTacticalDrawer {{ background: {bg}; border-left: 1px solid {border}; }}
            #EndfieldTacticalDrawer QScrollArea,
            #EndfieldTacticalDrawer #drawerFormContainer {{ background: {bg}; border: none; }}
            #EndfieldTacticalDrawer QLabel {{ color: {text}; }}
            #EndfieldTacticalDrawer QLabel#contentLabel {{ color: {muted}; }}
        """)
        self.header_widget.setStyleSheet(f"background: {surface};")
        self.footer_widget.setStyleSheet(f"background: {surface};")
        self.sub_label.setStyleSheet(f"color: {muted};")
        self.title_label.setStyleSheet(f"color: {text}; font-weight: bold;")
        self.close_btn.setStyleSheet(f"""
            QPushButton {{ color: {muted}; background: transparent; border: 1px solid {border}; }}
            QPushButton:hover {{ background: {colors["hover"]}; color: {text}; border-color: {colors["border_hover"]}; }}
        """)
        self.sep_line.setStyleSheet(f"background: {border};")
        self.bottom_sep.setStyleSheet(f"background: {border};")

    def _clear_form(self, *, save=True):
        self._layout_timer.stop()
        source, self._source_card = self._source_card, None
        commit, self._commit = self._commit, None
        if source is not None and isValid(source):
            source.removeEventFilter(self)
            source.destroyed.disconnect(self._source_destroyed)
            view = source.view
            view.removeEventFilter(self)
            for child in view.children():
                if isinstance(child, QWidget):
                    child.removeEventFilter(self)
            self.form_layout.removeWidget(view)
            view.hide()
            view.setParent(source.scrollWidget)
            source.scrollLayout.insertWidget(0, view)
            minimum, maximum, policy = self._view_constraints
            view.setMinimumSize(minimum)
            view.setMaximumSize(maximum)
            view.setSizePolicy(policy)
            source.isExpand = False
            source.setProperty("isExpand", False)
            source.card.expandButton.setExpand(False)
            source._adjustViewSize()
        if commit is not None and save:
            commit()
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.current_task = None

    def _source_destroyed(self):
        self._source_card = None
        self._commit = None
        self.close_drawer()

    def _show_panel(self, title):
        self.title_label.setText(title)
        self.apply_theme_style()
        parent = self.parentWidget()
        if parent is not None:
            width = min(self.target_width, parent.width())
            self.setGeometry(parent.width() - width, 0, width, parent.height())
        self.scroll_area.verticalScrollBar().setValue(0)
        self.show()
        self.raise_()
        self.close_btn.setFocus(Qt.OtherFocusReason)

    def _sync_source_layout(self):
        source = self._source_card
        if source is None or not isValid(source):
            return
        changed = False
        for index in range(source.viewLayout.count()):
            row = source.viewLayout.itemAt(index).widget()
            if row is None or row.isHidden():
                continue
            row.installEventFilter(self)
            layout = QWidget.layout(row)
            if layout is None:
                continue
            height = max(
                64,
                layout.totalHeightForWidth(row.width()) if layout.hasHeightForWidth() else layout.sizeHint().height(),
            )
            if row.minimumHeight() != height or row.maximumHeight() != height:
                row.setFixedHeight(height)
                changed = True
        if changed:
            source.viewLayout.invalidate()
            source.viewLayout.activate()
        source._adjustViewSize()

    def open_card(self, card):
        """Borrow native fields; closing returns them to the original card."""
        if not card_has_configuration(card):
            return False
        if card is self._source_card:
            self._show_panel(getattr(card, "_endfield_drawer_title", card.card.titleLabel.text()))
            return True
        self._clear_form()
        self._source_card = card
        self.task_name = getattr(card, "_endfield_drawer_title", card.card.titleLabel.text())
        self._view_constraints = (card.view.minimumSize(), card.view.maximumSize(), card.view.sizePolicy())
        self._commit = getattr(card, "_endfield_commit", None)
        ancestor = card.parentWidget()
        while self._commit is None and ancestor is not None:
            commit = getattr(ancestor, "_save_settings", None) or getattr(ancestor, "_save_pending_changes", None)
            if callable(commit):
                self._commit = commit
                break
            ancestor = ancestor.parentWidget()
        card.expandAni.stop()
        card.scrollLayout.removeWidget(card.view)
        card.view.setMinimumHeight(0)
        card.view.setMaximumHeight(16777215)
        card.view.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        card.view.installEventFilter(self)
        self.form_layout.addWidget(card.view)
        card.view.setEnabled(card.isEnabled())
        card.view.show()
        card.isExpand = True
        card.card.expandButton.setExpand(True)
        card.installEventFilter(self)
        card.destroyed.connect(self._source_destroyed)
        self.sub_label.setText("// CONFIGURATION")
        self.save_btn.setText(ui_tr("保存并关闭" if self._commit else "完成"))
        self.save_btn.setEnabled(card.isEnabled())
        self.run_once_btn.hide()
        self._show_panel(self.task_name)
        self._layout_timer.start(0)
        return True

    def open_subtask(self, task_name: str, task_obj: Any, hidden_keys=(), *, run_once=True):
        if not has_task_configuration(task_obj):
            return False
        self._clear_form()
        form = DrawerConfigForm(task_obj, self.form_container, hidden_keys)
        if not form.config_widgets and not has_operation_row(form.default_config, task_obj):
            form.deleteLater()
            return False
        self.task_name = task_name
        self.current_task = task_obj
        self.form_layout.addWidget(form)
        self.sub_label.setText("// TASK CONFIG")
        self.save_btn.setText(ui_tr("保存并同步"))
        self.save_btn.setEnabled(True)
        self.run_once_btn.setVisible(run_once)
        self._show_panel(ui_tr("{} 参数配置").format(ui_tr(task_name)))
        return True

    def save_and_sync(self):
        if self.current_task is not None:
            config = self.current_task.config
            if hasattr(config, "save"):
                config.save()
            sync_bus.config_changed.emit(self.task_name)
        self.close_drawer()

    def run_subtask_now(self):
        # 与任务卡片的「开始」走同一入口；TaskExecutor 没有按任务启动的方法
        start_controller = getattr(getattr(og, "app", None), "start_controller", None)
        if self.current_task is None or start_controller is None:
            return
        start_controller.start(self.current_task)

    def close_drawer(self, *, save=True):
        self._clear_form(save=save)
        self.hide()
        self.drawer_closed.emit()
        parent = self.parentWidget()
        if parent is not None:
            parent.update()
