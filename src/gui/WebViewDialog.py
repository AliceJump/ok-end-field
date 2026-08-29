"""内嵌 WebView 对话框组件，用于显示网页内容。"""

from ok.gui.tasks.ConfigCard import og
from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, FluentStyleSheet, PushButton


class WebViewDialog(QDialog):
    """内嵌 WebView 的对话框，用于显示网页内容。"""

    def __init__(self, title: str, url: str, parent=None):
        """
        Args:
            title: 对话框标题
            url: 要加载的网页 URL
            parent: 父窗口
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.url = url
        self._setup_ui()
        self._load_url()

    def _setup_ui(self):
        """设置对话框 UI 布局。"""
        # 设置对话框大小
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 创建工具栏
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)

        # 后退按钮
        self.back_button = PushButton(og.app.tr("后退"))
        self.back_button.setIcon(FluentIcon.LEFT_ARROW)
        self.back_button.clicked.connect(self._go_back)
        toolbar_layout.addWidget(self.back_button)

        # 前进按钮
        self.forward_button = PushButton(og.app.tr("前进"))
        self.forward_button.setIcon(FluentIcon.RIGHT_ARROW)
        self.forward_button.clicked.connect(self._go_forward)
        toolbar_layout.addWidget(self.forward_button)

        # 刷新按钮
        self.refresh_button = PushButton(og.app.tr("刷新"))
        self.refresh_button.setIcon(FluentIcon.SYNC)
        self.refresh_button.clicked.connect(self._refresh)
        toolbar_layout.addWidget(self.refresh_button)

        # 在浏览器中打开按钮
        self.open_in_browser_button = PushButton(og.app.tr("在浏览器中打开"))
        self.open_in_browser_button.setIcon(FluentIcon.LINK)
        self.open_in_browser_button.clicked.connect(self._open_in_browser)
        toolbar_layout.addWidget(self.open_in_browser_button)

        toolbar_layout.addStretch(1)
        main_layout.addWidget(toolbar)

        # 创建 WebView
        self.web_view = QWebEngineView()
        main_layout.addWidget(self.web_view, 1)  # stretch=1 让 WebView 占据剩余空间

        # 连接信号
        self.web_view.titleChanged.connect(self._on_title_changed)
        self.web_view.urlChanged.connect(self._on_url_changed)

        # 应用样式
        try:
            FluentStyleSheet.DIALOG.apply(self)
        except Exception:
            pass

    def _load_url(self):
        """加载指定的 URL。"""
        self.web_view.setUrl(QUrl(self.url))

    def _go_back(self):
        """后退到上一页。"""
        self.web_view.back()

    def _go_forward(self):
        """前进到下一页。"""
        self.web_view.forward()

    def _refresh(self):
        """刷新当前页面。"""
        self.web_view.reload()

    def _open_in_browser(self):
        """在默认浏览器中打开当前页面。"""
        import webbrowser

        webbrowser.open(self.web_view.url().toString())

    def _on_title_changed(self, title: str):
        """页面标题变化时更新对话框标题。"""
        if title:
            self.setWindowTitle(title)

    def _on_url_changed(self, url: QUrl):
        """URL 变化时更新按钮状态。"""
        self.back_button.setEnabled(self.web_view.history().canGoBack())
        self.forward_button.setEnabled(self.web_view.history().canGoForward())

    def closeEvent(self, event):
        """关闭对话框时清理资源。"""
        self.web_view.setUrl(QUrl("about:blank"))
        super().closeEvent(event)


def show_webview_dialog(title: str, url: str, parent=None) -> WebViewDialog:
    """
    显示 WebView 对话框的便捷函数。

    Args:
        title: 对话框标题
        url: 要加载的网页 URL
        parent: 父窗口

    Returns:
        WebViewDialog 实例
    """
    dialog = WebViewDialog(title, url, parent)
    dialog.show()
    return dialog
