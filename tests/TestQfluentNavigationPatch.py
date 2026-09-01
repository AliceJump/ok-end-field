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
当前项为 None、指示项为 None、指示项存在三条路径；测试类结束后恢复方法和
安装标记，并由后置哨兵测试验证，避免污染单进程 discovery 的后续模块。
"""

import unittest

from qfluentwidgets.components.navigation.navigation_panel import NavigationPanel

from src.patches import qfluent_navigation_patch


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
    _cleanup_completed = False

    @classmethod
    def setUpClass(cls):
        cls._original_method = NavigationPanel._onIndicatorAniFinished
        cls._original_flag = qfluent_navigation_patch._PATCH_INSTALLED
        cls.addClassCleanup(cls._assert_and_restore_patch_state)
        qfluent_navigation_patch._PATCH_INSTALLED = False
        qfluent_navigation_patch.install_qfluent_navigation_patch()

    @classmethod
    def _assert_and_restore_patch_state(cls):
        NavigationPanel._onIndicatorAniFinished = cls._original_method
        qfluent_navigation_patch._PATCH_INSTALLED = cls._original_flag
        cls._cleanup_completed = True

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


class TestZQfluentNavigationPatchStateRestored(unittest.TestCase):
    def test_global_state_restored_after_patch_tests(self):
        self.assertTrue(TestQfluentNavigationPatch._cleanup_completed)
        self.assertIs(
            NavigationPanel._onIndicatorAniFinished,
            TestQfluentNavigationPatch._original_method,
        )
        self.assertEqual(
            qfluent_navigation_patch._PATCH_INSTALLED,
            TestQfluentNavigationPatch._original_flag,
        )


if __name__ == "__main__":
    unittest.main()
