"""Exercise deferred page construction with real Qt visibility and lifetime events."""

import copy
import importlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from src.patches.lazy_task_tab_patch import (
    _BaseConfigView,
    _defer_first_refresh,
    _normalize_rendered_lists,
    _use_base_config_for_task_cards,
    install_lazy_task_tab_patch,
)


def _page_class():
    """A fresh page class with the framework's refresh/card ownership contract."""

    class Card(QLabel):
        def __init__(self, task):
            super().__init__(str(task))
            self.dispose_count = 0

        def dispose(self):
            self.dispose_count += 1

    class Page(QWidget):
        def __init__(self, tasks):
            super().__init__()
            self.source = list(tasks)
            self.tasks = []
            self.card_widgets = []
            self.rendered = []
            self.depth = 0
            self.max_depth = 0
            self.during_render = None
            self.fail_next = False
            self.dispose_count = 0
            self.layout_ = QVBoxLayout(self)
            self.refresh_ui()

        def refresh_ui(self):
            self.depth += 1
            self.max_depth = max(self.depth, self.max_depth)
            try:
                for card in self.card_widgets:
                    self.layout_.removeWidget(card)
                    card.dispose()
                    card.deleteLater()
                self.card_widgets.clear()
                self.tasks = list(self.source)
                self.rendered.append(tuple(self.tasks))
                for task in self.tasks:
                    card = Card(task)
                    self.card_widgets.append(card)
                    self.layout_.addWidget(card)
                    if self.fail_next:
                        self.fail_next = False
                        raise RuntimeError("injected card construction failure")
                if self.during_render is not None:
                    self.during_render()
            finally:
                self.depth -= 1

        def dispose(self):
            self.dispose_count += 1
            for card in self.card_widgets:
                card.dispose()

    return Page


class _QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _drain_events(self):
        for _ in range(4):
            self.app.processEvents()

    def _cleanup_widget(self, widget):
        if isValid(widget):
            widget.hide()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self._drain_events()

    def _new_page(self, tasks=("first",)):
        page_type = _page_class()
        _defer_first_refresh(page_type, lambda page: list(page.source))
        page = page_type(tasks)
        self.addCleanup(self._cleanup_widget, page)
        return page

    def _show_and_build(self, page):
        page.show()
        self._drain_events()


