"""Import a QSS or Nailong visual theme at runtime, without an upstream fork."""

from __future__ import annotations

from ok.gui.widget.CustomTab import CustomTab
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from qfluentwidgets import FluentIcon, NavigationItemPosition

from src.gui.NailongWidget import NailongBackdrop, NailongCompanion
from src.themes.theme_pack import ThemePackError, import_theme_pack, load_active_theme, select_theme

# Qt's default scroll-area/page canvases are otherwise opaque, which would
# hide a native wallpaper below the page stack. Limit transparency to those
# page surfaces, leaving cards, controls and the navigation visually intact.
_NAILONG_PAGE_QSS = """
QStackedWidget, QScrollArea, QAbstractScrollArea, QWidget#view {
    background: transparent;
}
"""


class ThemeTab(CustomTab):
    """Native theme manager with an always-safe return to the standard UI."""

    def __init__(self):
        """Build theme controls; the face itself belongs to the main window."""
        super().__init__()
        self._companion = None
        self._backdrop = None
        container = QFrame(self)
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        title = QLabel("奶龙主题 · Nailong Theme")
        title.setStyleSheet("font-size: 22px; font-weight: 650;")
        layout.addWidget(title)
        layout.addWidget(QLabel("奶龙大脸作为全窗口背景，切换任务、配置或设置页面时仍会跟随鼠标追视。"))
        actions = QHBoxLayout()
        import_button = QPushButton("导入主题 ZIP 并应用")
        import_button.clicked.connect(self._import_clicked)
        actions.addWidget(import_button)
        reset_button = QPushButton("恢复默认主题")
        reset_button.clicked.connect(self._reset_clicked)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = QLabel("当前：默认主题；导入奶龙 ZIP 后全窗口显示动态大脸。")
        layout.addWidget(self.status)
        self.add_widget(container)
        QTimer.singleShot(0, self._load_saved_theme)

    @property
    def name(self):
        """Expose the theme page in the main navigation."""
        return "奶龙主题"

    @property
    def position(self):
        """Keep theme settings near the built-in application preferences."""
        return NavigationItemPosition.BOTTOM

    @property
    def add_after_default_tabs(self):
        """Separate theme controls from gameplay task tabs."""
        return True

    @property
    def icon(self):
        """Use an icon shipped with the existing Fluent UI dependency."""
        return FluentIcon.PHOTO

    def _ensure_backdrop(self):
        """Attach one non-interactive face below all main-window pages."""
        if self._backdrop is not None:
            return self._backdrop
        parent = self.window()
        if parent is self or parent is None:
            return None
        self._backdrop = NailongBackdrop(parent)
        return self._backdrop

    def _ensure_companion(self):
        """Attach one non-interactive mascot to the real main window."""
        if self._companion is not None:
            return self._companion
        parent = self.window()
        if parent is self or parent is None:
            return None
        self._companion = NailongCompanion(parent, self.executor)
        return self._companion

    def _apply(self, pack):
        """Apply global wallpaper and QSS, or restore the original interface."""
        app = QApplication.instance()
        if app is None:
            raise ThemePackError("Qt application is not available.")
        if not hasattr(app, "_okef_base_style_sheet"):
            app._okef_base_style_sheet = app.styleSheet()
        original = app._okef_base_style_sheet
        if pack is not None or getattr(app, "_okef_theme_applied", False):
            style = original + ("\n" + pack.qss if pack is not None else "")
            if pack is not None and pack.kind == "nailong":
                style += "\n" + _NAILONG_PAGE_QSS
            app.setStyleSheet(style)
        app._okef_theme_applied = pack is not None
        backdrop = self._ensure_backdrop() if pack is not None and pack.kind == "nailong" else self._backdrop
        if backdrop is not None:
            backdrop.set_theme(pack)
        companion = self._ensure_companion() if pack is not None else self._companion
        if companion is not None:
            companion.set_theme(pack)
        self.status.setText(
            f"当前：{pack.name} ({pack.version})" if pack is not None else "当前：默认主题"
        )

    def _load_saved_theme(self):
        """Restore an installed theme after the Qt navigation is constructed."""
        try:
            self._apply(load_active_theme())
        except (ThemePackError, OSError, ValueError) as exc:
            self.status.setText(f"主题加载失败，沿用默认主题：{exc}")

    def _import_clicked(self):
        """Stage and validate a ZIP before installing or activating it."""
        source, _ = QFileDialog.getOpenFileName(self, "选择奶龙主题包", "", "OKEF Theme (*.zip)")
        if not source:
            return
        try:
            pack = import_theme_pack(source)
            select_theme(pack.id)
            self._apply(pack)
        except (ThemePackError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "主题加载失败", str(exc))

    def _reset_clicked(self):
        """Restore the original stylesheet, wallpaper and companion state."""
        try:
            select_theme(None)
            self._apply(None)
        except (ThemePackError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "恢复默认主题失败", str(exc))
