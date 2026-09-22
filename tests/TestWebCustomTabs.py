import os
import typing
import unittest
from unittest import mock

from src.config import config


class TestWebTabConfigRegistration(unittest.TestCase):
    """校验 web 自定义 tab 的注册声明与资产完整性。"""

    def test_web_tabs_registered_and_valid(self):
        from ok.task.web import configured_web_custom_tabs

        entries = config.get("web_tabs")
        self.assertEqual(
            entries,
            [
                ["src.gui.web_tabs", "GlobalConfigWebTab"],
                ["src.gui.web_tabs", "AccountWebTab"],
            ],
        )
        resolved = configured_web_custom_tabs(entries)
        self.assertEqual(len(resolved), 2)

    def test_web_tab_specs_and_assets_exist(self):
        from ok.task.web import WebTabConfig

        import src.gui.web_tabs as web_tabs
        from src.core.BattleConfig import BATTLE_CONFIG_NAME
        from src.core.global_config_store import GLOBAL_CONFIG_GROUPS, ZIP_LINE_CONFIG_NAME

        self.assertIsInstance(web_tabs.GlobalConfigWebTab.web_tab, WebTabConfig)
        self.assertEqual(web_tabs.GlobalConfigWebTab.web_tab.id, "global-config")
        self.assertEqual(web_tabs.AccountWebTab.web_tab.id, "account-config")
        self.assertTrue(web_tabs.GlobalConfigWebTab.web_tab.resolved_asset_dir.is_dir())
        self.assertTrue(web_tabs.GlobalConfigWebTab.web_tab.resolved_entrypoint.is_file())
        self.assertTrue(web_tabs.AccountWebTab.web_tab.resolved_asset_dir.is_dir())
        self.assertTrue(web_tabs.AccountWebTab.web_tab.resolved_entrypoint.is_file())
        # 分组来源已迁移至 global_config_store，桌面端与 web 端共用
        self.assertIn(BATTLE_CONFIG_NAME, GLOBAL_CONFIG_GROUPS["战斗配置"])
        self.assertIn(ZIP_LINE_CONFIG_NAME, GLOBAL_CONFIG_GROUPS["滑索配置"])

    def test_operations_are_allowlisted_without_duplicates(self):
        import inspect

        import src.gui.web_tabs as web_tabs

        for cls in (web_tabs.GlobalConfigWebTab, web_tabs.AccountWebTab):
            seen = set()
            for _name, function in inspect.getmembers(cls, predicate=inspect.isfunction):
                operation = getattr(function, "__task_tab_operation__", None)
                if operation is None:
                    continue
                self.assertNotIn(operation, seen, f"duplicate operation {operation} on {cls.__name__}")
                seen.add(operation)

    def test_sub_configs_normalized(self):
        from src.gui.web_tabs import _normalize_sub_configs

        self.assertEqual(
            _normalize_sub_configs({True: ["a", "b"], False: "c"}),
            {"true": ["a", "b"], "false": ["c"]},
        )
        self.assertEqual(_normalize_sub_configs(None), {})

    def test_monkey_patched_widget_types_have_web_controls(self):
        """cascade_drop_down / cond_sequence_editor 桌面端走 monkey-patch，web 端须有对应控件。"""
        from src.gui.web_tabs import _build_items, _control_for

        cascade_meta = {
            "type": "cascade_drop_down",
            "options": {"基础": ["副本A", "副本B"], "进阶": ["副本C"]},
            "labels": {"基础": "基础副本"},
        }
        self.assertEqual(_control_for("副本A", cascade_meta)[0], "cascade")
        items = {
            item["key"]: item
            for item in _build_items(
                {"体力本": "副本A"}, {"体力本": "副本A"}, {}, {}, {"体力本": cascade_meta}
            )
        }
        self.assertEqual(items["体力本"]["control"], "cascade")
        self.assertEqual(items["体力本"]["options"], cascade_meta["options"])
        self.assertEqual(items["体力本"]["labels"], cascade_meta["labels"])

        self.assertEqual(_control_for([], {"type": "cond_sequence_editor"})[0], "cond_sequence")
        ast_value = [{"if": {"all": ["link", "ult1"]}, "then": ["1", "e"]}]
        items = {
            item["key"]: item
            for item in _build_items(
                {"动作列表": []}, {"动作列表": ast_value}, {}, {}, {"动作列表": {"type": "cond_sequence_editor"}}
            )
        }
        self.assertEqual(items["动作列表"]["control"], "cond_sequence")
        self.assertEqual(items["动作列表"]["value"], ast_value)

    def test_set_value_cond_sequence_normalizes_and_saves(self):
        """set_value 对 cond_sequence_editor 走 normalize_ast 后落盘。"""
        from unittest import mock

        import src.gui.web_tabs as web_tabs
        from src.core.BattleConfig import KEY_COND_SEQUENCE

        ast_value = [
            {"if": {"all": ["link", "ult1", "非法原子"]}, "then": ["1", "e", "bad_token"]},
        ]

        class _FakeConfig(dict):
            default: typing.ClassVar = {KEY_COND_SEQUENCE: []}

            def get_default(self, key):
                return dict.get(self, key)

            def save_file(self):
                pass

        fake_config = _FakeConfig({KEY_COND_SEQUENCE: []})
        fake_option = mock.Mock()
        fake_option.config_type = {KEY_COND_SEQUENCE: {"type": "cond_sequence_editor"}}
        tab = web_tabs.GlobalConfigWebTab.__new__(web_tabs.GlobalConfigWebTab)

        with (
            mock.patch.object(web_tabs, "get_global_config", return_value=fake_config),
            mock.patch.object(
                web_tabs, "get_all_visible_configs", return_value=[("Battle Config", fake_config, fake_option)]
            ),
        ):
            result = tab.set_value({"config": "Battle Config", "key": KEY_COND_SEQUENCE, "value": ast_value})

        self.assertTrue(result["ok"])
        saved = fake_config[KEY_COND_SEQUENCE]
        self.assertIsInstance(saved, list)
        # 非法原子/动作被 normalize_ast 清洗掉
        for node in saved:
            self.assertIn(node.get("if"), ("link", "ult1", {"all": ["link", "ult1"]}))
            for token in node.get("then", []):
                self.assertNotEqual(token, "bad_token")

    def test_set_value_rejects_invalid_cascade_option(self):
        from unittest import mock

        import src.gui.web_tabs as web_tabs

        cascade_meta = {"type": "cascade_drop_down", "options": {"基础": ["副本A", "副本B"]}}

        class _FakeConfig(dict):
            default: typing.ClassVar = {"体力本": "副本A"}

            def get_default(self, key):
                return dict.get(self, key)

            def save_file(self):
                pass

        fake_config = _FakeConfig({"体力本": "副本A"})
        fake_option = mock.Mock()
        fake_option.config_type = {"体力本": cascade_meta}
        tab = web_tabs.GlobalConfigWebTab.__new__(web_tabs.GlobalConfigWebTab)

        with (
            mock.patch.object(web_tabs, "get_global_config", return_value=fake_config),
            mock.patch.object(
                web_tabs, "get_all_visible_configs", return_value=[("Battle Config", fake_config, fake_option)]
            ),
            self.assertRaises(ValueError),
        ):
            tab.set_value({"config": "Battle Config", "key": "体力本", "value": "不存在的副本"})

    def test_build_items_control_mapping(self):
        from src.gui.web_tabs import _build_items

        defaults = {
            "开关": False,
            "数字": 1.5,
            "文本": "abc",
            "列表": ["1", "2"],
            "下拉": "A",
            "按钮": "x",
        }
        types = {
            "下拉": {"type": "drop_down", "options": ["A", "B"], "sub_configs": {"A": ["开关"]}},
            "按钮": {"type": "button"},
            "列表": {"options_available": ["1", "2", "3"]},
        }
        items = {item["key"]: item for item in _build_items(defaults, dict(defaults), {}, {}, types)}
        self.assertEqual(items["开关"]["control"], "switch")
        self.assertEqual(items["数字"]["control"], "number")
        self.assertEqual(items["文本"]["control"], "text")
        self.assertEqual(items["列表"]["control"], "list")
        self.assertEqual(items["列表"]["options_available"], ["1", "2", "3"])
        self.assertEqual(items["下拉"]["control"], "select")
        self.assertEqual(items["下拉"]["options"], ["A", "B"])
        self.assertEqual(items["下拉"]["sub_configs"], {"A": ["开关"]})
        self.assertNotIn("按钮", items)

    def test_gui_env_override_enables_web(self):
        from src.config import _apply_gui_env_override

        cfg = {"use_gui": True}
        with mock.patch.dict(os.environ, {"OK_GUI": "web", "OK_WEB_LAUNCH_MODE": "server"}):
            _apply_gui_env_override(cfg)
        self.assertEqual(cfg["gui"], {"type": "web", "launch_mode": "server"})

        cfg2 = {"use_gui": True}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OK_GUI", None)
            _apply_gui_env_override(cfg2)
        self.assertNotIn("gui", cfg2)


if __name__ == "__main__":
    unittest.main()