class TestLazyTaskTabs(_QtTestCase):
    def test_hidden_refresh_updates_membership_without_constructing_cards(self):
        page = self._new_page()
        replacement = object()
        for _ in range(3):
            page.source = [replacement]
            page.refresh_ui()
            self._drain_events()

        self.assertEqual(page.tasks, [replacement])
        self.assertEqual(page.rendered, [])
        self.assertEqual(page.card_widgets, [])

        page.show()
        self.assertEqual(page.rendered, [], "showEvent must return before first construction")
        self._drain_events()
        self.assertEqual(page.rendered, [(replacement,)])

    def test_default_page_ready_does_not_construct_unvisited_pages(self):
        stack = QStackedWidget()
        stack.addWidget(QLabel("default page"))
        first = self._new_page(("first page",))
        second = self._new_page(("second page",))
        stack.addWidget(first)
        stack.addWidget(second)
        self.addCleanup(self._cleanup_widget, stack)

        stack.show()
        self._drain_events()
        self.assertTrue(stack.currentWidget().isVisible())
        self.assertEqual(first.rendered, [])
        self.assertEqual(second.rendered, [])

        stack.setCurrentWidget(first)
        self._drain_events()
        self.assertEqual(first.rendered, [("first page",)])
        self.assertEqual(second.rendered, [])

    def test_show_then_hide_does_not_build_until_next_show(self):
        page = self._new_page()
        page.show()
        page.hide()
        self._drain_events()
        self.assertEqual(page.rendered, [])

        self._show_and_build(page)
        self.assertEqual(page.rendered, [("first",)])
        page.hide()
        self._show_and_build(page)
        self.assertEqual(page.rendered, [("first",)])

    def test_built_refresh_remains_synchronous_when_visible_or_hidden(self):
        page = self._new_page()
        self._show_and_build(page)
        first_card = page.card_widgets[0]

        page.source = ["visible update"]
        page.refresh_ui()
        self.assertEqual(page.rendered[-1], ("visible update",))
        self.assertEqual(first_card.dispose_count, 1)
        second_card = page.card_widgets[0]

        page.hide()
        page.source = ["hidden update"]
        page.refresh_ui()
        self.assertEqual(page.rendered[-1], ("hidden update",))
        self.assertEqual(second_card.dispose_count, 1)
        self._drain_events()
        self.assertEqual(len(page.rendered), 3)

    def test_repeated_visible_refresh_before_first_callback_builds_latest_once(self):
        page = self._new_page()
        page.show()
        for name in ("second", "third", "latest"):
            page.source = [name]
            page.refresh_ui()
        self.assertEqual(page.rendered, [])
        self._drain_events()
        self.assertEqual(page.rendered, [("latest",)])

    def test_hidden_dirty_page_disposal_never_constructs_cards(self):
        page = self._new_page()
        page.source = ["replacement"]
        page.refresh_ui()
        page.dispose()
        page.dispose()
        self._show_and_build(page)
        page.refresh_ui()
        self._drain_events()
        self.assertEqual(page.rendered, [])
        self.assertEqual(page.dispose_count, 1)

    def test_disposal_cancels_already_queued_first_construction(self):
        page = self._new_page()
        page.show()
        page.dispose()
        self._drain_events()
        self.assertEqual(page.rendered, [])
        self.assertEqual(page.dispose_count, 1)

    def test_qobject_deletion_cancels_queued_callback_without_dispose_method(self):
        page_type = _page_class()
        del page_type.dispose  # TriggerTaskTab relies on QObject destruction.
        _defer_first_refresh(page_type, lambda page: list(page.source))
        page = page_type(("first",))
        self.addCleanup(self._cleanup_widget, page)
        rendered = page.rendered
        page.show()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(page))
        self._drain_events()
        self.assertEqual(rendered, [])

    def test_failed_first_build_waits_for_explicit_retry_and_releases_partial_cards(self):
        page = self._new_page(("first", "second"))
        page.fail_next = True
        with self.assertLogs("src.patches.lazy_task_tab_patch", level="ERROR"):
            self._show_and_build(page)
        self.assertEqual(len(page.rendered), 1)
        partial_card = page.card_widgets[0]
        self._drain_events()
        self.assertEqual(len(page.rendered), 1, "a failure must not start an automatic retry loop")

        page.source = ["retry"]
        page.refresh_ui()
        self.assertEqual(len(page.rendered), 1)
        self._drain_events()
        self.assertEqual(page.rendered[-1], ("retry",))
        self.assertEqual(partial_card.dispose_count, 1)
        self.assertEqual(len(page.card_widgets), 1)

    def test_built_refresh_failure_still_propagates_and_can_be_retried(self):
        page = self._new_page()
        self._show_and_build(page)
        page.fail_next = True
        with self.assertRaisesRegex(RuntimeError, "injected card"):
            page.refresh_ui()
        page.source = ["retry"]
        page.refresh_ui()
        self.assertEqual(page.rendered[-1], ("retry",))
        self.assertEqual(len(page.card_widgets), 1)

    def test_refresh_during_first_render_is_coalesced_without_recursion(self):
        page = self._new_page()

        def update_tasks():
            page.during_render = None
            page.source = ["reloaded"]
            page.refresh_ui()
            page.refresh_ui()

        page.during_render = update_tasks
        self._show_and_build(page)
        self.assertEqual(page.rendered, [("first",), ("reloaded",)])
        self.assertEqual(page.tasks, ["reloaded"])
        self.assertEqual(page.max_depth, 1)

    def test_reentrant_refresh_of_built_hidden_page_finishes_while_hidden(self):
        page = self._new_page()
        self._show_and_build(page)
        page.hide()

        def update_tasks():
            page.during_render = None
            page.source = ["reloaded while hidden"]
            page.refresh_ui()

        page.during_render = update_tasks
        page.source = ["hidden refresh"]
        page.refresh_ui()
        self.assertEqual(page.rendered[-1], ("hidden refresh",))
        self._drain_events()
        self.assertFalse(page.isVisible())
        self.assertEqual(page.rendered[-1], ("reloaded while hidden",))
        self.assertEqual(len(page.rendered), 3)
        self.assertEqual(page.max_depth, 1)

    def test_installing_twice_preserves_one_refresh_and_one_dispose(self):
        page_type = _page_class()

        def collector(page):
            return list(page.source)

        _defer_first_refresh(page_type, collector)
        _defer_first_refresh(page_type, collector)
        page = page_type(("first",))
        self.addCleanup(self._cleanup_widget, page)
        self._show_and_build(page)
        self.assertEqual(page.rendered, [("first",)])
        card = page.card_widgets[0]
        page.dispose()
        self.assertEqual(page.dispose_count, 1)
        self.assertEqual(card.dispose_count, 1)


