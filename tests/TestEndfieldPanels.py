"""Side-panel lifecycle and entry visibility regressions."""

import unittest
from itertools import pairwise
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from ok import og
from ok.ui.qt.tasks.ConfigCard import ConfigCard
from ok.ui.qt.widget.Card import Card
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, SettingCard, qconfig

from src.gui.components.EndfieldCheckpointCard import EndfieldCheckpointCard
from src.gui.components.EndfieldSectionMatrix import open_task_drawer
from src.gui.components.EndfieldTacticalDrawer import _DRAWERS, drawer_for, open_card_drawer
from src.gui.EndfieldConfigContent import has_task_configuration
from src.gui.EndfieldEntryCards import EndfieldSectionBanner
from src.gui.EndfieldWorkspaceTab import EndfieldWorkspaceTab
from src.patches.endfield_theme_patch import (
    patch_combat_config_forms,
    patch_config_row_background,
    patch_widget_paint_bridge,
)
from tests import TestEndfieldUi as fixtures


class PanelConfig(fixtures.CombatEditBuffer):
    def __init__(self, values):
        super().__init__(values)
        self.defaults = dict(values)
        self.reset_to_default = Mock(side_effect=lambda: self.update(self.defaults))

    def get_default(self, key: str):
        return self.defaults.get(key)


class PanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = fixtures.test_application()
        patch_combat_config_forms()
        patch_widget_paint_bridge()
        patch_config_row_background()

    def setUp(self):
        self.app_patch = patch.object(og, "app", SimpleNamespace(tr=lambda value: value))
        self.app_patch.start()
        self.host = QWidget()
        self.host.resize(1000, 700)
        self.layout = QVBoxLayout(self.host)
        self.host.show()

    def tearDown(self):
        entry = _DRAWERS.get(id(self.host))
        if entry:
            entry[0].close_drawer()
        self.host.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.app_patch.stop()

    def card(self, values=None, types=None, defaults=None, descriptions=None):
        config = PanelConfig(values or {})
        card = ConfigCard(
            None, "配置项", config, "说明", defaults or {}, descriptions or {}, types or {}, FluentIcon.SETTING
        )
        self.layout.addWidget(card)
        card.show()
        self.app.processEvents()
        return card

    def test_option_columns_align_and_grow_for_all_choices_in_shared_panel(self):
        options = [f"任务选项{index}" for index in range(8)]
        options[-1] += "包含较长说明，换行后仍完整显示"
        self.host.hide()
        values = {"短列表": ["一"], "长列表": options[:1], "后续列表": ["一"], "后续开关": False, "后续文本": ""}
        card = self.card(
            values,
            {
                "短列表": {"type": "multi_selection", "options": ["一", "二"]},
                "长列表": {"type": "multi_selection", "options": options},
                "后续列表": {"type": "multi_selection", "options": ["一", "二"]},
            },
            descriptions=dict.fromkeys(values, "按固定顺序执行选中的任务，较长说明可以换行且不挤压右侧选项。"),
        )
        card.update_config()
        self.host.show()
        self.app.processEvents()
        card.card.expandButton.click()
        drawer, _ = drawer_for(card)
        for _ in range(12):
            self.app.processEvents()
        short = card.config_widget_by_key["短列表"]
        long = card.config_widget_by_key["长列表"]
        required = long.content_layout.minimumHeight() + 20
        self.assertGreaterEqual(long.minimumHeight(), required)
        self.assertEqual(short.content_layout.width(), 180)
        self.assertEqual(long.content_layout.width(), 180)
        first_x = short.check_boxes[0].checkbox.mapTo(drawer, QPoint()).x()
        self.assertEqual(first_x, long.check_boxes[0].checkbox.mapTo(drawer, QPoint()).x())
        for row in (short, long):
            for box in row.check_boxes:
                rect = box.geometry().translated(row.content_layout.pos())
                self.assertTrue(row.rect().contains(rect), (row.size(), rect))
        self.assertGreater(long.height(), short.height() + 150)
        self.assertGreaterEqual(long.y(), short.y() + short.height())
        self.assertTrue(card.view.rect().contains(long.geometry()))
        self.assertGreaterEqual(card.view.minimumHeight(), card.viewLayout.totalHeightForWidth(card.view.width()))
        for idx in range(1, card.viewLayout.count()):
            previous = card.viewLayout.itemAt(idx - 1).widget()
            current = card.viewLayout.itemAt(idx).widget()
            self.assertGreaterEqual(current.y(), previous.y() + previous.height())
        changing = card.config_widget_by_key["后续开关"]
        old_height = changing.height()
        changing.contentLabel.setText("配置说明增长后需要继续换行，后面的输入行必须随之下移。" * 12)
        for _ in range(12):
            self.app.processEvents()
        self.assertGreater(changing.height(), old_height)
        self.assertGreaterEqual(card.config_widget_by_key["后续文本"].y(), changing.y() + changing.height())
        long.check_boxes[-1].checkbox.click()
        self.assertIn(options[-1], card.config["长列表"])
        drawer.close_drawer()

    def test_settings_entries_match_daily_height_gap_and_right_edge(self):
        from ok.ui.qt.common.config import cfg
        from ok.ui.qt.settings.SettingTab import SettingTab

        from src.gui.EndfieldWindowLayout import patch_window_layout
        from src.patches.endfield_theme_patch import generate_endfield_qss

        patch_window_layout()
        configs = []
        for name in ("Basic Options", "邮件配置"):
            option = SimpleNamespace(
                name=name,
                description="设置说明",
                default_config={"开关": False},
                config_description={},
                config_type={},
                icon=FluentIcon.SETTING,
                show_at_tab=False,
            )
            configs.append((name, PanelConfig({"开关": False}), option))
        executor = SimpleNamespace(global_config=SimpleNamespace(get_all_visible_configs=lambda: configs))
        with patch.object(og, "executor", executor):
            page = SettingTab()
        workspace = EndfieldWorkspaceTab("ENVIRONMENT", "系统与环境", [("设置", None, page)])
        self.layout.addWidget(workspace)
        entries = [page.themeCard, page.languageCard, *page.config_groups]
        controls = [
            page.themeCard.card.expandButton,
            page.languageCard.comboBox,
            *(entry.card.expandButton for entry in page.config_groups),
        ]
        # 「恢复默认大小」在标题栏，设置页不再有窗口大小行
        self.assertFalse(hasattr(page, "window_size_card"))
        previous_theme = qconfig.theme
        try:
            for theme in (fixtures.Theme.LIGHT, fixtures.Theme.DARK):
                qconfig.set(qconfig.themeMode, theme, save=False)
                self.host.setStyleSheet(generate_endfield_qss(theme == fixtures.Theme.DARK))
                for width in (1440, 600, 1000):
                    self.host.resize(width, 800)
                    workspace.show()
                    for _ in range(12):
                        self.app.processEvents()
                    self.assertEqual([entry.height() for entry in entries], [56] * 4)
                    # 同组条目贴在一起，由细线分隔
                    for previous, current in pairwise(entries):
                        self.assertEqual(current.y() - previous.y() - previous.height(), 0)
                    for entry, control in zip(entries, controls, strict=True):
                        right = control.mapTo(entry, QPoint(control.width(), 0)).x()
                        self.assertEqual(entry.width() - right, 12, entry.__class__.__name__)
                        origin = control.mapTo(entry, QPoint())
                        self.assertTrue(entry.rect().contains(origin))
                        self.assertLessEqual(origin.y() + control.height(), entry.height())
            self.assertIs(page.languageCard.configItem, cfg.language)
            page.config_groups[0].card.expandButton.click()
            drawer, _ = drawer_for(page)
            self.assertIs(drawer._source_card, page.config_groups[0])
            drawer.close_drawer()
        finally:
            qconfig.set(qconfig.themeMode, previous_theme, save=False)

    def test_entry_descriptions_are_beside_titles(self):
        first = self.card({"开关": False})
        second = SettingCard(FluentIcon.SETTING, "界面设置", "这是设置说明", self.host)
        self.layout.addWidget(second)
        self.app.processEvents()
        for header in (first.card, second):
            title, description = header.titleLabel, header.contentLabel
            self.assertGreater(description.mapTo(header, QPoint()).x(), title.mapTo(header, QPoint()).x())
            self.assertEqual(title.mapTo(header, QPoint()).y(), description.mapTo(header, QPoint()).y())
            self.assertEqual(title.height(), description.height())
        first.card.setTitle("更新后的标题")
        first.card.setContent("更新后需要省略的完整说明")
        self.assertEqual(first.card.titleLabel.toolTip(), first.card.titleLabel.text())
        self.assertEqual(first.card.contentLabel.toolTip(), first.card.contentLabel.text())

    def test_card_specific_save_callback_takes_precedence_over_page_save(self):
        self.host._save_pending_changes = Mock()
        card = self.card({"数量": 2})
        card._endfield_commit = Mock()
        card.card.expandButton.click()
        drawer, _ = drawer_for(card)
        drawer.save_btn.click()
        card._endfield_commit.assert_called_once()
        self.host._save_pending_changes.assert_not_called()
        self.assertTrue(drawer.isHidden())

    def test_native_fields_return_after_escape_replace_hide_and_destroy(self):
        first = self.card({"开关": False})
        second = self.card({"数量": 2})
        first.card.expandButton.click()
        drawer, mask = drawer_for(first)
        self.assertIs(drawer._source_card, first)
        self.assertIs(first.view.parentWidget(), drawer.form_container)
        self.assertEqual(first.height(), 56)
        first.config_widget_by_key["开关"].switch_button.setChecked(True)
        self.assertTrue(first.config["开关"])
        QTest.keyClick(drawer.close_btn, Qt.Key_Escape)
        self.assertTrue(drawer.isHidden())
        self.assertTrue(mask.isHidden())
        self.assertIs(first.view.parentWidget(), first.scrollWidget)
        self.assertFalse(first.isExpand)
        first.card.expandButton.click()
        second.card.expandButton.click()
        self.assertIs(first.view.parentWidget(), first.scrollWidget)
        self.assertIs(drawer._source_card, second)
        self.assertEqual(drawer.form_layout.count(), 1)
        second.hide()
        self.assertTrue(drawer.isHidden())
        first.card.expandButton.click()
        first.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(drawer.isHidden())
        self.assertIsNone(drawer._source_card)
        self.assertTrue(mask.isHidden())

    def test_borrowed_controls_keep_reset_and_disabled_state(self):
        card = self.card({"开关": False}, defaults={"开关": False})
        card.card.expandButton.click()
        drawer, mask = drawer_for(card)
        card.config_widget_by_key["开关"].switch_button.setChecked(True)
        assert card.reset_config is not None
        card.reset_config.click()
        self.assertFalse(card.config["开关"])
        self.assertEqual(card.config.reset_to_default.call_count, 1)
        card.setEnabled(False)
        self.assertFalse(card.view.isEnabled())
        self.assertFalse(drawer.save_btn.isEnabled())
        card.setEnabled(True)
        self.assertTrue(card.view.isEnabled())
        mask.clicked.emit()
        self.assertTrue(drawer.isHidden())
        self.assertIs(card.view.parentWidget(), card.scrollWidget)

    def test_entries_without_fields_or_operations_cannot_open_panels(self):
        for values, types in (({}, {}), ({"_state": True}, {}), ({"秘密": True}, {"秘密": {"hidden": True}})):
            card = self.card(values, types)
            self.assertTrue(card.card.expandButton.isHidden())
            card.setExpand(True)
            self.assertFalse(card.isExpand)
            task = SimpleNamespace(config=card.config, config_type=types)
            entry = EndfieldCheckpointCard("01", "任务", task)
            self.assertTrue(entry.config_btn.isHidden())
            self.assertIsNone(open_task_drawer(self.host, "任务", task))
            entry.deleteLater()
        self.assertNotIn(id(self.host), _DRAWERS)
        task = SimpleNamespace(
            config={},
            config_type={"操作": {"type": "button", "callback": lambda: None}},
            config_description={},
            show_create_shortcut=False,
        )
        self.assertFalse(has_task_configuration(task))
        self.assertIsNone(open_task_drawer(self.host, "任务", task))
        task.config = PanelConfig({"_state": True})
        self.assertTrue(has_task_configuration(task))
        from src.gui.components.EndfieldTacticalDrawer import show_drawer

        drawer = show_drawer(self.host, lambda target: target.open_subtask("操作", task))
        self.assertIsNotNone(drawer)
        self.assertEqual(drawer.form_layout.count(), 1)
        drawer.close_drawer()

    def test_hidden_only_entries_keep_native_reset_operation(self):
        card = self.card({"秘密": True}, {"秘密": {"hidden": True}}, defaults={"秘密": False})
        self.assertFalse(card.config_widgets)
        self.assertFalse(card.card.expandButton.isHidden())
        drawer = open_card_drawer(card)
        self.assertIsNotNone(drawer)
        self.assertTrue(card.reset_config.isVisible())
        card.reset_config.click()
        card.config.reset_to_default.assert_called_once()
        drawer.close_drawer()
        task = SimpleNamespace(
            config=PanelConfig({"秘密": True}),
            config_type={"秘密": {"hidden": True}},
            default_config={"秘密": False},
            config_description={},
            show_create_shortcut=False,
        )
        entry = EndfieldCheckpointCard("01", "任务", task)
        self.assertFalse(entry.config_btn.isHidden())
        drawer = open_task_drawer(self.host, "任务", task)
        self.assertIsNotNone(drawer)
        form = drawer.form_layout.itemAt(0).widget()
        self.assertFalse(form.config_widgets)
        self.assertTrue(form.reset_config.isVisible())
        drawer.close_drawer()
        entry.deleteLater()

    def test_clearing_account_override_does_not_recreate_it_from_borrowed_form(self):
        from src.gui.AccountConfigTab import AccountConfigTab

        page: Any = QWidget(self.host)
        self.layout.addWidget(page)
        layout = QVBoxLayout(page)
        card = self.card({"数量": 2})
        layout.addWidget(card)
        page.current_map_account_key = ""
        page.overrides_data = {"accounts": {"test-account": {"task": {"数量": 1}}}}
        page.rebuild_account_selector = Mock()
        page.load_current_map_content = Mock()
        page.render_task_editor = Mock(side_effect=card.hide)
        page._save_pending_changes = Mock(
            side_effect=lambda: page.overrides_data["accounts"].update({"test-account": {"task": dict(card.config)}})
        )
        page.show()
        card.show()
        self.app.processEvents()
        card.card.expandButton.click()
        drawer, _ = drawer_for(card)
        self.assertIs(drawer._source_card, card)
        with patch("src.gui.AccountConfigTab.update_overrides", side_effect=lambda merge: merge(page.overrides_data)):
            AccountConfigTab._clear_overrides_and_refresh(page, lambda accounts: accounts.clear())
        self.assertEqual(page.overrides_data["accounts"], {})
        page._save_pending_changes.assert_not_called()
        self.assertTrue(drawer.isHidden())
        self.assertIs(card.view.parentWidget(), card.scrollWidget)

    def test_capture_and_about_keep_native_headings_without_added_sections(self):
        for name in ("截图方式", "关于"):
            page: Any = QWidget()
            page.vBoxLayout = QVBoxLayout(page)
            native = Card("原版标题", QWidget())
            page.vBoxLayout.addWidget(native)
            setting = SettingCard(FluentIcon.INFO, "原版信息")
            page.vBoxLayout.addWidget(setting)
            workspace = EndfieldWorkspaceTab("ENVIRONMENT", "系统与环境", [(name, None, page)])
            self.layout.addWidget(workspace)
            workspace.show()
            self.app.processEvents()
            self.assertIsNone(page.findChild(EndfieldSectionBanner))
            self.assertEqual(native.titleLabel.text(), "原版标题")
            self.assertTrue(all(label.isHidden() for label in page.findChildren(QWidget, "checkpointIdx")))
            self.assertFalse(hasattr(page, "_endfield_numbering"))
            workspace.hide()

    def test_banner_stays_compact_and_numbered_settings_have_no_decorative_icons(self):
        banner = EndfieldSectionBanner("01", "全局配置", "GLOBAL CONFIGURATION")
        self.layout.addWidget(banner)
        plain = SettingCard(FluentIcon.SETTING, "设置")
        self.layout.addWidget(plain)
        expandable = self.card({"数量": 2})
        for label in (plain._endfield_entry_index, expandable._endfield_entry_index):
            label.set_text("01")
            label.show()
        self.app.processEvents()
        self.assertLess(banner.height(), 100)
        for header in (plain, expandable.card):
            self.assertTrue(header.iconLabel.isHidden())
            self.assertEqual(
                header.titleLabel.mapTo(header, QPoint()).x() - header._endfield_entry_index.geometry().right() - 1, 10
            )
            self.assertEqual(header.titleLabel.mapTo(header, QPoint()).x(), plain.titleLabel.mapTo(plain, QPoint()).x())
            # 普通设置卡（如“语言”）的标题与可展开卡同一字重，不被卡片自带的 QLabel 字体规则压回常规
            self.assertGreaterEqual(header.titleLabel.font().weight(), QFont.DemiBold)

    def test_plain_and_expandable_setting_rows_share_one_content_edge(self):
        from qfluentwidgets import (
            ComboBoxSettingCard,
            OptionsConfigItem,
            OptionsSettingCard,
            OptionsValidator,
            SettingCardGroup,
        )

        from src.patches.endfield_theme_patch import generate_endfield_qss

        self.host.setStyleSheet(generate_endfield_qss(False))
        group = SettingCardGroup("软件设置", self.host)
        self.layout.addWidget(group)
        item = OptionsConfigItem("panel-test", "row-edge", "a", OptionsValidator(["a", "b"]))
        theme = OptionsSettingCard(item, FluentIcon.BRUSH, "应用主题", texts=["a", "b"], parent=group)
        language = ComboBoxSettingCard(item, FluentIcon.LANGUAGE, "语言", texts=["a", "b"], parent=group)
        group.addSettingCard(theme)
        group.addSettingCard(language)
        self.app.processEvents()
        # 窗口级的卡片边框不能把普通设置卡的内容挤进 1px，编号牌与标题要和可展开卡对齐
        self.assertEqual(language.contentsRect(), theme.card.contentsRect())

    def test_theme_options_keep_native_selection_and_source_connection(self):
        from qfluentwidgets import (
            OptionsConfigItem,
            OptionsSettingCard,
            OptionsValidator,
        )

        item = OptionsConfigItem("panel-test", "theme", "light", OptionsValidator(["light", "dark"]))
        card = OptionsSettingCard(item, FluentIcon.BRUSH, "主题", texts=["浅色", "深色"])
        self.layout.addWidget(card)
        card.show()
        card.card.expandButton.click()
        drawer, _ = drawer_for(card)
        buttons = card.buttonGroup.buttons()
        with patch.object(qconfig, "save"):
            buttons[1].click()
        self.assertEqual(qconfig.get(item), "dark")
        self.assertTrue(buttons[1].isChecked())
        drawer.close_drawer()
        card.card.expandButton.click()
        self.assertTrue(buttons[1].isChecked())
        self.assertEqual(drawer.form_layout.count(), 1)
