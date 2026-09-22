import os
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
