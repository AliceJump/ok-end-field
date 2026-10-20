"""Endfield layout regressions; run with the bundled Python and Qt offscreen."""

import os
import unittest
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok import og
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QRect, Qt  # type: ignore
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QFontMetrics  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)
from qfluentwidgets import SwitchButton, Theme, qconfig  # type: ignore

from src.core.BattleConfig import (
    BATTLE_CONFIG_MODE_KEY,
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
    KEY_COMPLETE_NOTIFY,
    KEY_COND_SEQUENCE,
    KEY_LEGACY_COMBAT_MODE,
    KEY_TIMING_DECISION_WINDOW,
    KEY_TIMING_ROTATION,
)
from src.gui.components.EndfieldCheckpointCard import extract_strategy_tags
from src.gui.components.EndfieldSectionMatrix import EndfieldSectionMatrix
from src.gui.components.EndfieldTacticalDrawer import (
    DrawerConfigForm,
    EndfieldTacticalDrawer,
    sync_bus,
)
from src.gui.EndfieldCombatTab import (
    CombatEditBuffer,
    CombatTarget,
    EndfieldCombatTab,
    resolve_battle_values,
)
from src.gui.EndfieldWorkspaceTab import EndfieldWorkspaceTab
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.patches.endfield_theme_patch import (
    patch_about_content_layout,
    patch_combat_config_forms,
    patch_config_row_background,
    patch_widget_paint_bridge,
)
from src.tasks.mixin.battle_mixin import BattleMixin


def test_application():
    app = QApplication.instance() or QApplication([])
    # Windows offscreen has no platform font database; use the same faces as the real UI.
    if not QFontDatabase.families():
        for filename in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc"):
            path = Path("C:/Windows/Fonts") / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        app.setFont(QFont("Segoe UI", 9))
    return app


class SectionMatrixLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = test_application()

    def setUp(self):
        self.executor_patch = patch.object(og, "executor", SimpleNamespace(onetime_tasks=[]))
        self.executor_patch.start()
        self.previous_theme = qconfig.theme
        tasks = [SimpleNamespace(name=name, config={}) for name in ("送礼", "帝江号整理", "帝江号收菜")]
        self.matrix = EndfieldSectionMatrix("帝江号", tasks)
        self.matrix.resize(1000, self.matrix.height())
        self.matrix.show()
        self.flush_events()

    def tearDown(self):
        self.matrix.close()
        self.matrix.deleteLater()
        qconfig.set(qconfig.themeMode, self.previous_theme, save=False)
        self.flush_events()
        self.executor_patch.stop()

    def flush_events(self):
        for _ in range(8):
            self.app.processEvents()

    def test_daily_config_change_rereads_the_check_rows(self):
        class DailyTask(SimpleNamespace):
            CFG_BOAT_TASKS = "帝江号任务"
            BOAT_TASKS = ("送礼", "帝江号整理", "帝江号收菜")

        daily = DailyTask(name="日常任务", config={"帝江号任务": ["送礼"]})
        og.executor.onetime_tasks = [daily, *self.matrix.tasks]
        checks = [card.checkbox for card in self.matrix.checkpoint_cards]
        self.assertEqual([box.isChecked() for box in checks], [False, False, False])
        sync_bus.config_changed.emit("送礼")
        self.assertEqual([box.isChecked() for box in checks], [False, False, False])
        sync_bus.config_changed.emit("日常任务")
        self.assertEqual([box.isChecked() for box in checks], [True, False, False])
        # 回显不能反过来改写配置
        self.assertEqual(daily.config, {"帝江号任务": ["送礼"]})
        self.assertTrue(self.matrix.count_tag.text().endswith("1 / 3"))

    def test_section_banners_keep_native_and_wrapped_page_routes(self):
        from PySide6.QtWidgets import QVBoxLayout

        from src.gui.EndfieldEntryCards import EndfieldSectionBanner

        native: Any = QWidget()
        native.vBoxLayout = QVBoxLayout(native)
        plain = QWidget()
        workspace = EndfieldWorkspaceTab(
            "COMBAT",
            "业务与战斗",
            [
                ("大世界", None, native),
                ("自动战斗", None, plain),
            ],
        )
        self.assertIs(workspace.stack.widget(0), native)
        self.assertIsInstance(native.vBoxLayout.itemAt(0).widget(), EndfieldSectionBanner)
        self.assertEqual(native.findChild(QLabel, "bannerCode").text(), "// SECTION 01 // OPEN WORLD")
        self.assertEqual(workspace.eyebrow_label.text(), "// OPERATIONS & COMBAT")
        self.assertTrue(workspace.has_child(plain))
        workspace.switch_to_child(plain)
        self.assertEqual(workspace.stack.currentIndex(), 1)
        self.assertIsNot(workspace.stack.currentWidget(), plain)
        self.assertIs(plain.parentWidget(), workspace.stack.currentWidget())
        self.assertEqual(
            workspace.stack.currentWidget().findChild(QLabel, "bannerCode").text(), "// SECTION 02 // AUTO COMBAT"
        )
        daily: Any = QWidget()
        daily.vBoxLayout = QVBoxLayout(daily)
        daily.taskCardLayout = QVBoxLayout()
        daily.vBoxLayout.addLayout(daily.taskCardLayout)
        daily.vBoxLayout.setSpacing(6)
        daily.taskCardLayout.setSpacing(6)
        daily_workspace = EndfieldWorkspaceTab("DAILY", "日常一条龙", [("帝江号", None, daily)])
        self.assertEqual(daily.vBoxLayout.spacing(), 12)
        self.assertEqual(daily.taskCardLayout.spacing(), 12)
        self.assertFalse(hasattr(daily, "_endfield_numbering"))
        daily_workspace.deleteLater()
        workspace.deleteLater()

    def test_entry_numbers_follow_visible_layout_and_dynamic_cards(self):
        from ok.ui.qt.widget.Card import Card
        from PySide6.QtWidgets import QVBoxLayout
        from qfluentwidgets import (
            ExpandSettingCard,
            FluentIcon,
            SettingCard,
            SettingCardGroup,
        )

        from src.gui.EndfieldTheme import palette

        patch_widget_paint_bridge()
        page: Any = QWidget()
        page.setObjectName("numbered-page")
        page.vBoxLayout = QVBoxLayout(page)
        group = SettingCardGroup("设置", page)
        first: Any = SettingCard(FluentIcon.SETTING, "第一项", parent=group)
        second: Any = ExpandSettingCard(FluentIcon.SETTING, "第二项", parent=group)
        group.addSettingCard(first)
        group.addSettingCard(second)
        panel: Any = Card("面板", QWidget(), parent=page)
        page.vBoxLayout.addWidget(group)
        page.vBoxLayout.addWidget(panel)
        workspace = EndfieldWorkspaceTab("ENVIRONMENT", "系统与环境", [("设置", None, page)])
        workspace.resize(800, 600)
        workspace.show()
        self.flush_events()
        self.assertEqual(page.findChild(QLabel, "bannerCode").text(), "// SECTION 01 // SETTINGS")
        self.assertEqual([card._endfield_entry_index.text() for card in (first, second, panel)], ["01", "02", "03"])
        self.assertIs(second._endfield_entry_index, second.card._endfield_entry_index)
        self.assertEqual(group.cardLayout.spacing(), 0)
        self.assertEqual(page.vBoxLayout.spacing(), 12)
        self.assertEqual(second.y() - first.y() - first.height(), 0)
        # 同组第二行起不画顶线，上一行的底线就是分隔线
        self.assertFalse(getattr(first, "_endfield_join_top", False))
        self.assertTrue(second._endfield_join_top and second.card._endfield_join_top)
        self.assertFalse(getattr(panel, "_endfield_join_top", False))
        colors = palette(qconfig.theme == Theme.DARK)
        self.assertEqual(first.grab().toImage().pixelColor(40, 0), QColor(colors["border"]))
        image = second.card.grab().toImage()
        width, height = image.width(), image.height()
        self.assertEqual(image.pixelColor(40, 0), QColor(colors["panel"]))
        # 左、右、下三条边与独立面板的外框落在同一像素上
        for edge, inner in (
            ((width - 1, height // 2), (width - 2, height // 2)),
            ((40, height - 1), (40, height - 2)),
            ((0, height // 2), (1, height // 2)),
        ):
            self.assertEqual(image.pixelColor(*edge), QColor(colors["border"]))
            self.assertEqual(image.pixelColor(*inner), QColor(colors["panel"]))
        first.hide()
        self.flush_events()
        self.assertEqual([card._endfield_entry_index.text() for card in (second, panel)], ["01", "02"])
        self.assertFalse(second._endfield_join_top)
        third: Any = SettingCard(FluentIcon.SETTING, "新增项", parent=group)
        group.addSettingCard(third)
        third.show()
        self.flush_events()
        self.assertEqual([card._endfield_entry_index.text() for card in (second, third, panel)], ["01", "02", "03"])
        first.show()
        self.flush_events()
        self.assertEqual(
            [card._endfield_entry_index.text() for card in (first, second, third, panel)], ["01", "02", "03", "04"]
        )
        workspace.close()
        workspace.deleteLater()
        self.flush_events()

    def test_entry_numbers_support_native_widgets_with_layout_attributes(self):
        from PySide6.QtWidgets import QVBoxLayout
        from qfluentwidgets import FluentIcon, SettingCard

        patch_widget_paint_bridge()
        page: Any = QWidget()
        page.setObjectName("layout-attribute-page")
        page.layout = QVBoxLayout(page)
        nested: Any = QWidget(page)
        nested.layout = QHBoxLayout(nested)
        entry: Any = SettingCard(FluentIcon.SETTING, "入口", parent=nested)
        nested.layout.addWidget(entry)
        page.layout.addWidget(nested)
        workspace = EndfieldWorkspaceTab("DEVELOPER", "开发者工具", [("工具", None, page)])
        workspace.show()
        self.flush_events()
        self.assertEqual(entry._endfield_entry_index.text(), "01")
        self.assertFalse(entry._endfield_entry_index.isHidden())
        workspace.close()
        workspace.deleteLater()
        self.flush_events()

    def test_task_status_uses_real_state_with_text_and_semantic_color(self):
        from PySide6.QtWidgets import QFrame

        from src.gui.EndfieldEntryCards import update_task_status
        from src.gui.EndfieldTheme import palette

        header: Any = QFrame()
        row = QHBoxLayout(header)
        header.addWidget = row.addWidget
        task = SimpleNamespace(running=False, paused=False, enabled=False)
        card = SimpleNamespace(card=header, task=task, onetime=True)
        for theme in (Theme.LIGHT, Theme.DARK):
            qconfig.set(qconfig.themeMode, theme, save=False)
            for attr, text, role, edge in (
                ("enabled", "等待执行", "muted", "dashed"),
                ("running", "运行中", "text", "solid"),
                ("paused", "已暂停", "signal", "solid"),
            ):
                task.running = task.paused = task.enabled = False
                setattr(task, attr, True)
                update_task_status(card)
                self.assertEqual(card._endfield_status.text(), text)
                self.assertIn(f"1px {edge} {palette(theme == Theme.DARK)[role]}", card._endfield_status.styleSheet())
                other = Theme.DARK if theme == Theme.LIGHT else Theme.LIGHT
                qconfig.set(qconfig.themeMode, other, save=False)
                self.assertIn(f"1px {edge} {palette(other == Theme.DARK)[role]}", card._endfield_status.styleSheet())
                self.assertEqual(card._endfield_status.text(), text)
                qconfig.set(qconfig.themeMode, theme, save=False)
            task.running = task.paused = task.enabled = False
            update_task_status(card)
            self.assertTrue(card._endfield_status.isHidden())
        header.deleteLater()

    def test_bottom_border_survives_late_style_and_theme_changes(self):
        last = self.matrix.checkpoint_cards[-1]
        from src.gui.EndfieldTheme import palette

        for theme, banner_height in (
            (Theme.DARK, 42),
            (Theme.LIGHT, 54),
            (Theme.DARK, 30),
        ):
            with self.subTest(theme=theme, banner_height=banner_height):
                qconfig.set(qconfig.themeMode, theme, save=False)
                self.matrix.setStyleSheet(
                    f"QFrame#compositeBanner {{ min-height: {banner_height}px; border: 1px solid black; }}"
                )
                QApplication.sendEvent(last, QEvent(QEvent.Enter))
                self.flush_events()
                colors = palette(theme == Theme.DARK)
                self.assertLess(last.geometry().bottom(), self.matrix.height() - 1)
                image = self.matrix.grab().toImage()
                center = last.x() + last.width() // 2
                self.assertEqual(image.pixelColor(center, last.geometry().bottom()), QColor(colors["row_hover"]))
                self.assertEqual(image.pixelColor(center, self.matrix.height() - 1), QColor(colors["border"]))
                first = self.matrix.checkpoint_cards[0]
                self.assertEqual(image.pixelColor(center, first.geometry().bottom()), QColor(colors["divider"]))


class CombatSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = test_application()
        patch_combat_config_forms()
        patch_config_row_background()
        patch_widget_paint_bridge()

    def setUp(self):
        self.common = CombatEditBuffer(deepcopy(DEFAULT_BATTLE_CONFIG))
        self.task: Any = SimpleNamespace(
            name="日常刷体力",
            running=False,
            config=CombatEditBuffer({BATTLE_CONFIG_MODE_KEY: False, **deepcopy(DEFAULT_BATTLE_CONFIG)}),
            default_config={},
            config_type={},
            config_description={},
            log_debug=lambda *_: None,
            show_create_shortcut=False,
        )
        self.task._parse_use_independent = lambda value: BattleMixin._parse_use_independent(self.task, value)
        BattleMixin._register_battle_config(self.task)
        self.patches = [
            patch.object(og, "app", SimpleNamespace(tr=lambda value: value)),
            patch.object(
                og,
                "executor",
                SimpleNamespace(
                    onetime_tasks=[self.task],
                    trigger_tasks=[],
                    current_task=None,
                ),
            ),
            patch("src.gui.EndfieldCombatTab.get_global_config", return_value=self.common),
            patch("src.gui.EndfieldCombatTab.load_overrides", return_value={"accounts": {}}),
        ]
        for mock_patch in self.patches:
            mock_patch.start()
        self.page = EndfieldCombatTab()
        # The normal startup patches install the project-specific sequence editor.
        # These tests exercise the native hierarchy without opening that editor.
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page._load_targets()

    def tearDown(self):
        from ok.ui.qt.Communicate import communicate

        communicate.task.disconnect(self.page._on_task_state_changed)  # type: ignore[attr-defined]
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        for mock_patch in reversed(self.patches):
            mock_patch.stop()

    def test_sources_match_runtime_and_keep_saved_values(self):
        key = KEY_COMPLETE_NOTIFY
        self.common[key] = False
        self.task.config[key] = True
        for task_mode, account, expected in (
            (False, {}, False),
            (True, {}, True),
            ("使用独立配置", {}, True),
            (True, {key: "false"}, False),
            (True, {BATTLE_CONFIG_MODE_KEY: False, key: True}, False),
            (False, {BATTLE_CONFIG_MODE_KEY: True, key: "true"}, True),
        ):
            with self.subTest(mode=task_mode, account=account):
                self.task.config[BATTLE_CONFIG_MODE_KEY] = task_mode
                before = deepcopy(dict(self.task.config))
                _, values = resolve_battle_values(self.task, account, self.common)
                self.assertEqual(values[key], expected)
                self.assertEqual(self.task.config, before)

    def test_shared_edits_write_to_the_runtime_store(self):
        assert self.page._editor is not None
        self.page._editor.config[KEY_COMPLETE_NOTIFY] = False
        self.assertFalse(self.common[KEY_COMPLETE_NOTIFY])
        self.assertFalse(resolve_battle_values(self.task, {}, self.common)[1][KEY_COMPLETE_NOTIFY])
        self.assertEqual(extract_strategy_tags(self.task.name, self.task), ["默认参数"])
        self.task.config["体力本"] = "干员经验"
        self.assertEqual(extract_strategy_tags(self.task.name, self.task), ["副本: 干员经验"])

    def test_account_saves_merge_only_changed_battle_keys(self):
        self.task.config[BATTLE_CONFIG_MODE_KEY] = True
        data = {"accounts": {"test-account": {"SimpleNamespace": {"关卡": "stage-a", "_enabled": True}}}}
        self.page._targets.append(CombatTarget("测试范围", self.task, "test-account"))
        with (
            patch("src.gui.EndfieldCombatTab.load_overrides", return_value=data),
            patch("src.gui.EndfieldCombatTab.update_overrides", side_effect=lambda merge: merge(data)),
        ):
            with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
                self.page._select_target(len(self.page._targets) - 1)
            assert self.page._buffer is not None
            self.page._buffer[KEY_COMPLETE_NOTIFY] = False
            self.page._save_settings()
        self.assertEqual(
            data["accounts"]["test-account"]["SimpleNamespace"],
            {
                "关卡": "stage-a",
                "_enabled": True,
                KEY_COMPLETE_NOTIFY: False,
            },
        )
        self.assertTrue(self.task.config[KEY_COMPLETE_NOTIFY])

    def test_reentry_refreshes_account_scopes_and_keeps_the_selection(self):
        data: dict[str, Any] = {"accounts": {}, "account_registry": {"acc-1": {"username": "测试账号"}}}
        account_label = "测试账号 / 日常刷体力"

        def labels():
            return [self.page.target_combo.itemText(i) for i in range(self.page.target_combo.count())]

        with (
            patch("src.gui.EndfieldCombatTab.load_overrides", side_effect=lambda *_args, **_kwargs: data),
            patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}),
        ):
            self.page.show()
            self.page.target_combo.setCurrentIndex(1)
            self.page.hide()
            # 账号页新增覆盖后重新进入：出现账号范围，当前选中的任务范围不变
            data["accounts"]["acc-1"] = {"SimpleNamespace": {BATTLE_CONFIG_MODE_KEY: True}}
            self.page.show()
            self.assertIn(account_label, labels())
            self.assertEqual((self.page._target.task, self.page._target.account_key), (self.task, ""))
            self.page.target_combo.setCurrentIndex(labels().index(account_label))
            self.page.hide()
            # 清空覆盖后重新进入：旧范围移除，选择回到通用设置
            data["accounts"].clear()
            self.page.show()
            self.assertNotIn(account_label, labels())
            self.assertEqual(self.page.target_combo.currentIndex(), 0)
            self.assertIsNone(self.page._target.task)

    def test_runtime_lock_prevents_source_changes(self):
        self.task.config[BATTLE_CONFIG_MODE_KEY] = True
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page._select_target(1)
        self.task.running = True
        self.page._on_task_state_changed()
        assert self.page._editor is not None
        self.assertFalse(self.page._editor.isEnabled())
        self.page._use_shared()
        self.assertTrue(self.task.config[BATTLE_CONFIG_MODE_KEY])

    def test_drawer_operation_buttons_use_the_bundled_translation(self):
        from importlib import import_module

        from PySide6.QtCore import QTranslator

        import_module("ok.ui.qt.resources")  # 注册内置译文 :/i18n
        translator = QTranslator()
        self.assertTrue(translator.load("zh_CN", ":/i18n"))
        self.app.installTranslator(translator)
        self.addCleanup(self.app.removeTranslator, translator)
        self.task.default_config = {"一次送礼个数": 2}
        self.task.show_create_shortcut = True
        self.task.create_shortcut = lambda: None
        form = DrawerConfigForm(self.task)
        self.assertEqual(form.reset_config.text(), "重置配置")
        self.assertIn("添加开始菜单快捷方式", [button.text() for button in form.findChildren(QPushButton)])
        form.deleteLater()

    def test_drawer_hides_shared_fields_and_keeps_task_dependencies(self):
        self.task.config["使用药品"] = False
        self.task.config["药品上限"] = 1
        self.task.config.get_default = self.task.config.get
        self.task.config_type["使用药品"] = {"sub_configs": {True: ["药品上限"]}}
        form = DrawerConfigForm(self.task)
        self.assertNotIn(BATTLE_CONFIG_MODE_KEY, form.config_widget_by_key)
        self.assertFalse(set(DEFAULT_BATTLE_CONFIG) & form.config_widget_by_key.keys())
        child = form.config_widget_by_key["药品上限"]
        self.assertTrue(child.isHidden())
        form.config_widget_by_key["使用药品"].switch_button.setChecked(True)
        self.assertTrue(child.property("subConfig"))
        self.assertTrue(form.config_widget_by_key["使用药品"].property("configFirst"))
        self.assertFalse(child.property("configFirst"))
        self.assertEqual(child.layout.contentsMargins().left(), 38)
        self.assertEqual(form.config_widget_by_key["使用药品"].layout.contentsMargins().left(), 14)
        self.assertFalse(child.isHidden())
        form.deleteLater()

    def test_starred_field_labels_preserve_keys_values_and_dependencies(self):
        from ok.ui.qt.tasks.ConfigCard import ConfigCard

        for drawer in (False, True):
            with self.subTest(drawer=drawer):
                config = CombatEditBuffer({"⭐使用药品": False, "🌟药品上限": 1})
                types = {"⭐使用药品": {"sub_configs": {True: ["🌟药品上限"]}}}
                task = SimpleNamespace(
                    config=config, config_description={}, config_type=types, show_create_shortcut=False
                )
                with patch.object(config, "get_default", side_effect=config.get):
                    form = (
                        DrawerConfigForm(task)
                        if drawer
                        else ConfigCard(None, "★药品设置", config, "", {}, {}, types, None)
                    )
                fields = form.config_widget_by_key
                self.assertEqual(fields["⭐使用药品"].title.text(), "使用药品")
                self.assertEqual(fields["🌟药品上限"].title.text(), "药品上限")
                self.assertTrue(fields["🌟药品上限"].isHidden())
                fields["⭐使用药品"].switch_button.setChecked(True)
                self.assertTrue(config["⭐使用药品"])
                self.assertNotIn("使用药品", config)
                self.assertFalse(fields["🌟药品上限"].isHidden())
                self.assertEqual(set(config), {"⭐使用药品", "🌟药品上限"})
                if not drawer:
                    self.assertEqual(form.card.titleLabel.text(), "药品设置")
                form.deleteLater()

    def test_drawer_and_inline_forms_share_standard_row_geometry(self):
        from ok.ui.qt.tasks.ConfigCard import ConfigCard

        from src.gui.EndfieldTheme import palette
        from src.patches.endfield_theme_patch import _widget_scope_override_qss

        for theme in (Theme.LIGHT, Theme.DARK):
            qconfig.set(qconfig.themeMode, theme, save=False)
            config = CombatEditBuffer({"开关": False})
            with patch.object(config, "get_default", side_effect=config.get):
                form = DrawerConfigForm(
                    SimpleNamespace(config=config, config_description={}, config_type={}, show_create_shortcut=False)
                )
                inline = ConfigCard(None, "设置", config, "", {}, {}, {}, None)
            for panel in (form, inline):
                panel.setStyleSheet(_widget_scope_override_qss(theme == Theme.DARK))
                panel.resize(520, 66)
                panel.show()
            inline.setExpand(True)
            inline.expandAni.setCurrentTime(180)
            self.app.processEvents()
            for panel in (form, inline):
                row = panel.config_widget_by_key["开关"]
                self.assertEqual(row.minimumHeight(), 64)
                self.assertEqual(row.height(), 64)
                margins = row.layout.contentsMargins()
                self.assertEqual((margins.left(), margins.top(), margins.right(), margins.bottom()), (14, 10, 14, 10))
            self.assertEqual(form.grab().toImage().pixelColor(0, 10), QColor(palette(theme == Theme.DARK)["border"]))
            for panel in (form, inline):
                panel.close()
                panel.deleteLater()

    def test_reenabling_specialized_settings_preserves_saved_task_values(self):
        self.common[KEY_COMPLETE_NOTIFY] = False
        self.task.config[BATTLE_CONFIG_MODE_KEY] = True
        self.task.config[KEY_COMPLETE_NOTIFY] = True
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.target_combo.setCurrentIndex(1)
            self.page._use_shared()
            self.page._use_task_settings()
        self.assertTrue(self.task.config[BATTLE_CONFIG_MODE_KEY])
        self.assertTrue(self.task.config[KEY_COMPLETE_NOTIFY])

    def test_shared_writes_are_blocked_when_a_run_starts_without_a_signal(self):
        assert self.page._editor is not None
        executor: Any = og.executor
        executor.current_task = SimpleNamespace(running=True)
        self.page._editor.config[KEY_COMPLETE_NOTIFY] = False
        self.assertTrue(self.common[KEY_COMPLETE_NOTIFY])
        self.page._on_task_state_changed()
        self.assertFalse(self.page._editor.isEnabled())
        self.assertFalse(self.page.target_combo.isEnabled())

    def test_running_scope_switch_keeps_the_pending_edit_buffer(self):
        self.task.config[BATTLE_CONFIG_MODE_KEY] = True
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.target_combo.setCurrentIndex(1)
        assert self.page._buffer is not None
        self.page._buffer[KEY_COMPLETE_NOTIFY] = False
        self.task.running = True
        self.page.target_combo.setCurrentIndex(0)
        self.assertEqual(self.page.target_combo.currentIndex(), 1)
        self.assertFalse(self.page._buffer[KEY_COMPLETE_NOTIFY])
        self.task.running = False
        self.page._save_settings()
        self.assertFalse(self.task.config[KEY_COMPLETE_NOTIFY])

    def test_side_panel_close_saves_task_buffer_and_preserves_run_guard(self):
        from src.gui.components.EndfieldTacticalDrawer import drawer_for

        self.task.config[BATTLE_CONFIG_MODE_KEY] = True
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.show()
            self.app.processEvents()
            self.page.target_combo.setCurrentIndex(1)
            self.app.processEvents()
            assert self.page._editor is not None
            card = self.page._editor
            card.card.expandButton.click()
            drawer, _ = drawer_for(card)
            self.assertIs(drawer._source_card, card)
            card.config_widget_by_key[KEY_COMPLETE_NOTIFY].switch_button.setChecked(False)
            drawer.save_btn.click()
            self.assertFalse(self.task.config[KEY_COMPLETE_NOTIFY])
            self.assertIs(card.view.parentWidget(), card.scrollWidget)
            card.card.expandButton.click()
            card.config_widget_by_key[KEY_COMPLETE_NOTIFY].switch_button.setChecked(True)
            self.task.running = True
            drawer.close_drawer()
            self.assertFalse(self.task.config[KEY_COMPLETE_NOTIFY])
            assert self.page._buffer is not None
            self.assertTrue(self.page._buffer[KEY_COMPLETE_NOTIFY])
            self.task.running = False
            self.page._save_settings()
            self.assertTrue(self.task.config[KEY_COMPLETE_NOTIFY])

    def test_combat_panel_closes_on_reentry_and_scope_changes(self):
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.show()
            self.app.processEvents()
            assert self.page._editor is not None
            self.assertFalse(self.page._editor.isExpand)
            button = self.page._editor.card.expandButton
            self.assertEqual(button.text(), "配置")
            button.click()
            self.page._editor.expandAni.setCurrentTime(180)
            self.assertTrue(self.page._editor.isExpand)
            self.assertEqual(button.text(), "配置")
            self.page.hide()
            self.page.show()
            self.app.processEvents()
            assert self.page._editor is not None
            self.assertFalse(self.page._editor.isExpand)
            self.page._editor.card.expandButton.click()
            self.page._editor.expandAni.setCurrentTime(180)
            self.page.hide()
            self.page.show()
            self.app.processEvents()
            assert self.page._editor is not None
            self.assertFalse(self.page._editor.isExpand)
            self.page.target_combo.setCurrentIndex(1)
            assert self.page._editor is not None
            self.assertFalse(self.page._editor.isExpand)

    def test_combat_page_has_no_empty_task_area_before_its_editor(self):
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.resize(1200, 800)
            self.page.show()
            for _ in range(8):
                self.app.processEvents()
            self.assertFalse(hasattr(self.page, "task_cards_view"))
            assert self.page._editor is not None
            editor = self.page._editor
            self.assertFalse(editor.isExpand)
            editor.setExpand(True)
            editor.expandAni.setCurrentTime(200)
            self.app.processEvents()
            self.assertEqual(editor.height(), editor.card.height())
            self.assertIsNot(editor.view.parentWidget(), editor.scrollWidget)
            editor.setExpand(False)
            self.page._editor.expandAni.setCurrentTime(200)
            self.app.processEvents()
            header = self.page.findChild(QWidget, "combatSettingsHeader")
            assert header is not None
            self.assertLess(self.page._editor.y() - header.geometry().bottom(), 30)
            self.assertEqual(self.page._editor.height(), self.page._editor.card.height())

    def test_expand_reversal_settles_and_clears_hidden_content(self):
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            self.page.resize(1000, 700)
            self.page.show()
            self.app.processEvents()
            card = self.page._editor
            assert card is not None
            for expanded, time_ms in ((False, 40), (True, 60), (False, 20), (True, 90), (False, 180)):
                card.setExpand(expanded)
                card.expandAni.setCurrentTime(time_ms)
                self.app.processEvents()
            self.assertEqual(card.height(), card.card.height())
            self.assertTrue(card.view.isHidden())
            self.assertEqual(card.verticalScrollBar().value(), 0)
            card.setExpand(True)
            card.expandAni.setCurrentTime(60)
            card.hide()
            self.assertEqual(card.expandAni.state(), QAbstractAnimation.Stopped)
            self.assertEqual(card.height(), card.card.height())
            self.assertIs(card.view.parentWidget(), card.scrollWidget)
            card.show()
            card.setExpand(False)
            card.expandAni.setCurrentTime(180)
            self.app.processEvents()
            self.assertEqual(card.height(), card.card.height())
            self.assertTrue(card.view.isHidden())

    def test_drawer_replaces_old_form_and_tracks_host_resize(self):
        host = QWidget()
        host.resize(1200, 700)
        host.show()
        self.task.config["关卡"] = "测试关卡"
        self.task.config.get_default = self.task.config.get
        drawer = EndfieldTacticalDrawer(host)
        drawer.open_subtask(self.task.name, self.task)
        old_form = drawer.form_layout.itemAt(0).widget()
        drawer.open_subtask(self.task.name, self.task)
        self.assertTrue(old_form.isHidden())
        self.assertIsNone(old_form.parent())
        host.resize(1000, 650)
        self.app.processEvents()
        self.assertEqual(drawer.geometry().right(), host.width() - 1)
        self.assertEqual(drawer.height(), host.height())
        drawer.close_drawer()
        self.assertTrue(drawer.isHidden())
        host.close()
        host.deleteLater()

    def test_daily_settings_drawer_edits_master_fields_without_the_roster(self):
        from src.gui.components.EndfieldSectionMatrix import open_daily_settings_drawer

        class DailyTask(SimpleNamespace):
            CFG_BOAT_TASKS = "帝江号任务"
            CFG_TAIL_TASKS = "收尾任务"

        config = CombatEditBuffer(
            {"帝江号任务": ["送礼"], "收尾任务": [], "多账户模式": False, "账号列表": "", "仅退出游戏": False}
        )
        roster_type = {"type": "multi_selection", "options": ["送礼"]}
        daily = DailyTask(
            name="日常任务",
            config=config,
            default_config=dict(config),
            config_description={},
            config_type={
                "帝江号任务": roster_type,
                "收尾任务": {"type": "multi_selection", "options": []},
                "多账户模式": {"sub_configs": {True: ["账号列表"]}},
            },
            show_create_shortcut=False,
        )
        host = QWidget()
        host.resize(1200, 700)
        host.show()
        with (
            patch.object(og.executor, "onetime_tasks", [daily]),
            patch.object(config, "get_default", side_effect=config.get),
        ):
            drawer = open_daily_settings_drawer(host)
        assert drawer is not None
        self.assertIs(drawer.current_task, daily)
        form = drawer.form_layout.itemAt(0).widget()
        self.assertEqual(set(form.config_widget_by_key), {"多账户模式", "账号列表", "仅退出游戏"})
        self.assertEqual(daily.config_type["帝江号任务"], {"type": "multi_selection", "options": ["送礼"]})

        # 重置会改到隐藏的子任务清单，表单要通知勾选行重读
        emitted = []

        def record(name):
            emitted.append(name)

        config["帝江号任务"] = []
        sync_bus.config_changed.connect(record)
        with (
            patch.object(
                config, "reset_to_default", create=True, side_effect=lambda: config.update(daily.default_config)
            ),
            patch.object(config, "get_default", side_effect=config.get),
        ):
            form.reset_config.click()
        sync_bus.config_changed.disconnect(record)
        self.assertEqual(config["帝江号任务"], ["送礼"])
        self.assertEqual(emitted, ["日常任务"])
        drawer.close_drawer()
        host.close()
        host.deleteLater()

    def test_run_once_starts_the_task_through_the_start_controller(self):
        started = []
        drawer = EndfieldTacticalDrawer()
        drawer.current_task = self.task
        start_controller = SimpleNamespace(start=started.append)
        with patch.object(og, "app", SimpleNamespace(tr=lambda value: value, start_controller=start_controller)):
            drawer.run_subtask_now()
        self.assertEqual(started, [self.task])
        drawer.deleteLater()

    def test_multi_selection_wraps_in_one_column_and_keeps_value_binding(self):
        from ok.ui.qt.tasks.LabelAndMultiSelection import LabelAndMultiSelection

        options = ["据点兑换", "买物资", "买卖货和较长的说明文本" * 6]
        config = CombatEditBuffer({"选项": options[:1]})
        row = LabelAndMultiSelection({}, options, config, "选项")
        row.resize(520, 260)
        row.show()
        self.app.processEvents()
        boxes = row.check_boxes
        self.assertEqual(len({box.x() for box in boxes}), 1)
        self.assertEqual(len({box.checkbox.x() for box in boxes}), 1)
        self.assertEqual(len({box.label.x() for box in boxes}), 1)
        self.assertLess(boxes[0].geometry().bottom(), boxes[1].y())
        self.assertTrue(boxes[2].label.wordWrap())
        self.assertGreater(boxes[2].label.height(), boxes[0].label.height())
        boxes[1].checkbox.click()
        self.assertEqual(config["选项"], options[:2])
        row.close()
        row.deleteLater()

    def test_multi_selection_is_a_compact_group_at_the_right_margin(self):
        from ok.ui.qt.tasks.LabelAndMultiSelection import LabelAndMultiSelection

        row = LabelAndMultiSelection({}, ["武陵", "四号谷地"], CombatEditBuffer({"地区": ["武陵"]}), "地区")
        row.resize(900, 100)
        row.show()
        self.app.processEvents()
        column = row.content_layout
        self.assertEqual(row.width() - column.geometry().right() - 1, 14)
        self.assertEqual(column.width(), 180)
        row.close()
        row.deleteLater()

    def test_only_root_rows_have_separators(self):
        from src.patches.endfield_theme_patch import _widget_scope_override_qss

        self.task.config = CombatEditBuffer({"父项": True, "子项": 1, "下一项": False})
        self.task.config_type = {"父项": {"sub_configs": {True: ["子项"]}}}
        with patch.object(self.task.config, "get_default", side_effect=self.task.config.get):
            form = DrawerConfigForm(self.task)
        form.setStyleSheet(_widget_scope_override_qss(False))
        form.resize(650, 300)
        form.show()
        self.app.processEvents()
        fields = form.config_widget_by_key
        from src.gui.EndfieldTheme import LIGHT

        border = QColor(LIGHT["divider"])
        self.assertNotEqual(fields["父项"].grab().toImage().pixelColor(5, 0), border)
        self.assertNotEqual(fields["子项"].grab().toImage().pixelColor(5, 0), border)
        self.assertEqual(fields["下一项"].grab().toImage().pixelColor(5, 0), border)
        form.close()
        form.deleteLater()

    def test_native_task_header_elides_without_losing_full_text(self):
        from ok.ui.qt.tasks.TaskCard import TaskCard

        from src.gui.EndfieldPrimitives import EndfieldElidedLabel

        task: Any = SimpleNamespace(
            name="物品导航",
            description="监听本地 WebSocket 位置数据，指向已选物品的最近点并支持按键标记" * 3,
            config=CombatEditBuffer({}),
            default_config={},
            config_description={},
            config_type={},
            icon=None,
            show_create_shortcut=False,
        )
        with patch.object(TaskCard, "update_buttons"):
            card = TaskCard(task, onetime=False)
        self.assertEqual(card.card.height(), 56)
        self.assertEqual(card.card.expandButton.text(), "配置")
        card.resize(480, card.height())
        card.show()
        self.app.processEvents()
        label = card.card.contentLabel
        self.assertIsInstance(label, EndfieldElidedLabel)
        self.assertEqual(label.text(), task.description)
        self.assertEqual(label.toolTip(), task.description)
        self.assertLess(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        self.assertEqual(label.minimumSizeHint().width(), 0)
        card.close()
        card.deleteLater()

    def test_world_task_hides_only_the_moved_debug_field(self):
        from ok.ui.qt.tasks.ConfigCard import ConfigCard

        task: Any = type("AutoCombatTask", (), {})()
        task.show_create_shortcut = False
        task.config = CombatEditBuffer(
            {
                **deepcopy(DEFAULT_BATTLE_CONFIG),
                BATTLE_CONFIG_MODE_KEY: False,
                KEY_SAVE_ENEMY_PRESENCE_FRAMES: False,
            }
        )
        card = ConfigCard(task, "自动战斗", task.config, "说明", {}, {}, {}, None)
        self.assertNotIn(KEY_SAVE_ENEMY_PRESENCE_FRAMES, card.config_widget_by_key)
        self.assertNotIn(BATTLE_CONFIG_MODE_KEY, card.config_widget_by_key)
        self.assertIn(KEY_SAVE_ENEMY_PRESENCE_FRAMES, task.config)
        card.deleteLater()

    def test_debug_edits_write_back_and_lock_when_a_run_starts(self):
        auto_task: Any = type("AutoCombatTask", (), {})()
        auto_task.config = CombatEditBuffer({KEY_SAVE_ENEMY_PRESENCE_FRAMES: False})
        auto_task.config_description = {}
        executor: Any = og.executor
        executor.trigger_tasks = [auto_task]
        with patch.object(auto_task.config, "get_default", side_effect=auto_task.config.get):
            page = EndfieldCombatTab()
        assert page._debug_editor is not None
        self.assertTrue(page._debug_editor.card.iconLabel.isHidden())
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", {}):
            page._load_targets()
        assert page._editor is not None
        self.assertTrue(page._editor.card.iconLabel.isHidden())
        self.assertFalse(page._debug_editor.isExpand)
        page._debug_editor.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES] = True
        self.assertTrue(auto_task.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES])
        executor.current_task = SimpleNamespace(running=True)
        page._debug_editor.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES] = False
        self.assertTrue(auto_task.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES])
        from ok.ui.qt.Communicate import communicate

        communicate.task.disconnect(page._on_task_state_changed)  # type: ignore[attr-defined]
        page.deleteLater()

    def test_group_styling_accepts_the_real_conditional_editor(self):
        from src.gui.ConditionalRotationPanel import ConditionalRotationPanel
        from src.patches.conditional_rotation_patch import (
            install_conditional_rotation_patch,
        )

        install_conditional_rotation_patch()
        from ok.ui.qt.tasks.ConfigItemFactory import config_widget

        with patch("ok.ui.qt.tasks.ConfigCard.config_widget", config_widget):
            self.page._select_target(0)
        assert self.page._editor is not None
        panel = self.page._editor.config_widget_by_key[KEY_COND_SEQUENCE]
        self.assertIsInstance(panel, ConditionalRotationPanel)
        self.assertEqual(panel._row.layout.contentsMargins().left(), 38)
        self.assertEqual(panel._row.title_layout.spacing(), 6)
        self.assertIs(panel.config, self.page._editor.config)

    def test_combat_page_preserves_the_native_strategy_dependencies(self):
        types = deepcopy(BATTLE_CONFIG_TYPE)
        # Replace only the project editor; keep the real native dependency tree.
        types[KEY_COND_SEQUENCE] = {}
        with patch("src.gui.EndfieldCombatTab.BATTLE_CONFIG_TYPE", types):
            self.page._select_target(0)
        assert self.page._editor is not None
        fields = self.page._editor.config_widget_by_key
        self.assertNotIn(BATTLE_CONFIG_MODE_KEY, fields)
        self.assertTrue(fields[KEY_TIMING_DECISION_WINDOW].isHidden())
        self.assertFalse(fields[KEY_LEGACY_COMBAT_MODE].isHidden())
        fields[KEY_TIMING_ROTATION].switch_button.setChecked(True)
        self.assertFalse(fields[KEY_TIMING_DECISION_WINDOW].isHidden())
        self.assertTrue(fields[KEY_LEGACY_COMBAT_MODE].isHidden())


class NativeTextLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = test_application()
        patch_widget_paint_bridge()
        patch_config_row_background()
        patch_about_content_layout()

    def test_bundled_field_font_renders_without_system_installation(self):
        from src.gui.EndfieldTypography import field_font

        font = field_font(13, label=True)
        self.assertEqual(QFontInfo(font).family(), "Saira")
        self.assertEqual(font.variableAxisValue(QFont.Tag("wdth")), 125.0)
        widths = {QFontMetrics(font).horizontalAdvance(str(digit)) for digit in range(10)}
        self.assertEqual(len(widths), 1)

    def assert_rows_fill_panel(self, panel, rows):
        content = panel.contentsRect()
        bounds = [QRect(row.mapTo(panel, QPoint()), row.size()) for row in rows]
        self.assertEqual(bounds[0].top(), content.top())
        self.assertEqual(bounds[-1].bottom(), content.bottom())
        for row, rect in zip(rows, bounds, strict=True):
            self.assertEqual(rect.left(), content.left())
            self.assertEqual(rect.right(), content.right())
            self.assertEqual(row.layout.contentsMargins().top(), 10)
            self.assertEqual(row.layout.contentsMargins().bottom(), 10)
        for before, after in pairwise(bounds):
            self.assertEqual(before.bottom() + 1, after.top())

    def test_account_rows_reach_panel_edges_without_extra_outer_padding(self):
        from ok.ui.qt.Communicate import communicate

        from src.gui.components.EndfieldTacticalDrawer import drawer_for

        with patch("src.core.global_config_store.get_global_config", return_value=CombatEditBuffer({})):
            from src.gui.AccountConfigTab import AccountConfigTab
        from src.patches.endfield_theme_patch import _widget_scope_override_qss

        with patch.object(og, "app", SimpleNamespace(tr=lambda value: value)):
            page = AccountConfigTab()
            page.show()
            for dark in (False, True):
                page.setStyleSheet(_widget_scope_override_qss(dark))
                for width in (600, 1100):
                    page.resize(width, 800)
                    for card in (page.base_card, page.selector_card):
                        card.card.expandButton.click()
                        for _ in range(8):
                            self.app.processEvents()
                        panel = card.viewLayout.itemAt(0).widget()
                        layout = panel.layout()
                        rows = [layout.itemAt(i).widget() for i in range(layout.count())]
                        self.assert_rows_fill_panel(panel, rows)
                        self.assertTrue(all(row.minimumHeight() == 64 for row in rows))
                        self.assertEqual(card.height(), 56)
                        drawer_for(page)[0].close_drawer()
            communicate.task.disconnect(page._on_task_state_changed)  # type: ignore[attr-defined]
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_notification_rows_reach_panel_edges_without_extra_outer_padding(self):
        from ok.ui.qt.settings.GlobalConfigTab import GlobalConfigTab

        from src.patches.endfield_theme_patch import _widget_scope_override_qss

        patch_combat_config_forms()
        values: Any = CombatEditBuffer({"系统通知": True, "Discord通知": False, "邮件通知": False})
        option: Any = SimpleNamespace(
            name="通知",
            description="",
            default_config=dict(values),
            config_description=dict.fromkeys(values, "通知方式说明"),
            config_type={},
        )
        with (
            patch.object(og, "app", SimpleNamespace(tr=lambda value: value)),
            patch.object(values, "get_default", side_effect=values.get),
        ):
            page = GlobalConfigTab(values, option)
            page.show()
            for dark in (False, True):
                page.setStyleSheet(_widget_scope_override_qss(dark))
                for width in (600, 1100):
                    page.resize(width, 800)
                    for _ in range(8):
                        self.app.processEvents()
                    layout = page.viewLayout
                    rows = [layout.itemAt(i).widget() for i in range(layout.count())]
                    self.assert_rows_fill_panel(page.contentFrame, rows)
                    self.assertEqual({row.height() for row in rows}, {64})
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_project_url_has_the_full_text_and_available_width(self):
        from ok.ui.qt.about.ProjectCard import ProjectCard

        url = "https://github.com/example-name/a-long-project-name"
        card = ProjectCard("项目", url)
        card.resize(1100, 72)
        card.show()
        self.app.processEvents()
        self.assertEqual(card.contentLabel.text(), url)
        self.assertGreater(card.contentLabel.width(), 400)
        card.resize(420, 72)
        self.app.processEvents()
        self.assertNotEqual(card.contentLabel.text(), url)
        self.assertTrue(card.contentLabel.text().endswith("…"))
        self.assertEqual(card.contentLabel.toolTip(), url)
        self.assertLessEqual(
            card.contentLabel.fontMetrics().horizontalAdvance(card.contentLabel.text()), card.contentLabel.width()
        )
        card.resize(1100, 72)
        self.app.processEvents()
        self.assertEqual(card.contentLabel.text(), url)
        card.close()
        card.deleteLater()

    def test_form_description_receives_space_instead_of_the_spacer(self):
        from ok.ui.qt.tasks.LabelAndWidget import LabelAndWidget

        with patch.object(og, "app", SimpleNamespace(tr=lambda value: value)):
            row = LabelAndWidget("设置", "说明文字" * 40)
            row.add_widget(SwitchButton())
            row.resize(1000, 100)
            row.show()
            self.app.processEvents()
            self.assertGreater(row.contentLabel.width(), 700)
            row.close()
            row.deleteLater()

    def test_narrow_workspace_keeps_tabs_and_action_inside_the_header(self):
        opened = []
        workspace = EndfieldWorkspaceTab(
            "DAILY",
            "日常一条龙",
            [(name, None, QWidget()) for name in ("帝江号", "其他", "战斗", "奖励")],
            primary_action=("开始一条龙", lambda: None),
            secondary_action=("一条龙设置", lambda: opened.append(True)),
        )
        workspace.show()
        for width in (1300, 450, 650, 1300):
            workspace.resize(width, 750)
            for _ in range(10):
                self.app.processEvents()
            header = workspace.header_bar
            action = workspace.primary_action_button
            setting = workspace.secondary_action_button
            assert action is not None and setting is not None
            for widget in (*workspace.tab_bar.buttons, setting, action):
                origin = widget.mapTo(header, QPoint(0, 0))
                self.assertTrue(header.rect().contains(QRect(origin, widget.size())))
            # 两个动作同排，设置在开始左侧
            self.assertEqual(setting.y(), action.y())
            self.assertLess(setting.geometry().right(), action.x())
        setting.click()
        self.assertEqual(opened, [True])
        workspace.close()
        workspace.deleteLater()

    def test_developer_tab_name_stays_complete_at_narrow_widths(self):
        workspace = EndfieldWorkspaceTab(
            "DEVELOPER",
            "开发者工具",
            [("工具与调试", None, QWidget())],
        )
        workspace.show()
        button = workspace.tab_bar.buttons[0]
        for width in (1300, 600, 450):
            workspace.resize(width, 700)
            for _ in range(6):
                self.app.processEvents()
            self.assertEqual(
                button.fontMetrics().elidedText(button.text(), Qt.ElideRight, button.width() - 20), button.text()
            )
            origin = button.mapTo(workspace.header_bar, QPoint())
            self.assertTrue(workspace.header_bar.rect().contains(QRect(origin, button.size())))
        workspace.close()
        workspace.deleteLater()

    def test_workspace_tabs_match_the_developer_tab_size_and_wrap_whole_buttons(self):
        workspace = EndfieldWorkspaceTab(
            "ENVIRONMENT",
            "系统与环境",
            [(name, None, QWidget()) for name in ("截图方式", "全局配置", "账号配置", "通知", "设置", "关于")],
        )
        reference = EndfieldWorkspaceTab("DEVELOPER", "开发者工具", [("工具与调试", None, QWidget())])
        reference_size = reference.tab_bar.buttons[0].size()
        workspace.show()
        for width in (1300, 600, 450, 1300):
            workspace.resize(width, 750)
            for _ in range(10):
                self.app.processEvents()
            for button in workspace.tab_bar.buttons:
                self.assertEqual(button.size(), reference_size)
                origin = button.mapTo(workspace.header_bar, QPoint())
                self.assertTrue(workspace.header_bar.rect().contains(QRect(origin, button.size())))
                self.assertTrue(workspace.tab_bar.rect().contains(button.geometry()))
                self.assertEqual(
                    button.fontMetrics().elidedText(button.text(), Qt.ElideRight, button.width() - 20), button.text()
                )
            if width == 450:
                self.assertGreater(len({button.y() for button in workspace.tab_bar.buttons}), 1)
            elif width == 1300:
                self.assertEqual(len({button.y() for button in workspace.tab_bar.buttons}), 1)
        workspace.close()
        workspace.deleteLater()
        reference.deleteLater()

    def test_tabs_route_by_page_identity_when_titles_are_translated(self):
        from ok.ui.qt.widget.CustomTab import CustomTab

        from src.gui.EndfieldEntryCards import EndfieldSectionBanner
        from src.patches.endfield_theme_patch import route_endfield_tabs

        class GlobalSettingsPage(CustomTab):
            name = "全局配置"

        def page(class_name, group_name=None):
            widget = type(class_name, (QWidget,), {})()
            widget.group_name = group_name
            return widget

        # 日文界面的原生页签文字；只有组名和页面类别不随语言变化
        pending = [
            {"widget": widget, "icon": None, "text": text}
            for text, widget in (
                ("キャプチャ", page("StartTab")),
                ("グローバル設定", GlobalSettingsPage()),
                ("收尾", page("OneTimeTaskTab", "收尾")),
                ("トリガー", page("TriggerTaskTab")),
                ("戦闘", page("OneTimeTaskTab", "战斗")),
                ("帝江号", page("OneTimeTaskTab", "帝江号")),
                ("副本", page("OneTimeTaskTab", "副本")),
                ("ツールとデバッグ", page("OneTimeTaskTab", "工具与调试")),
                ("スケジュール", page("ScheduleTaskTab")),
                ("通知", page("GlobalConfigTab")),
                ("設定", page("SettingTab")),
                ("概要", page("AboutTab")),
            )
        ]
        routed = route_endfield_tabs(pending, lambda text: f"<{text}>")
        self.assertEqual(
            {code: [label for label, _, _ in tabs] for code, tabs in routed.items()},
            {
                "ENVIRONMENT": ["キャプチャ", "グローバル設定", "通知", "設定", "概要"],
                "DAILY": ["帝江号", "戦闘"],
                "COMBAT": ["<大世界>", "副本", "<其他>"],
                "AUTOMATION": ["スケジュール"],
                "DEVELOPER": ["ツールとデバッグ"],
            },
        )

        workspace = EndfieldWorkspaceTab("ENVIRONMENT", "系统与环境", routed["ENVIRONMENT"])
        self.assertEqual(
            [button.code for button in workspace.tab_bar.buttons], ["CAPTURE", "GLOBAL", "NOTICE", "SETTINGS", "ABOUT"]
        )
        eyebrows = sorted(
            label.text()
            for banner in workspace.findChildren(EndfieldSectionBanner)
            for label in banner.findChildren(QLabel)
            if label.text().startswith("// SECTION")
        )
        self.assertEqual(
            eyebrows,
            ["// SECTION 02 // GLOBAL CONFIGURATION", "// SECTION 03 // NOTIFICATIONS", "// SECTION 04 // SETTINGS"],
        )
        combat = EndfieldWorkspaceTab("COMBAT", "业务与战斗", routed["COMBAT"])
        self.assertEqual([button.code for button in combat.tab_bar.buttons], ["WORLD", "DUNGEON", "OTHERS"])
        workspace.deleteLater()
        combat.deleteLater()

    def test_every_endfield_ui_text_has_an_entry_in_each_locale(self):
        import ast
        import gettext

        root = Path(__file__).resolve().parents[1]
        texts = {
            "系统与环境",
            "日常一条龙",
            "业务与战斗",
            "自动化调度",
            "开发者工具",
            "帝江号",
            "战斗",
            "奖励",
            "其他",
            "大世界",
            "副本",
            "自动战斗",
            "开始一条龙",
            "一条龙设置",
            "暂停",
            "停止",
        }
        for path in [*root.glob("src/gui/**/*.py"), root / "src/patches/endfield_theme_patch.py"]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ui_tr":
                    arg = node.args[0]
                    choices = (arg.body, arg.orelse) if isinstance(arg, ast.IfExp) else (arg,)
                    texts.update(c.value for c in choices if isinstance(c, ast.Constant))
        self.assertIn("恢复默认大小", texts)
        for locale in ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"):
            catalog = gettext.translation("ok", str(root / "i18n"), languages=[locale])
            with self.subTest(locale=locale):
                self.assertEqual(sorted(texts - catalog._catalog.keys()), [])

    def test_workspace_and_drawer_text_follow_the_ui_language(self):
        import gettext

        root = Path(__file__).resolve().parents[1]
        catalog = gettext.translation("ok", str(root / "i18n"), languages=["ja_JP"])
        with patch.object(og, "app", SimpleNamespace(tr=catalog.gettext)):
            workspace = EndfieldWorkspaceTab(
                "DAILY",
                "日常一条龙",
                [("帝江号", None, QWidget())],
                primary_action=("开始一条龙", lambda: None),
                secondary_action=("一条龙设置", lambda: None),
            )
            drawer = EndfieldTacticalDrawer()
        self.assertEqual(workspace.title_label.text(), "デイリールーチン")
        self.assertEqual(workspace.eyebrow_label.text(), "// DAILY ROUTINE")
        assert workspace.primary_action_button is not None and workspace.secondary_action_button is not None
        self.assertEqual(workspace.primary_action_button.text(), "ルーチン開始")
        self.assertEqual(workspace.secondary_action_button.text(), "ルーチン設定")
        self.assertEqual((drawer.save_btn.text(), drawer.run_once_btn.text()), ("保存して同期", "今すぐ単体実行"))
        workspace.deleteLater()
        drawer.deleteLater()

    def test_long_translated_tab_names_widen_the_tab_instead_of_eliding(self):
        workspace = EndfieldWorkspaceTab(
            "ENVIRONMENT", "System", [(name, None, QWidget()) for name in ("Account Config", "グローバル設定", "设置")]
        )
        widths = [button.width() for button in workspace.tab_bar.buttons]
        reference = EndfieldWorkspaceTab("DEVELOPER", "开发者工具", [("工具与调试", None, QWidget())])
        self.assertEqual(widths[2], reference.tab_bar.buttons[0].width())
        for button in workspace.tab_bar.buttons:
            self.assertEqual(
                button.fontMetrics().elidedText(button.text(), Qt.ElideRight, button.width() - 20), button.text()
            )
        workspace.deleteLater()
        reference.deleteLater()

    def test_primary_action_is_at_the_right_edge_and_has_its_own_shape(self):
        workspace = EndfieldWorkspaceTab(
            "DAILY",
            "日常一条龙",
            [("帝江号", None, QWidget())],
            primary_action=("开始一条龙", lambda: None),
        )
        workspace.resize(1300, 700)
        workspace.show()
        self.app.processEvents()
        button = workspace.primary_action_button
        assert button is not None
        self.assertTrue(button.chamfered)
        right = button.mapTo(workspace, QPoint(button.width(), 0)).x()
        self.assertEqual(workspace.width() - right, 36)
        self.assertTrue(workspace.hatch_band.isVisible())
        self.assertLess(
            workspace.hatch_band.geometry().bottom(), workspace.tab_bar.mapTo(workspace.header_bar, QPoint()).y()
        )
        self.assertEqual(button.font().pixelSize(), 14)
        self.assertEqual(button.iconSize().width(), 14)
        self.assertEqual(button.size().width(), 132)
        self.assertEqual(button.size().height(), 36)
        self.assertGreater(button.x(), workspace.tab_bar.geometry().right() + 100)
        workspace.close()
        workspace.deleteLater()

    def test_daily_header_actions_follow_the_task_run_state(self):
        from ok.ui.qt.Communicate import communicate
        from PySide6.QtCore import QCoreApplication, QEvent

        calls = []
        task = SimpleNamespace(
            enabled=False,
            paused=False,
            running=False,
            pause=lambda: calls.append("pause"),
            disable=lambda: calls.append("disable"),
            unpause=lambda: calls.append("unpause"),
        )
        workspace = EndfieldWorkspaceTab(
            "DAILY",
            "日常一条龙",
            [("帝江号", None, QWidget())],
            primary_action=("开始一条龙", lambda: calls.append("start")),
            secondary_action=("一条龙设置", lambda: None),
            run_task=lambda: task,
        )
        notices = []
        workspace.show_notification = lambda message, **_kwargs: notices.append(message)
        workspace.resize(1300, 700)
        workspace.show()
        start, pause, stop, status = (
            workspace.primary_action_button,
            workspace.pause_action_button,
            workspace.stop_action_button,
            workspace.run_status,
        )
        assert start is not None and pause is not None and stop is not None and status is not None

        def publish(**state):
            for key, value in state.items():
                setattr(task, key, value)
            communicate.task.emit(task)
            for _ in range(4):
                self.app.processEvents()
            return [widget.isVisible() for widget in (status, pause, stop, start)]

        self.assertEqual(publish(), [False, False, False, True])
        # 执行器已在运行：ok 不会弹“启动成功”，由页眉在任务启用后补上
        with patch.object(og, "executor", SimpleNamespace(paused=False)):
            start.click()
        self.assertEqual(publish(enabled=True), [True, False, True, False])
        self.assertEqual(status.text(), "等待执行")
        self.assertEqual(notices, ["Start Success."])
        self.assertEqual(publish(running=True), [True, True, True, False])
        self.assertEqual(status.text(), "运行中")
        self.assertEqual(workspace.width() - stop.mapTo(workspace, QPoint(stop.width(), 0)).x(), 36)
        pause.click()
        self.assertEqual(publish(paused=True), [True, False, True, True])
        self.assertEqual((start.text(), status.text()), ("继续", "已暂停"))
        stop.click()
        self.assertEqual(publish(enabled=False, paused=False, running=False), [False, False, False, True])
        self.assertEqual(start.text(), "开始一条龙")
        self.assertEqual(calls, ["start", "pause", "disable", "unpause"])
        # 执行器暂停时由 ok 自己提示，页眉不重复
        with patch.object(og, "executor", SimpleNamespace(paused=True)):
            start.click()
        publish(enabled=True)
        self.assertEqual(notices, ["Start Success."])
        workspace.close()
        workspace.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertNotIn(workspace._sync_run_state, communicate.task._subscribers)


class NativeControlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = test_application()
        from src.gui.EndfieldControls import install_control_paint

        install_control_paint()
        patch_widget_paint_bridge()

    def test_square_switch_preserves_mouse_keyboard_disabled_and_animation(self):
        from PySide6.QtTest import QSignalSpy, QTest

        from src.gui.EndfieldTheme import DARK, LIGHT

        saved = qconfig.theme
        try:
            qconfig.set(qconfig.themeMode, Theme.DARK, save=False)
            switch = SwitchButton()
            switch.show()
            self.app.processEvents()
            spy = QSignalSpy(switch.checkedChanged)
            QTest.mouseClick(switch.indicator, Qt.LeftButton)
            self.assertEqual(switch.indicator.slideAni.endValue(), 25)
            switch.indicator.slideAni.setCurrentTime(switch.indicator.slideAni.duration())
            self.assertTrue(switch.isChecked())
            self.assertEqual(spy.count(), 1)
            self.assertEqual(switch.indicator.sliderX, 25)
            image = switch.indicator.grab().toImage()
            self.assertEqual(image.pixelColor(31, 11), QColor(DARK["on_signal"]))
            self.assertEqual(image.pixelColor(1, 1), QColor(DARK["signal"]))
            self.assertEqual(image.pixelColor(1, 1), image.pixelColor(2, 1))
            switch.setDisabled(True)
            QTest.mouseClick(switch.indicator, Qt.LeftButton)
            self.assertEqual(spy.count(), 1)
            switch.setEnabled(True)
            switch.indicator.setFocus(Qt.TabFocusReason)
            QTest.keyClick(switch.indicator, Qt.Key_Space)
            self.assertEqual(switch.indicator.slideAni.endValue(), 5)
            switch.indicator.slideAni.setCurrentTime(switch.indicator.slideAni.duration())
            self.assertFalse(switch.isChecked())
            self.assertEqual(spy.count(), 2)
            self.assertEqual(switch.indicator.sliderX, 5)
            qconfig.set(qconfig.themeMode, Theme.LIGHT, save=False)
            image = switch.indicator.grab().toImage()
            self.assertEqual(image.pixelColor(8, 11), QColor(LIGHT["muted"]))
            switch.close()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_square_checkbox_preserves_partial_state_and_disabled_input(self):
        from PySide6.QtTest import QSignalSpy, QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionButton
        from qfluentwidgets import CheckBox

        box = CheckBox("选择任务")
        box.setTristate(True)
        box.show()
        self.app.processEvents()
        spy = QSignalSpy(box.checkStateChanged)
        QTest.keyClick(box, Qt.Key_Space)
        self.assertEqual(box.checkState(), Qt.PartiallyChecked)
        self.assertEqual(spy.count(), 1)
        box.setDisabled(True)
        QTest.keyClick(box, Qt.Key_Space)
        self.assertEqual(spy.count(), 1)
        box.setEnabled(True)
        box.setCheckState(Qt.Checked)
        option = QStyleOptionButton()
        option.initFrom(box)
        rect = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)
        image = box.grab().toImage()
        self.assertEqual(image.pixelColor(rect.topLeft()).rgba(), box._borderColor().rgba())
        box.close()

    def test_primary_button_has_distinct_pressed_and_disabled_states(self):
        from src.gui.EndfieldPrimitives import EndfieldActionButton
        from src.gui.EndfieldTheme import palette

        saved = qconfig.theme
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                colors = palette(theme == Theme.DARK)
                button = EndfieldActionButton(None, "保存", primary=True)
                button.resize(132, 36)
                button.show()
                self.app.processEvents()
                button.clearFocus()
                button.setAttribute(Qt.WA_UnderMouse, False)
                normal = button.grab().toImage().pixelColor(10, 10)
                self.assertEqual(normal, QColor(colors["go"]))
                button.setDown(True)
                pressed = button.grab().toImage().pixelColor(10, 10)
                self.assertEqual(pressed, QColor(colors["primary_pressed"]))
                self.assertNotEqual(pressed, normal)
                button.setDown(False)
                button.setEnabled(False)
                disabled = button.grab().toImage().pixelColor(10, 10)
                self.assertNotEqual(disabled, normal)
                self.assertNotEqual(disabled, pressed)
                button.close()
                button.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_native_and_custom_buttons_share_normal_and_disabled_colors(self):
        from qfluentwidgets import PrimaryPushButton

        from src.gui.EndfieldPrimitives import EndfieldActionButton
        from src.gui.EndfieldTheme import palette

        saved = qconfig.theme
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                colors = palette(theme == Theme.DARK)
                for button in (PrimaryPushButton("保存"), EndfieldActionButton(None, "保存", primary=True)):
                    button.resize(132, 36)
                    button.show()
                    self.app.processEvents()
                    button.clearFocus()
                    button.setAttribute(Qt.WA_UnderMouse, False)
                    self.assertEqual(button.grab().toImage().pixelColor(8, 8), QColor(colors["go"]))
                    button.setDisabled(True)
                    self.app.processEvents()
                    self.assertEqual(button.grab().toImage().pixelColor(8, 8), QColor(colors["disabled_bg"]))
                    button.close()
                    button.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_checkpoint_disabled_selection_remains_visible_without_accent_fill(self):
        from PySide6.QtWidgets import QCheckBox, QFrame, QStyle, QStyleOptionButton

        from src.gui.EndfieldTheme import palette
        from src.patches.endfield_theme_patch import generate_endfield_qss

        saved = qconfig.theme
        try:
            for dark in (False, True):
                qconfig.set(qconfig.themeMode, Theme.DARK if dark else Theme.LIGHT, save=False)
                colors = palette(dark)
                host = QFrame()
                host.setObjectName("EndfieldCheckpointCard")
                host.setStyleSheet(generate_endfield_qss(dark))
                box = QCheckBox(host)
                QHBoxLayout(host).addWidget(box)
                host.show()
                self.app.processEvents()
                for checked, role in ((False, "disabled_bg"), (True, "disabled_text")):
                    box.setChecked(checked)
                    box.setDisabled(True)
                    option = QStyleOptionButton()
                    option.initFrom(box)
                    indicator = box.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, box)
                    point = indicator.topLeft() + QPoint(3, 3)
                    self.assertEqual(box.grab().toImage().pixelColor(point), QColor(colors[role]))
                    self.assertEqual(box.isChecked(), checked)
                    box.setEnabled(True)
                host.close()
                host.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_tabs_show_keyboard_focus_in_both_themes(self):
        from PySide6.QtTest import QTest

        from src.gui.EndfieldWorkspaceTab import EndfieldTabButton

        saved = qconfig.theme
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                host = QWidget()
                layout = QHBoxLayout(host)
                button = EndfieldTabButton("自动战斗")
                other = QPushButton("另一个控件")
                layout.addWidget(button)
                layout.addWidget(other)
                host.show()
                host.activateWindow()
                other.setFocus(Qt.TabFocusReason)
                QTest.qWait(1)
                self.assertFalse(button.hasFocus())
                unfocused = button.grab().toImage()
                QTest.keyClick(other, Qt.Key_Tab)
                self.assertTrue(button.hasFocus())
                self.assertNotEqual(unfocused, button.grab().toImage())
                host.close()
                host.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_navigation_marks_the_current_item_and_brand_shows_the_window_icon(self):
        from PySide6.QtGui import QIcon, QPixmap
        from PySide6.QtWidgets import QVBoxLayout
        from qfluentwidgets import FluentIcon
        from qfluentwidgets.components.navigation.navigation_widget import NavigationTreeWidget

        from src.gui.EndfieldNavigation import EndfieldNavBrand, install_navigation_paint, style_navigation_item
        from src.gui.EndfieldTheme import palette

        install_navigation_paint()
        saved = qconfig.theme
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                colors = palette(theme == Theme.DARK)
                host = QWidget()
                QVBoxLayout(host)
                item = NavigationTreeWidget(FluentIcon.SETTING, "系统与环境", True, host)
                style_navigation_item(item, "01 · SYSTEM")
                item.setCompacted(False)
                self.assertEqual((item.height(), item.itemWidget.height()), (46, 46))
                self.assertGreater(item.suitableWidth(), 44)
                item.setSelected(True)
                image = item.itemWidget.grab().toImage()
                self.assertEqual(image.pixelColor(2, 2), QColor(colors["signal"]))
                self.assertNotEqual(image.pixelColor(item.width() - 1, item.height() - 1), QColor(colors["signal"]))
                item.setSelected(False)
                self.assertNotEqual(item.itemWidget.grab().toImage().pixelColor(2, 2), QColor(colors["signal"]))
                pixmap = QPixmap(64, 64)
                pixmap.fill(QColor("#FF0000"))
                brand = EndfieldNavBrand(QIcon(pixmap), "v1.1.25", host)
                brand.setCompacted(False)
                image = brand.grab().toImage()
                self.assertEqual(image.pixelColor(6 + 17, 32), QColor("#FF0000"))
                self.assertNotEqual(image.pixelColor(6 + 33, 15 + 33), QColor("#FF0000"))
                brand.setCompacted(True)
                self.assertEqual(brand.width(), 40)
                self.assertEqual(brand.grab().toImage().pixelColor(20, 32), QColor("#FF0000"))
                host.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_tags_split_key_value_and_buttons_wipe_signal_on_hover(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QEnterEvent

        from src.gui.EndfieldPrimitives import WIPE_MS, EndfieldActionButton, EndfieldTag
        from src.gui.EndfieldTheme import palette

        saved = qconfig.theme
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                colors = palette(theme == Theme.DARK)
                self.assertEqual(EndfieldTag("一键存放: ON")._parts, ("一键存放", "ON"))
                plate = EndfieldTag("01", ink=True)
                image = plate.grab().toImage()
                self.assertEqual(image.pixelColor(1, 1), QColor(colors["tag_bg"]))
                self.assertNotEqual(image.pixelColor(plate.width() - 1, plate.height() - 1), QColor(colors["tag_bg"]))
                button = EndfieldActionButton(None, "配置")
                button.resize(80, 28)
                button.show()
                self.assertEqual(button.focusPolicy(), Qt.TabFocus)
                self.assertEqual(button.grab().toImage().pixelColor(10, 10), QColor(colors["chip"]))
                button.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
                button._wipe_ani.setCurrentTime(WIPE_MS)
                self.assertEqual(button.grab().toImage().pixelColor(10, 10), QColor(colors["signal"]))
                button.leaveEvent(QEvent(QEvent.Leave))
                button._wipe_ani.setCurrentTime(WIPE_MS)
                self.assertEqual(button.grab().toImage().pixelColor(10, 10), QColor(colors["chip"]))
                button.close()
                button.deleteLater()
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)

    def test_selector_lists_fit_their_rows_up_to_six(self):
        from qfluentwidgets import ListWidget

        from src.gui.EndfieldWindowLayout import fit_list_rows

        view = ListWidget()
        view.addItem("PC 版 · Endfield.exe")
        fit_list_rows(view)
        row = view.sizeHintForRow(0)
        self.assertGreaterEqual(row, 40)
        two_rows = view.height()
        self.assertGreaterEqual(two_rows, 2 * row)
        view.addItems(["WGC", "BitBlt_RenderFull"])
        self.assertEqual(view.height(), two_rows + row)
        view.addItems([str(index) for index in range(10)])
        self.assertEqual(view.height(), two_rows + 4 * row)
        view.deleteLater()

    def test_line_edit_retains_error_color_over_normal_focus_color(self):
        from qfluentwidgets import LineEdit
        from qfluentwidgets.common.color import FluentSystemColor

        from src.gui.EndfieldTheme import LIGHT

        saved = qconfig.theme
        try:
            qconfig.set(qconfig.themeMode, Theme.LIGHT, save=False)
            edit = LineEdit()
            self.assertEqual(edit.focusedBorderColor(), QColor(LIGHT["accent"]))
            edit.setError(True)
            self.assertEqual(edit.focusedBorderColor(), FluentSystemColor.CRITICAL_FOREGROUND.color())
        finally:
            qconfig.set(qconfig.themeMode, saved, save=False)


