# -*- coding: utf-8 -*-
"""qfluentwidgets 导航面板窗口显示前 expand 崩溃补丁的单元测试。

背景
----
ok 库 MainWindow.set_window_size 在窗口显示前调用
navigationInterface.expand(False)，qfluentwidgets NavigationPanel.expand 内部
走 _stopIndicatorAnimation -> _onIndicatorAniFinished，该方法对
_findIndicatorItem(item) 的结果直接调用 setAboutSelected(False)。窗口尚未显示
时导航项 isVisible() 全为 False，_findIndicatorItem 沿父链找不到可见项返回
None，启动即抛 AttributeError。

补丁在 _onIndicatorAniFinished 中对 _findIndicatorItem 结果判空，找不到可见
指示项时跳过 setAboutSelected（此时没有正在显示的指示器）。

隔离策略
--------
不构造 Qt 窗口，只安装补丁后用假 panel 对象调用被打补丁的方法，覆盖
当前项为 None、指示项为 None、指示项存在三条路径。
"""
import unittest

from qfluentwidgets.components.navigation.navigation_panel import NavigationPanel

from src.patches.qfluent_navigation_patch import install_qfluent_navigation_patch


class _FakeIndicator:
    def __init__(self):
        self.hidden = False

    def hide(self):
        self.hidden = True


class _FakeWidget:
    def __init__(self):
        self.selected = None
        self.about_selected = None

    def setSelected(self, value):
        self.selected = value

    def setAboutSelected(self, value):
        self.about_selected = value


class _FakePanel:
    def __init__(self, item, indicator_item=None):
        self.item = item
        self.indicator_item = indicator_item
        self.indicator = _FakeIndicator()

    def currentItem(self):
        return self.item

    def _findIndicatorItem(self, item):
        return self.indicator_item


class TestQfluentNavigationPatch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._original = NavigationPanel._onIndicatorAniFinished
        install_qfluent_navigation_patch()

    def test_no_crash_when_indicator_item_none(self):
        # 窗口未显示时 _findIndicatorItem 返回 None，原方法会抛 AttributeError
        panel = _FakePanel(_FakeWidget(), indicator_item=None)
        NavigationPanel._onIndicatorAniFinished(panel)
        self.assertTrue(panel.indicator.hidden)

    def test_set_about_selected_false_when_indicator_item_found(self):
        item = _FakeWidget()
        indicator_item = _FakeWidget()
        panel = _FakePanel(item, indicator_item=indicator_item)
        NavigationPanel._onIndicatorAniFinished(panel)
        self.assertTrue(item.selected)
        self.assertFalse(indicator_item.about_selected)
        self.assertTrue(panel.indicator.hidden)

    def test_return_early_when_no_current_item(self):
        panel = _FakePanel(None, indicator_item=_FakeWidget())
        NavigationPanel._onIndicatorAniFinished(panel)
        self.assertFalse(panel.indicator.hidden)


if __name__ == "__main__":
    unittest.main()