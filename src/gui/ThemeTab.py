"""Opt-in project theme management; no upstream UI fork required."""

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

from src.themes.theme_pack import ThemePackError, import_theme_pack, load_active_theme, select_theme


class ThemeTab(CustomTab):
    def __init__(self):
        """Build the theme import controls and schedule saved-theme restoration."""
        super().__init__()
        container = QFrame(self)
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        title = QLabel("主题包 · Theme Packs")
        title.setStyleSheet("font-size: 22px; font-weight: 650;")
        layout.addWidget(title)
        layout.addWidget(QLabel("从 ZIP 导入自定义 QSS 主题。仅支持外观样式，不运行第三方代码。"))
        self.preview = QFrame(container)
        self.preview.setObjectName("ThemePreview")
        self.preview.setMinimumHeight(105)
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.addWidget(QLabel("AURORA  /  极光之夜"))
        preview_layout.addWidget(QLabel("导入示例主题后，此处及界面配色会改变。"))
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
        self.status = QLabel("当前：默认主题")
        layout.addWidget(self.status)
        self.add_widget(container)
        QTimer.singleShot(0, self._load_saved_theme)

    @property
    def name(self):
        """Expose a stable navigation tab label."""
        return "主题"

    @property
    def position(self):
        """Place theme controls next to the application's bottom settings."""
        return NavigationItemPosition.BOTTOM

    @property
    def add_after_default_tabs(self):
        """Keep theme controls separate from core task tabs."""
        return True

    @property
    def icon(self):
        """Use an existing fluent icon supported by the pinned runtime."""
        return FluentIcon.SETTING

    def _apply(self, pack):
        """Apply a theme stylesheet or restore the original application style."""
        app = QApplication.instance()
        if app is None:
            raise ThemePackError("Qt application is not available.")
        if not hasattr(app, "_okef_base_style_sheet"):
            app._okef_base_style_sheet = app.styleSheet()
        original = app._okef_base_style_sheet
        if pack is not None or getattr(app, "_okef_theme_applied", False):
            app.setStyleSheet(original + ("\n" + pack.qss if pack is not None else ""))
        app._okef_theme_applied = pack is not None
        self.status.setText(
            f"当前：{pack.name} ({pack.version})" if pack is not None else "当前：默认主题"
        )

    def _load_saved_theme(self):
        """Load the persisted selection without blocking application startup."""
        try:
            self._apply(load_active_theme())
        except (ThemePackError, OSError) as exc:
            self.status.setText(f"主题加载失败，沿用默认主题：{exc}")

    def _import_clicked(self):
        """Validate, install, select, and immediately apply a chosen ZIP."""
        source, _ = QFileDialog.getOpenFileName(self, "选择主题包", "", "OKEF Theme (*.zip)")
        if not source:
            return
        try:
            pack = import_theme_pack(source)
            select_theme(pack.id)
            self._apply(pack)
        except (ThemePackError, OSError) as exc:
            QMessageBox.warning(self, "主题加载失败", str(exc))

    def _reset_clicked(self):
        """Remove the saved selection and restore the current launch's style."""
        try:
            select_theme(None)
            self._apply(None)
        except (ThemePackError, OSError) as exc:
            QMessageBox.warning(self, "恢复默认主题失败", str(exc))