class PaletteContrastTest(unittest.TestCase):
    def test_text_action_edges_and_statuses_keep_readable_contrast(self):
        from src.gui.EndfieldTheme import palette

        def contrast(first, second):
            def luminance(value):
                rgb = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
                channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
                return sum(v * w for v, w in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))

            values = sorted((luminance(first), luminance(second)))
            return (values[1] + 0.05) / (values[0] + 0.05)

        for dark in (False, True):
            c = palette(dark)
            for foreground in ("text", "muted"):
                for background in ("canvas", "panel", "input", "hover", "chip", "row_hover", "navigation"):
                    with self.subTest(dark=dark, fg=foreground, bg=background):
                        self.assertGreaterEqual(contrast(c[foreground], c[background]), 4.5)
            for foreground, background in (
                ("on_accent", "accent"),
                ("on_go", "go"),
                ("tag_fg", "tag_bg"),
                ("on_signal", "signal"),
                ("on_signal", "primary_hover"),
                ("on_signal", "primary_pressed"),
            ):
                with self.subTest(dark=dark, fg=foreground, bg=background):
                    self.assertGreaterEqual(contrast(c[foreground], c[background]), 4.5)
            for background in ("panel", "input"):
                self.assertGreaterEqual(contrast(c["border_hover"], c[background]), 3)

    def test_dark_surfaces_get_lighter_toward_the_viewer(self):
        from src.gui.EndfieldTheme import DARK

        def lightness(value):
            rgb = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
            channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            y = sum(v * w for v, w in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))
            return 116 * y ** (1 / 3) - 16

        # 导航与画布同色，标题栏左段才不会出现色差缺口
        self.assertEqual(DARK["navigation"], DARK["canvas"])
        # 深色是调暗的灰而不是近黑；层级靠明度区分，越靠前越亮
        self.assertGreaterEqual(lightness(DARK["canvas"]), 15)
        levels = [lightness(DARK[key]) for key in ("canvas", "panel", "chip")]
        for lower, upper in pairwise(levels):
            self.assertGreaterEqual(upper - lower, 4)

    def test_dark_mirrors_the_light_ink_and_rule_roles(self):
        from src.gui.EndfieldTheme import DARK, LIGHT

        def lightness(value):
            rgb = (int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))
            channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            y = sum(v * w for v, w in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))
            return 116 * y ** (1 / 3) - 16

        for name, c in (("light", LIGHT), ("dark", DARK)):
            with self.subTest(theme=name):
                # 勾选框、编号牌、主按钮都用墨色；信号黄只留给当前项
                self.assertEqual({c["accent"], c["go"], c["tag_bg"]}, {c["text"]})
                self.assertNotEqual(c["accent"], c["signal"])
                # 边线与面板拉开的明度差两套主题一致，深色的面板轮廓不比浅色弱
                self.assertGreaterEqual(abs(lightness(c["border"]) - lightness(c["panel"])), 12)


class WindowRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = test_application()

    def test_reset_fits_and_centers_on_the_current_screen(self):
        from src.gui.EndfieldWindowLayout import default_window_rect, reset_window_size

        for screen in (QRect(1920, 40, 2560, 1400), QRect(-1280, 0, 1280, 720)):
            target = default_window_rect({}, screen)
            self.assertTrue(screen.contains(target))
            self.assertLessEqual((target.center() - screen.center()).manhattanLength(), 1)
        window = QWidget()
        window.resize(1700, 950)
        window.showMaximized()
        self.app.processEvents()
        reset_window_size(window, {})
        self.app.processEvents()
        self.assertFalse(window.isMaximized())
        expected = default_window_rect({}, window.screen().availableGeometry())
        self.assertEqual(window.geometry(), expected)
        window.close()
        window.deleteLater()

    def test_title_bar_button_restores_the_default_size(self):
        from qfluentwidgets import FluentWindow

        from src.gui.EndfieldWindowLayout import default_window_rect, install_window_size_button

        window = FluentWindow()
        install_window_size_button(window)
        bar = window.titleBar
        button = window._endfield_size_button
        self.assertEqual(bar.buttonLayout.indexOf(button) + 1, bar.buttonLayout.indexOf(bar.minBtn))
        self.assertEqual(button.toolTip(), "恢复默认大小")
        self.assertEqual(button.size(), bar.minBtn.size())
        window.resize(700, 500)
        window.show()
        self.app.processEvents()
        # 按钮在标题栏右侧，左侧仍可拖动窗口
        self.assertGreater(button.mapTo(bar, QPoint()).x(), bar.width() // 2)
        self.assertFalse(bar._isDragRegion(button.mapTo(bar, QPoint(4, 4))))
        with patch.object(og, "config", {}):
            button.click()
        self.app.processEvents()
        self.assertEqual(window.geometry(), default_window_rect({}, window.screen().availableGeometry()))
        window.close()
        window.deleteLater()

    def test_capture_page_reflows_without_hiding_the_right_edge(self):
        from ok.ui.qt.widget.Card import Card
        from ok.ui.qt.widget.Tab import Tab
        from qfluentwidgets import PushButton

        from src.gui.EndfieldWindowLayout import wrap_capture_controls
        from src.patches.endfield_theme_patch import generate_endfield_qss

        page = Tab()
        page.selector_widget = QWidget()
        selectors = QHBoxLayout(page.selector_widget)
        for text in ("选择窗口", "截图方式", "选择交互方式"):
            selectors.addWidget(Card(text, QWidget(), stretch=1))
        page.add_widget(page.selector_widget, 1)
        page.debug_widget = QWidget()
        page.debug_layout = QHBoxLayout(page.debug_widget)
        buttons = [
            PushButton(text)
            for text in (
                "导出日志",
                "上传日志",
                "安装目录",
                "截图目录",
                "日志目录",
                "查看日志",
                "OCR",
                "导出配置",
                "导入配置",
            )
        ]
        page.overlay_switch = SwitchButton()
        page.overlay_switch.setOffText("禁用标记框")
        page.overlay_switch.setOnText("启用标记框")
        for button in buttons[:7]:
            button.setMinimumWidth(110)
            page.debug_layout.addWidget(button)
        page.debug_layout.addWidget(page.overlay_switch)
        for button in buttons[7:]:
            button.setMinimumWidth(110)
            page.debug_layout.addWidget(button)
        panel = page.add_card("开发工具", page.debug_widget)
        wrap_capture_controls(page)
        controls = [page.debug_layout.itemAt(index).widget() for index in range(page.debug_layout.count())]
        self.assertEqual(controls, buttons)
        # 开关离开按钮流，排在标题之后、靠右
        self.assertGreater(
            panel.title_layout.indexOf(page.overlay_switch), panel.title_layout.indexOf(panel.titleLabel)
        )
        # 首次显示前先定宽，否则 offscreen 平台会把第一次 resize 退回到 sizeHint
        page.resize(1000, 850)
        page.show()
        for dark in (False, True):
            page.setStyleSheet(generate_endfield_qss(dark))
            for width in (1000, 1500, 1000, 950):
                page.hide()
                page.resize(width, 850)
                self.app.processEvents()
                page.show()
                for _ in range(12):
                    self.app.processEvents()
                self.assertEqual(page.width(), width)
                self.assertLessEqual(page.view.width(), page.viewport().width())
                row_count = len({button.y() for button in buttons})
                self.assertGreater(row_count, 1 if width == 1000 else 0)
                for button in buttons:
                    self.assertEqual(button.height(), 28)
                    self.assertLessEqual(button.geometry().right(), button.parentWidget().width())
                    self.assertLessEqual(button.geometry().bottom(), button.parentWidget().height())
                switch = page.overlay_switch
                switch_box = QRect(switch.mapTo(panel, QPoint()), switch.size())
                self.assertTrue(panel.rect().contains(switch_box))
                self.assertLess(switch_box.bottom(), panel.card.y())
                self.assertGreater(switch_box.left(), panel.width() // 2)
        page.close()
        page.deleteLater()

    def test_dependency_rows_do_not_get_group_backgrounds(self):
        from src.patches.endfield_theme_patch import _widget_scope_override_qss

        for dark in (False, True):
            qss = _widget_scope_override_qss(dark)
            block = qss.split("#configRow[configGroup=true] {", 1)[1].split("}", 1)[0]
            self.assertIn("background-color: transparent", block)
            self.assertNotIn("2px", block)


if __name__ == "__main__":
    unittest.main()
