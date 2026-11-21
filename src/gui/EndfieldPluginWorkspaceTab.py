from ok.ui.qt.plugins.imported_tabs import ImportedTabs

from src.gui.EndfieldWorkspaceTab import EndfieldWorkspaceTab


class EndfieldPluginWorkspaceTab(EndfieldWorkspaceTab):
    """插件工作区同时作为 ok-script 的 ImportedTabs 宿主。"""

    def bind_plugins(self, window):
        self.app = window.app
        self.executor = window.executor
        self.imported_tabs = window.imported_tabs
        self.update_navigation_width = window.update_navigation_width
        self.stackedWidget = self.stack
        self.navigationInterface = self
        window._imported_tab_controller = ImportedTabs(self)

    def addSubInterface(self, widget, icon, text, **_kwargs):
        self.add_page(widget, text, icon, key="插件")

    def removeWidget(self, route_key):
        # ImportedTabs 先按名称移除侧栏项，再按 QWidget 从 stackedWidget 移除页面。
        # 本工作区只注册了后者，widgetRemoved 会同步移除对应的顶部页签。
        pass
