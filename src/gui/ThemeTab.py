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

from src.gui.NailongWidget import NailongCompanion, NailongScene
from src.themes.theme_pack import ThemePackError, import_theme_pack, load_active_theme, select_theme


class ThemeTab(CustomTab):
    """Native theme manager with an always-safe return to the standard UI."""

    def __init__(self):
        """Build the importer, the live pupil-tracking preview and reset control."""
        super().__init__()
        self._companion = None
        container = QFrame(self)
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        title = QLabel("奶龙主题 · Nailong Theme")
        title.setStyleSheet("font-size: 22px; font-weight: 650;")
        layout.addWidget(title)
        layout.addWidget(QLabel("支持导入奶龙素材 ZIP、动态眼睛追视、空闲与任务运行形象。"))
        self.preview = NailongScene(container)
        self.preview.setMinimumHeight(330)
        layout.addWidget(self.preview)
        actions = QHBoxLayout()
        import_button = QPushButton("导入主题 ZIP 并应用")
        import_button.clicked.connect(self._import_clicked)
        actions.addWidget(import_button)
        reset_button = QPushButton("恢复默认主题")
        reset_button.clicked.connect(self._reset_clicked)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = QLabel("当前：默认主题；导入奶龙 ZIP 后可在此预览。")
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
        """Apply the style, face and companion, or restore the original appearance."""
        app = QApplication.instance()
        if app is None:
            raise ThemePackError("Qt application is not available.")
        if not hasattr(app, "_okef_base_style_sheet"):
            app._okef_base_style_sheet = app.styleSheet()
        original = app._okef_base_style_sheet
        if pack is not None or getattr(app, "_okef_theme_applied", False):
            app.setStyleSheet(original + ("\n" + pack.qss if pack is not None else ""))
        app._okef_theme_applied = pack is not None
        self.preview.set_theme(pack)
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
        """Restore the original stylesheet, face and idle/working mascot state."""
        try:
            select_theme(None)
            self._apply(None)
        except (ThemePackError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "恢复默认主题失败", str(exc))