class TestBaseConfigView(unittest.TestCase):
    def test_reads_base_values_and_delegates_writes_without_changing_runtime_getter(self):
        class Config(dict):
            def get_default(self, key):
                return {"amount": 3}.get(key)

            def reset_to_default(self):
                self["amount"] = 3

        config = Config(amount=3)
        view = _BaseConfigView(config)
        self.assertEqual(view.get("amount"), 3)
        # Account binding can happen after the UI view was created.
        config._raw_get = config.get
        config.get = lambda key, default=None: 99 if key == "amount" else default
        runtime_getter = config.get
        self.assertEqual(view.get("amount"), 3)
        self.assertEqual(config.get("amount"), 99)
        self.assertEqual(dict(view), {"amount": 3})
        self.assertEqual(view.get_default("amount"), 3)
        view["amount"] = 5
        self.assertEqual(config["amount"], 5)
        view.reset_to_default()
        self.assertEqual(config["amount"], 3)
        self.assertIs(config.get, runtime_getter)

    def test_config_adapter_wraps_task_cards_only_and_preserves_task_identity(self):
        class ConfigCard:
            def __init__(
                self, task, name, config, description, default_config, config_description, config_type, config_icon
            ):
                self.task = task
                self.config = config

        class TaskCard(ConfigCard):
            pass

        config = {"amount": 3}
        task = SimpleNamespace(config=config)
        _use_base_config_for_task_cards(ConfigCard, TaskCard)
        _use_base_config_for_task_cards(ConfigCard, TaskCard)
        card = TaskCard(task, "test", config, "", {}, {}, {}, None)
        global_card = ConfigCard(None, "global", config, "", {}, {}, {}, None)
        self.assertIs(card.task, task)
        self.assertIs(task.config, config)
        self.assertIsInstance(card.config, _BaseConfigView)
        self.assertIs(card.config._config, config)
        self.assertIs(global_card.config, config)


