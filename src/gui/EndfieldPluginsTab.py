"""One navigation destination for plugin management and plugin pages."""

from qfluentwidgets import FluentIcon

from src.gui.EndfieldWorkspaceTab import EndfieldWorkspaceTab


class EndfieldPluginsTab(EndfieldWorkspaceTab):
    def __init__(self, parent=None):
        super().__init__("PLUGINS", "插件", [], parent)

    def add_page(self, widget, title):
        if self.has_child(widget):
            return
        self.child_tabs.append((title, FluentIcon.APPLICATION, widget))
        self._page_containers[widget] = widget
        self.tab_bar.add_tab(title, FluentIcon.APPLICATION, "插件")
        self.stack.addWidget(widget)
        widget.destroyed.connect(self._sync_pages)

    def _sync_pages(self):
        from shiboken6 import isValid

        if not isValid(self) or not isValid(self.tab_bar):
            return
        self.child_tabs[:] = [entry for entry in self.child_tabs if isValid(entry[2])]
        self._page_containers = {widget: widget for _, _, widget in self.child_tabs}
        for button in self.tab_bar.buttons:
            self.tab_bar.layout.removeWidget(button)
            button.deleteLater()
        self.tab_bar.buttons.clear()
        for title, icon, widget in self.child_tabs:
            self.tab_bar.add_tab(title, icon, "插件")
        self.tab_bar.set_current_index(max(0, self.stack.currentIndex()))
