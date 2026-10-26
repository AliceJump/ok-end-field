"""Run controls at the foot of the navigation: launch the game and drive the daily routine from any page."""

from typing import Any

from ok import og
from ok.ui.qt.common.OKIcon import OKIcon
from ok.ui.qt.Communicate import communicate
from PySide6.QtCore import QSize  # type: ignore
from PySide6.QtGui import QColor, QFont, QPainter  # type: ignore
from PySide6.QtWidgets import QWidget  # type: ignore
from qfluentwidgets import FluentIcon, ToolTipFilter, ToolTipPosition, qconfig  # type: ignore
from qfluentwidgets.components.navigation.navigation_widget import NavigationWidget  # type: ignore

from src.gui.EndfieldPrimitives import EndfieldActionButton, ThemeRepaint, dark_theme, ui_tr
from src.gui.EndfieldTheme import palette
from src.gui.EndfieldTypography import ui_font
from src.gui.EndfieldWorkspaceTab import EndfieldRunState, EndfieldWorkspaceTab, run_state

BUTTON_HEIGHT = 36
ROW_GAP = 6
# 顶部 1px 分隔线下留 10px；游戏行和一条龙行之间多留 2px，分成两组
TOP = 11
GROUP_GAP = 8
BOTTOM = 6
# 状态方块对齐导航图标列，状态文字对齐导航文字列
MARKER_LEFT = 16.5
TEXT_LEFT = 44


def game_connected() -> bool:
    manager = getattr(og, "device_manager", None)
    device = manager.get_preferred_device() if manager is not None else None
    return bool(device and device.get("connected"))


def game_launching() -> bool:
    controller = getattr(getattr(og, "app", None), "start_controller", None)
    return bool(getattr(controller, "starting", False))


def launch_game():
    """同原生启动卡片的“启动游戏”：游戏没开时先拉起游戏，再启动执行器。"""
    og.app.start_controller.start()


class EndfieldRunDock(ThemeRepaint, NavigationWidget):
    """Launch the game, then start, pause or stop the daily routine; mirrors the daily header actions."""

    def __init__(self, workspace: EndfieldWorkspaceTab, parent: Any = None):
        super().__init__(isSelectable=False, parent=parent)
        self.workspace = workspace
        self._launching = game_launching()
        self._rows: list[list[QWidget]] = []
        self.launch_button = self._button(FluentIcon.GAME)
        self.launch_button.clicked.connect(launch_game)
        self.game_status = self._status()
        self.game_status.set_state("online")
        self.run_status = self._status()
        self.start_button = self._button(FluentIcon.PLAY, signal=True)
        self.start_button.clicked.connect(workspace.start_run)
        self.pause_button = self._button(FluentIcon.PAUSE)
        self.pause_button.clicked.connect(workspace.pause_run)
        self.stop_button = self._button(OKIcon.STOP, primary=True)
        self.stop_button.clicked.connect(workspace.stop_run)
        qconfig.themeChanged.connect(self._repaint)
        # 事件总线强引用订阅者，控件销毁时要退订
        subscriptions = (
            (communicate.task, self.sync),
            (communicate.adb_devices, self.sync),
            (communicate.starting_emulator, self._launch_changed),
        )
        for signal, slot in subscriptions:
            signal.connect(slot)

        def unsubscribe(*_args):
            for signal, slot in subscriptions:
                signal.disconnect(slot)

        self.destroyed.connect(unsubscribe)
        self.sync()

    def _button(self, icon: Any, *, primary: bool = False, signal: bool = False) -> EndfieldActionButton:
        button = EndfieldActionButton(icon, "", self, primary=primary, signal=signal)
        button.setIconSize(QSize(14, 14))
        button.setFont(ui_font(14, QFont.DemiBold))
        button.installEventFilter(ToolTipFilter(button, 300, ToolTipPosition.RIGHT))
        return button

    def _status(self) -> EndfieldRunState:
        status = EndfieldRunState(self, marker_left=MARKER_LEFT, text_left=TEXT_LEFT)
        status.installEventFilter(ToolTipFilter(status, 300, ToolTipPosition.RIGHT))
        return status

    def _label(self, button: EndfieldActionButton, text: str):
        """Compact buttons show only the icon; the label moves to the tooltip."""
        button.setText("" if self.isCompacted else text)
        button.setToolTip(text if self.isCompacted else "")
        button.setAccessibleName(text)

    def _launch_changed(self, done: bool, *_args):
        self._launching = not done
        self.sync()

    def setCompacted(self, isCompacted: bool):
        self.isCompacted = isCompacted
        self.sync()

    # 控制区本身只展示，点击交给里面的按钮，不触发导航
    def mousePressEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def sync(self, *_args):
        self.launch_button.setEnabled(not self._launching)
        self._label(self.launch_button, ui_tr("启动中…" if self._launching else "启动游戏"))
        self._label(self.pause_button, ui_tr("暂停"))
        self._label(self.stop_button, ui_tr("停止"))
        rows: list[list[QWidget]] = [[self.game_status if game_connected() else self.launch_button]]
        task = self.workspace.run_task()
        if task is not None:
            state = run_state(task)
            self.start_button.setEnabled(not self._launching)
            self._label(self.start_button, ui_tr("继续" if state == "paused" else "开始一条龙"))
            if state == "idle":
                rows.append([self.start_button])
            else:
                self.run_status.set_state(state)
                actions = {
                    "waiting": [self.stop_button],
                    "running": [self.pause_button, self.stop_button],
                    "paused": [self.start_button, self.stop_button],
                }[state]
                rows.append([self.run_status])
                rows.extend([[button] for button in actions] if self.isCompacted else [actions])
        self._rows = rows
        self._relayout()

    def _relayout(self):
        width = 40 if self.isCompacted else self.EXPAND_WIDTH
        placed = {widget for row in self._rows for widget in row}
        for widget in (
            self.launch_button,
            self.game_status,
            self.run_status,
            self.start_button,
            self.pause_button,
            self.stop_button,
        ):
            widget.setVisible(widget in placed)
        y = TOP
        for index, row in enumerate(self._rows):
            if index:
                y += GROUP_GAP if index == 1 else ROW_GAP
            if isinstance(row[0], EndfieldRunState):
                row[0].set_compact(self.isCompacted)
                row[0].move(0, y)
                y += row[0].height()
                continue
            cell = (width - ROW_GAP * (len(row) - 1)) / len(row)
            for column, button in enumerate(row):
                left = round(column * (cell + ROW_GAP))
                button.setFixedSize(round(column * (cell + ROW_GAP) + cell) - left, BUTTON_HEIGHT)
                button.move(left, y)
            y += BUTTON_HEIGHT
        self.setFixedSize(width, y + BOTTOM)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(0, 0, self.width(), 1, QColor(palette(dark_theme())["divider"]))
        painter.end()