@unittest.skipUnless(os.name == "nt", "Native ok-script imports require Windows")
class TestNativeLazyTaskTabs(_QtTestCase):
    """Exercise the pinned renderers and persistent Config, never a live game."""

    def setUp(self):
        from ok import og
        from ok.core.events import EventBus, communicate
        from ok.ui.qt.tasks.ConfigCard import ConfigCard
        from ok.ui.qt.tasks.OneTimeTaskTab import OneTimeTaskTab
        from ok.ui.qt.tasks.TaskCard import TaskCard
        from ok.ui.qt.tasks.TriggerTaskTab import TriggerTaskTab
        from ok.util.config import Config

        self.og = og
        self.Config = Config
        self.ConfigCard = ConfigCard
        self.OneTimeTaskTab = OneTimeTaskTab
        self.TaskCard = TaskCard
        self.TriggerTaskTab = TriggerTaskTab
        lazy_patch = importlib.import_module("src.patches.lazy_task_tab_patch")
        self.enterContext(
            patch.object(
                lazy_patch, "_write_config_value", getattr(lazy_patch, "_write_config_value", None), create=True
            )
        )
        # Earlier GUI fixtures can leave subscriptions to deleted widgets.
        # Use real, isolated signals so this fixture exercises its own cards.
        events = EventBus()
        for name in ("task", "task_list_updated", "config_validation"):
            self.enterContext(patch.object(communicate, name, getattr(events, name)))
        self.folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(og, "app", SimpleNamespace(tr=lambda message, *args: message), create=True))
        self.executor = SimpleNamespace(
            current_task=None, onetime_tasks=[], trigger_tasks=[], waiting_for_task=lambda task: None
        )
        self.enterContext(patch.object(og, "executor", self.executor, create=True))
        self.enterContext(patch.object(og, "task_manager", SimpleNamespace(imported_scripts={}), create=True))
        for cls, names in (
            (ConfigCard, ("__init__",)),
            (OneTimeTaskTab, ("refresh_ui", "showEvent", "dispose", "_endfield_collect_tasks")),
            (TriggerTaskTab, ("refresh_ui", "showEvent")),
        ):
            for name in names:
                self.enterContext(patch.object(cls, name, getattr(cls, name, None), create=True))

    def _config(self, name, defaults, values=None):
        path = Path(self.folder, f"{name}.json")
        path.write_text(json.dumps(defaults if values is None else values), encoding="utf-8")
        return self.Config(name, copy.deepcopy(defaults), folder=self.folder)

    def _task(self, name, *, group=None, values=None):
        from src.core.base_mixin.account_override_mixin import AccountOverrideMixin

        class Task(AccountOverrideMixin):
            pass

        task = Task()
        task.name = name
        task.description = ""
        task.group_name = group
        task.visible = True
        task.default_config = {"sequence": ["keep"], "amount": 3}
        task.config = self._config(name, task.default_config, values)
        task.config_type = {"sequence": {"options_available": ["keep"]}}
        task.config_description = {}
        task.icon = None
        task.show_create_shortcut = False
        task.enabled = False
        task.running = False
        task.paused = False
        task.current_account_id = "account-1"
        task.current_user = "tester"
        task.start_time = 0
        task.info = {}
        task.enable = lambda: None
        task.disable = lambda: None
        return task

    def _track_page(self, page):
        self.addCleanup(self._cleanup_widget, page)
        self.addCleanup(page.timer.stop)
        return page

    def test_list_cleanup_matches_native_config_content_and_persisted_values(self):
        from ok.core.config_schema import resolve_config_type
        from qfluentwidgets import FluentIcon

        list_keys = (
            "sequence",
            "hidden",
            "_private",
            "inactive",
            "hidden_child",
            "orphan",
            "multi",
            "drop",
            "button_child",
            "cycle_leaf",
        )
        defaults = {key: ["keep"] for key in list_keys}
        defaults.update(flag=False, hidden_parent=False, cycle_a=False, cycle_b=False, scalar=3)
        values = {key: ["obsolete", "keep", "keep"] for key in list_keys}
        values.update(flag=False, hidden_parent=False, cycle_a=False, cycle_b=False, scalar=3)
        types = {key: {"options_available": ["keep"]} for key in list_keys}
        types["hidden"]["hidden"] = True
        types["hidden_child"]["hidden"] = True
        types["flag"] = {"sub_configs": {True: ["inactive", "hidden_child", "cycle_a"], False: []}}
        types["hidden_parent"] = {"hidden": True, "sub_configs": {True: ["orphan"]}}
        types["multi"].update(type="multi_selection", options=["keep"])
        types["drop"]["type"] = "drop_down"
        types["scalar"] = {"options_available": [3]}
        types["action"] = {"type": "button", "sub_configs": {"unused": ["button_child"]}}
        # Use supported boolean controllers for the cycle. ModifyListItem has
        # a PushButton rather than the checkedChanged signal required of a
        # switch controller; the separate list leaf exercises cleanup.
        types["cycle_a"] = {"sub_configs": {True: ["cycle_b", "cycle_leaf"]}}
        types["cycle_b"] = {"sub_configs": {True: ["cycle_a"]}}

        eager = self._config("eager", defaults, values)
        deferred = self._config("deferred", defaults, values)
        card = self.ConfigCard(None, "list cleanup", eager, "", defaults, {}, types, FluentIcon.INFO)
        self.addCleanup(self._cleanup_widget, card)
        _normalize_rendered_lists(deferred, defaults, types, resolve_config_type)

        self.assertEqual(dict(deferred), dict(eager))
        self.assertEqual(
            json.loads(Path(deferred.config_file).read_text(encoding="utf-8")),
            json.loads(Path(eager.config_file).read_text(encoding="utf-8")),
        )
        self.assertEqual(deferred["sequence"], ["keep", "keep"], "duplicates must be preserved")
        self.assertEqual(deferred["inactive"], ["keep", "keep"])
        self.assertEqual(deferred["button_child"], ["keep", "keep"])
        self.assertEqual(deferred["cycle_leaf"], ["keep", "keep"])
        for key in ("hidden", "_private", "orphan", "multi"):
            self.assertEqual(deferred[key], values[key])

        from src.core.BattleConfig import BATTLE_CONFIG_MODE_KEY, KEY_SKILL_RELEASE
        from src.gui.EndfieldConfigContent import visible_config_types

        defaults = {BATTLE_CONFIG_MODE_KEY: False, KEY_SKILL_RELEASE: ["1"]}
        values = {BATTLE_CONFIG_MODE_KEY: False, KEY_SKILL_RELEASE: ["obsolete", "1"]}
        eager = self._config("battle_eager", defaults, values)
        deferred = self._config("battle_deferred", defaults, values)
        types = visible_config_types(None, eager, {KEY_SKILL_RELEASE: {"options_available": ["1"]}})
        card = self.ConfigCard(None, "shared combat", eager, "", defaults, {}, types, FluentIcon.INFO)
        self.addCleanup(self._cleanup_widget, card)
        _normalize_rendered_lists(deferred, defaults, types, resolve_config_type)
        self.assertEqual(dict(deferred), dict(eager))
        self.assertEqual(deferred[KEY_SKILL_RELEASE], values[KEY_SKILL_RELEASE])

    def test_first_show_while_running_reads_base_and_keeps_runtime_overrides(self):
        from ok import TriggerTask
        from ok.core.events import communicate

        lock_patch = importlib.import_module("src.patches.task_config_lock_patch")
        self.enterContext(patch.object(self.TaskCard, "update_buttons", self.TaskCard.update_buttons))
        self.enterContext(patch.object(TriggerTask, "after_init", TriggerTask.after_init))
        self.enterContext(patch.object(lock_patch, "_PATCH_INSTALLED", False))
        lock_patch.install_task_config_lock_patch()
        _use_base_config_for_task_cards(self.ConfigCard, self.TaskCard)
        _defer_first_refresh(self.OneTimeTaskTab, lambda page: list(self.executor.onetime_tasks))

        task = self._task("running")
        self.executor.onetime_tasks = [task]
        page = self._track_page(self.OneTimeTaskTab())
        self.assertEqual(page.card_widgets, [])
        self.assertTrue(page.in_current_list(task))
        self.enterContext(
            patch(
                "src.core.base_mixin.account_override_mixin.get_account_task_overrides",
                return_value={"amount": 99, "sequence": ["obsolete account value"]},
            )
        )
        task._bind_account_aware_config_get()
        task.running = True
        task.enabled = True
        runtime_getter = task.config.get
        saved = Path(task.config.config_file).read_text(encoding="utf-8")
        self._show_and_build(page)

        self.assertEqual(len(page.card_widgets), 1)
        card = page.card_widgets[0]
        amount = card.config_widget_by_key["amount"]
        self.assertEqual(amount.spin_box.value(), 3)
        self.assertFalse(amount.isEnabled())
        self.assertEqual(card.config.get("sequence"), ["keep"])
        self.assertEqual(card.config_widget_by_key["sequence"].list_text.text(), "keep")
        self.assertEqual(task.config.get("amount"), 99)
        self.assertIs(task.config.get, runtime_getter)
        self.assertIs(card.task, task)
        self.assertEqual(Path(task.config.config_file).read_text(encoding="utf-8"), saved)

        task.running = False
        communicate.task.emit(task)
        self.assertTrue(amount.isEnabled())
        self.assertEqual(amount.spin_box.value(), 3)
        self.assertEqual(Path(task.config.config_file).read_text(encoding="utf-8"), saved)

    def test_theme_grouping_matrix_skip_and_reloaded_task_routes(self):
        from ok.core.events import communicate

        matrix_module = importlib.import_module("src.gui.components.EndfieldSectionMatrix")
        from src.patches.endfield_theme_patch import patch_onetime_task_tab

        group = next(iter(matrix_module.MATRIX_GROUPS))
        values = {"sequence": ["obsolete", "keep"], "amount": 3}
        first = self._task("first", group=group, values=values)
        second = self._task("second", group=group, values=values)
        outside = self._task("outside", group=group, values=values)
        ordinary = self._task("ordinary", group="audit", values=values)
        trigger = self._task("trigger", values=values)
        self.executor.onetime_tasks = [second, outside, ordinary, first]
        self.executor.trigger_tasks = [trigger]

        class Matrix(QWidget):
            def __init__(self, name, tasks, parent):
                super().__init__(parent)
                self.tasks = list(tasks)

            def dispose(self):
                pass

        self.enterContext(
            patch.object(matrix_module, "daily_group_map", return_value={"first": group, "second": group})
        )
        self.enterContext(patch.object(matrix_module, "EndfieldSectionMatrix", Matrix))
        patch_onetime_task_tab()
        install_lazy_task_tab_patch()
        matrix_page = self._track_page(self.OneTimeTaskTab(False, group))
        ordinary_page = self._track_page(self.OneTimeTaskTab(False, "audit"))
        trigger_page = self._track_page(self.TriggerTaskTab())
        self.assertEqual(matrix_page.tasks, [first, second])
        for page in (matrix_page, ordinary_page, trigger_page):
            self.assertEqual(page.card_widgets, [])
        self.assertEqual(first.config["sequence"], values["sequence"], "matrix forms were already lazy")
        self.assertEqual(ordinary.config["sequence"], ["keep"])
        self.assertEqual(trigger.config["sequence"], ["keep"])

        replacement = self._task("replacement", group="audit")
        self.executor.onetime_tasks = [first, replacement, second]
        communicate.task_list_updated.emit()
        self.assertEqual(ordinary_page.tasks, [replacement])
        self.assertEqual(ordinary_page.card_widgets, [])
        self._show_and_build(ordinary_page)
        self.assertEqual([card.task for card in ordinary_page.card_widgets], [replacement])
        self.assertEqual(matrix_page.card_widgets, [])
        self._show_and_build(matrix_page)
        self.assertEqual(matrix_page.card_widgets[0].tasks, [first, second])
        self._show_and_build(trigger_page)
        self.assertEqual([card.task for card in trigger_page.card_widgets], [trigger])

    def test_first_matrix_show_and_edits_keep_base_roster_while_account_is_running(self):
        from src.core.base_mixin.account_override_mixin import AccountOverrideMixin
        from src.gui.components.EndfieldTacticalDrawer import ConfigSyncBus
        from src.patches.endfield_theme_patch import patch_onetime_task_tab

        matrix_module = importlib.import_module("src.gui.components.EndfieldSectionMatrix")
        checkpoint_module = importlib.import_module("src.gui.components.EndfieldCheckpointCard")
        sync = ConfigSyncBus()
        self.enterContext(patch.object(matrix_module, "sync_bus", sync))
        self.enterContext(patch.object(checkpoint_module, "sync_bus", sync))

        class DailyTask(AccountOverrideMixin):
            CFG_BOAT_TASKS = "帝江号任务"
            BOAT_TASKS = ("送礼", "帝江号整理", "帝江号收菜")

        daily = DailyTask()
        daily.name = "matrix_daily"
        daily.default_config = {daily.CFG_BOAT_TASKS: ["送礼", "帝江号整理"]}
        daily.config = self._config(daily.name, daily.default_config)
        daily.running = False
        daily.current_account_id = "account-1"
        daily.current_user = "tester"
        tasks = [self._task(name, group="帝江号") for name in daily.BOAT_TASKS]
        self.executor.onetime_tasks = [daily, *tasks]
        patch_onetime_task_tab()
        install_lazy_task_tab_patch()
        page = self._track_page(self.OneTimeTaskTab(False, "帝江号"))
        self.assertEqual(page.card_widgets, [])
        self.assertEqual(page.tasks, tasks)

        overrides = {daily.CFG_BOAT_TASKS: []}
        self.enterContext(
            patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides", return_value=overrides)
        )
        daily._bind_account_aware_config_get()
        daily.running = True
        runtime_getter = daily.config.get
        config_path = Path(daily.config.config_file)
        saved = config_path.read_text(encoding="utf-8")
        persist = self.enterContext(patch.object(daily.config, "save_file", wraps=daily.config.save_file))
        self.assertEqual(runtime_getter(daily.CFG_BOAT_TASKS), [])

        self._show_and_build(page)
        self.assertEqual(len(page.card_widgets), 1)
        matrix = page.card_widgets[0]
        self.assertIsInstance(matrix, matrix_module.EndfieldSectionMatrix)
        checks = {card.task_name: card.checkbox for card in matrix.checkpoint_cards}
        self.assertEqual(
            {name: box.isChecked() for name, box in checks.items()},
            {"送礼": True, "帝江号整理": True, "帝江号收菜": False},
        )
        self.assertEqual(config_path.read_text(encoding="utf-8"), saved)
        persist.assert_not_called()

        checks["帝江号收菜"].click()
        self.assertEqual(daily.config[daily.CFG_BOAT_TASKS], ["送礼", "帝江号整理", "帝江号收菜"])
        checks["送礼"].click()
        self.assertEqual(daily.config[daily.CFG_BOAT_TASKS], ["帝江号整理", "帝江号收菜"])
        self.assertEqual(
            json.loads(config_path.read_text(encoding="utf-8"))[daily.CFG_BOAT_TASKS],
            ["帝江号整理", "帝江号收菜"],
        )

        daily.config[daily.CFG_BOAT_TASKS] = ["送礼"]
        saved = config_path.read_text(encoding="utf-8")
        persist.reset_mock()
        sync.config_changed.emit(daily.name)
        self.assertEqual(
            {name: box.isChecked() for name, box in checks.items()},
            {"送礼": True, "帝江号整理": False, "帝江号收菜": False},
        )
        self.assertEqual(config_path.read_text(encoding="utf-8"), saved, "sync must not write configuration")
        persist.assert_not_called()

        checks["送礼"].click()
        self.assertEqual(daily.config[daily.CFG_BOAT_TASKS], [], "the last base selection must be removable")
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))[daily.CFG_BOAT_TASKS], [])
        self.assertIs(daily.config.get, runtime_getter)
        self.assertEqual(daily.config.get(daily.CFG_BOAT_TASKS), [])
        self.assertEqual(overrides, {daily.CFG_BOAT_TASKS: []})

        validated = []

        def reject_selection(key, value):
            validated.append((key, list(value)))
            return False, "fixture validation rejection"

        daily.config.validator = reject_selection
        saved = config_path.read_text(encoding="utf-8")
        persist.reset_mock()
        checks["帝江号整理"].click()
        self.assertEqual(validated, [(daily.CFG_BOAT_TASKS, ["帝江号整理"])])
        self.assertEqual(daily.config[daily.CFG_BOAT_TASKS], [])
        self.assertEqual(config_path.read_text(encoding="utf-8"), saved)
        persist.assert_not_called()

    def test_running_list_cleanup_persists_when_override_equals_cleaned_base(self):
        from ok.core.config_schema import resolve_config_type

        task = self._task("running_list_cleanup", values={"sequence": ["obsolete", "keep"], "amount": 3})
        overrides = {"sequence": ["keep"]}
        self.enterContext(
            patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides", return_value=overrides)
        )
        task._bind_account_aware_config_get()
        task.running = True
        task.enabled = True
        runtime_getter = task.config.get
        config_path = Path(task.config.config_file)
        validated = []

        def validate(key, value):
            validated.append((key, list(value)))
            return True, ""

        task.config.validator = validate
        persist = self.enterContext(patch.object(task.config, "save_file", wraps=task.config.save_file))
        self.executor.onetime_tasks = [task]
        install_lazy_task_tab_patch()
        page = self._track_page(self.OneTimeTaskTab())
        self.assertEqual(page.card_widgets, [])
        self.assertEqual(task.config["sequence"], ["keep"])
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["sequence"], ["keep"])
        self.assertEqual(validated, [("sequence", ["keep"])])
        persist.assert_called_once()

        # A base edit after preparation must also be cleaned by the real
        # ModifyListItem constructed on first show, through the same view.
        task.config["sequence"] = ["obsolete", "keep"]
        validated.clear()
        persist.reset_mock()
        self._show_and_build(page)
        self.assertEqual(len(page.card_widgets), 1)
        self.assertIsInstance(page.card_widgets[0], self.TaskCard)
        self.assertEqual(task.config["sequence"], ["keep"])
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["sequence"], ["keep"])
        self.assertEqual(validated, [("sequence", ["keep"])])
        persist.assert_called_once()

        task.config["sequence"] = ["obsolete", "keep"]
        saved = config_path.read_text(encoding="utf-8")
        validated.clear()
        persist.reset_mock()

        def reject(key, value):
            validated.append((key, list(value)))
            return False, "fixture validation rejection"

        task.config.validator = reject
        _normalize_rendered_lists(task.config, task.default_config, task.config_type, resolve_config_type)
        self.assertEqual(validated, [("sequence", ["keep"])])
        self.assertEqual(task.config["sequence"], ["obsolete", "keep"])
        self.assertEqual(config_path.read_text(encoding="utf-8"), saved)
        persist.assert_not_called()
        self.assertIs(task.config.get, runtime_getter)
        self.assertEqual(runtime_getter("sequence"), ["keep"])
        self.assertEqual(overrides, {"sequence": ["keep"]})


if __name__ == "__main__":
    unittest.main()
