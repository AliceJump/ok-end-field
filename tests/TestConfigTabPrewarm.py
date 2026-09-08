import contextlib
import copy
import os
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.patches.conditional_rotation_patch import install_conditional_rotation_patch

# 必须先于 ConfigCard 导入（config_widget 为导入时绑定）。
# 只安装本文件需要的补丁，不装 startup_patches 全量，
# 避免污染后续用例的全局 ctypes / 模块状态。
install_conditional_rotation_patch()

import sys as _sys  # noqa: E402

import ok.gui.tasks.ConfigItemFactory as _factory  # noqa: E402

# 套件中更早的用例可能已导入 ConfigCard（其 config_widget 引用绑定于补丁安装前），
# 强制同步为补丁后的函数，保证 Battle Config 卡片（cond_sequence_editor 类型）可构建。
for _name in ("ok.ui.qt.tasks.ConfigCard", "ok.gui.tasks.ConfigCard"):
    _mod = _sys.modules.get(_name)
    if _mod is not None:
        _mod.config_widget = _factory.config_widget

from ok import og  # noqa: E402

from src.gui.AccountConfigTab import AccountConfigTab  # noqa: E402
from src.gui.GlobalConfigTab import GlobalConfigTab  # noqa: E402


class _FakeApp:
    @staticmethod
    def tr(message, *args):
        return message


class _Executor:
    def __init__(self):
        self.onetime_tasks = []
        self.trigger_tasks = []


def _process_events(app):
    """processEvents，容忍套件中其他用例遗留的已删除控件事件。"""
    with contextlib.suppress(RuntimeError):
        app.processEvents()


def _drain_pending(app, tab, timeout=5.0):
    """推进事件循环直到延迟构建任务排空。"""
    deadline = time.monotonic() + timeout
    while getattr(tab, "_pending_cards", None) and time.monotonic() < deadline:
        _process_events(app)
    _process_events(app)


class TabTestCase(unittest.TestCase):
    """公共基类：共享 QApplication 并在用例结束后清理 tab，避免事件残留。"""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._had_og_app = hasattr(og, "app")
        cls._previous_og_app = getattr(og, "app", None)
        og.app = _FakeApp()

    @classmethod
    def tearDownClass(cls):
        # 恢复 og.app，避免 _FakeApp 泄漏到套件中后续的其他用例
        if cls._had_og_app:
            og.app = cls._previous_og_app
        else:
            delattr(og, "app")

    def tearDown(self):
        _process_events(self.app)


def _register_tab_cleanup(test, tab):
    test.addCleanup(lambda: _dispose_tab(test, tab))


def _dispose_tab(test, tab):
    try:
        tab.hide()
        tab.close()
        tab.deleteLater()
    except RuntimeError:
        return
    _process_events(test.app)


class TestGlobalConfigTabBuild(TabTestCase):
    def _new_tab(self):
        tab = GlobalConfigTab()
        _register_tab_cleanup(self, tab)
        return tab

    def test_show_event_defers_card_building(self):
        """首次切换到全局配置页：瞬间展示，卡片在后续事件循环逐张构建。"""
        tab = self._new_tab()
        tab.show()
        self.assertEqual(tab.vBoxLayout.count(), 0, "showEvent 应立即返回，不构建卡片")
        self.assertTrue(tab._build_scheduled)

        _drain_pending(self.app, tab)
        self.assertEqual(tab._pending_cards, [])
        self.assertEqual(tab.vBoxLayout.count(), 5, "应构建全部 5 张配置卡片")

    def test_build_only_scheduled_once(self):
        tab = self._new_tab()
        tab.show()
        _drain_pending(self.app, tab)
        count = tab.vBoxLayout.count()

        tab.show()  # 重复 showEvent 不应再次构建
        _process_events(self.app)
        self.assertEqual(tab.vBoxLayout.count(), count)
        self.assertEqual(tab._pending_cards, [])

    def test_prewarm_builds_before_show(self):
        """启动空闲预热：未展示过 tab 时也能提前完成构建。"""
        tab = self._new_tab()
        tab._prewarm_build()
        _drain_pending(self.app, tab)
        self.assertEqual(tab.vBoxLayout.count(), 5)


_ACCOUNT_STORE = {
    # 账号覆盖存储是 gitignore 的运行时文件（configs/account_scoped_overrides.json），
    # 测试必须注入固定数据，不能依赖本机是否登录过账号。
    "account_list_text": "tester_a\ntester_b\n",
    "account_registry": {},
    "accounts": {},
    "map_contents": {},
}


class TestAccountConfigTabPrewarm(TabTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        patcher = unittest.mock.patch(
            "src.gui.AccountConfigTab.load_overrides",
            lambda force=False: copy.deepcopy(_ACCOUNT_STORE),
        )
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def _new_tab(self, with_executor=True):
        tab = AccountConfigTab()
        if with_executor:
            tab.executor = _Executor()
        _register_tab_cleanup(self, tab)
        return tab

    def test_prewarm_populates_and_marks_loaded(self):
        tab = self._new_tab()
        tab._prewarm_refresh()

        self.assertTrue(tab._loaded_once)
        items = [tab.task_selector.itemText(i) for i in range(tab.task_selector.count())]
        # 本 PR 的账号页仅含滑索代理；键位配置代理由 feat/account-scoped-key-config 引入
        self.assertTrue(any("Zip Line Config" in item for item in items), "任务选择器应包含滑索配置代理")

    def test_prewarm_skipped_without_executor(self):
        tab = self._new_tab(with_executor=False)
        tab._prewarm_refresh()
        self.assertFalse(tab._loaded_once, "executor 未注入时应留待首次 showEvent")

    def test_first_show_defers_editor_render(self):
        """首次切换到账号页：选择器立即就绪，编辑卡片延后一拍构建。"""
        tab = self._new_tab()
        tab.show()

        self.assertTrue(tab._loaded_once)
        self.assertGreater(tab.task_selector.count(), 0, "任务选择器应在 showEvent 内同步就绪")
        self.assertIsNone(tab.current_task, "编辑卡片应延后构建")

        deadline = time.monotonic() + 5
        while tab.current_task is None and time.monotonic() < deadline:
            _process_events(self.app)
        self.assertIsNotNone(tab.current_task, "事件循环推进后编辑卡片应完成构建")

    def test_repeat_show_uses_cheap_sync(self):
        tab = self._new_tab()
        tab.show()
        deadline = time.monotonic() + 5
        while tab.current_task is None and time.monotonic() < deadline:
            _process_events(self.app)

        tab.show()  # 二次展示走 sync_from_source，不触发 deferred 队列
        self.assertIsNotNone(tab.current_task)


if __name__ == "__main__":
    unittest.main()
