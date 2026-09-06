import unittest
from unittest.mock import patch

from src.core.BattleConfig import (
    BATTLE_CONFIG_MODE_KEY,
    KEY_BATTLE_INITIAL_WAIT,
    KEY_COMPLETE_NOTIFY,
    KEY_COND_SEQUENCE,
    KEY_INSTANT_LINK,
    KEY_INSTANT_ULT,
    KEY_NO_NUMBER_OPERATION_INTERVAL,
    KEY_ROTATION_SEQUENCE,
    KEY_SKILL_ALLOWLIST,
    KEY_START_SKILL_POINT,
    KEY_ULT_RELEASE_MODE,
    BattleConfigManager,
)
from src.gui.AccountConfigTab import AccountConfigTab
from src.tasks.mixin.battle_mixin import BattleMixin


class _DummyTask:
    name = "测试任务"
    icon = None
    default_config = {
        "普通配置": 1,
        "隐藏配置": 2,
        "强制配置": 3,
        "多账户模式": True,
        "配置选择": "常用配置",
        "其他配置项": 6,
        "打开帮助": "帮助",
    }
    config = dict(default_config)
    config_description = {}
    config_type = {
        "隐藏配置": {"type": "drop_down", "options": [1, 2]},
        "强制配置": {"type": "global"},
        "配置选择": {
            "type": "drop_down",
            "options": ["常用配置", "其他配置"],
            "sub_configs": {
                "常用配置": ["普通配置"],
                "其他配置": ["其他配置项"],
            },
        },
        "打开帮助": {"type": "button"},
    }
    account_config_blacklist = {"隐藏配置"}
    account_config_whitelist = {"隐藏配置", "强制配置", "额外配置"}
    account_config_defaults = {"额外配置": 7}
    account_config_description = {}
    account_config_type = {}

    @staticmethod
    def get_account_config_base_value(key, default=None):
        if key == "额外配置":
            return 9
        return default


class _AccountConfigHarness:
    ALWAYS_HIDDEN_CONFIG_KEYS = AccountConfigTab.ALWAYS_HIDDEN_CONFIG_KEYS
    _is_supported_value = staticmethod(AccountConfigTab._is_supported_value)
    _config_key_set = staticmethod(AccountConfigTab._config_key_set)
    _account_config_schema = AccountConfigTab._account_config_schema
    _account_config_rules = AccountConfigTab._account_config_rules
    _account_config_base_value = staticmethod(AccountConfigTab._account_config_base_value)
    _coerce_like = staticmethod(AccountConfigTab._coerce_like)
    _build_virtual_config = AccountConfigTab._build_virtual_config
    _apply_current_task_override = AccountConfigTab._apply_current_task_override
    _apply_current_map_content = AccountConfigTab._apply_current_map_content
    _has_current_task_changes = AccountConfigTab._has_current_task_changes
    _save_pending_changes = AccountConfigTab._save_pending_changes


class TestAccountConfigRules(unittest.TestCase):
    def test_blacklist_wins_and_whitelist_always_displays(self):
        tab = _AccountConfigHarness()
        tab.overrides_data = {
            "accounts": {
                "acc": {
                    "_DummyTask": {
                        "普通配置": 4,
                    }
                }
            }
        }

        config, editable_keys, base_values, total = tab._build_virtual_config(
            _DummyTask(),
            "acc",
            "",
            only_diff=True,
        )

        self.assertEqual(editable_keys, ["普通配置", "强制配置", "配置选择", "额外配置"])
        self.assertNotIn("隐藏配置", config)
        self.assertNotIn("多账户模式", config)
        self.assertNotIn("其他配置项", config)
        self.assertNotIn("打开帮助", config)
        self.assertEqual(config["强制配置"], 3)
        self.assertEqual(config["配置选择"], "常用配置")
        self.assertEqual(config["额外配置"], 9)
        self.assertEqual(base_values["额外配置"], 9)
        self.assertEqual(total, 4)

    def test_saving_writes_full_schema_and_removes_invalid_overrides(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 5}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 1}
        tab.overrides_data = {
            "accounts": {
                "acc": {
                    "_DummyTask": {
                        "隐藏配置": 8,
                        "未显示合法配置": 9,
                    }
                }
            }
        }
        changed = tab._apply_current_task_override(cleanup_blacklist=True)

        self.assertTrue(changed)
        saved = tab.overrides_data["accounts"]["acc"]["_DummyTask"]
        self.assertEqual(
            saved,
            {
                "普通配置": 5,
                "强制配置": 3,
                "配置选择": "常用配置",
                "额外配置": 9,
            },
        )

    def test_unchanged_visible_values_still_complete_full_snapshot(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 5}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 5}
        tab.overrides_data = {
            "accounts": {
                "acc": {
                    "_DummyTask": {
                        "普通配置": 5,
                    }
                }
            }
        }

        changed = tab._apply_current_task_override(cleanup_blacklist=True)
        self.assertTrue(changed)
        self.assertEqual(
            tab.overrides_data["accounts"]["acc"]["_DummyTask"],
            {
                "普通配置": 5,
                "强制配置": 3,
                "配置选择": "常用配置",
                "额外配置": 9,
            },
        )

    def test_base_value_is_persisted_in_full_snapshot(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 1}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 5}
        tab.overrides_data = {
            "accounts": {
                "acc": {
                    "_DummyTask": {
                        "普通配置": 5,
                    }
                }
            }
        }

        changed = tab._apply_current_task_override(cleanup_blacklist=True)

        self.assertTrue(changed)
        saved = tab.overrides_data["accounts"]["acc"]["_DummyTask"]
        self.assertEqual(saved["普通配置"], 1)
        self.assertEqual(saved["强制配置"], 3)

    def test_diff_view_save_preserves_hidden_snapshot_values(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 5}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_account_name = ""
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 4}
        tab.overrides_data = {
            "accounts": {
                "acc": {
                    "_DummyTask": {
                        "普通配置": 4,
                        "强制配置": 8,
                        "配置选择": "其他配置",
                        "额外配置": 11,
                    }
                }
            }
        }

        tab._apply_current_task_override(cleanup_blacklist=True)

        self.assertEqual(
            tab.overrides_data["accounts"]["acc"]["_DummyTask"],
            {
                "普通配置": 5,
                "强制配置": 8,
                "配置选择": "其他配置",
                "额外配置": 11,
            },
        )

    def test_explicit_save_without_edits_writes_full_snapshot(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 1}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_account_name = ""
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 1}
        tab.current_map_account_key = ""
        tab.overrides_data = {"accounts": {}}

        with patch(
            "src.gui.AccountConfigTab.update_overrides",
            side_effect=lambda updater: updater({"accounts": {}}),
        ):
            changed = tab._save_pending_changes(cleanup_blacklist=True)

        self.assertTrue(changed)
        self.assertEqual(
            tab.overrides_data["accounts"]["acc"]["_DummyTask"],
            {
                "普通配置": 1,
                "强制配置": 3,
                "配置选择": "常用配置",
                "额外配置": 9,
            },
        )

    def test_pending_save_merges_latest_external_overrides(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 6}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 5}
        tab.current_map_account_key = ""
        tab.overrides_data = {"accounts": {"acc": {"_DummyTask": {"普通配置": 5}}}}
        latest = {
            "accounts": {
                "acc": {"_DummyTask": {"普通配置": 5}},
                "other": {"OtherTask": {"值": 9}},
            }
        }

        with patch(
            "src.gui.AccountConfigTab.update_overrides",
            side_effect=lambda updater: updater(latest),
        ):
            tab._save_pending_changes()

        self.assertEqual(
            tab.overrides_data["accounts"]["acc"]["_DummyTask"]["普通配置"],
            6,
        )
        self.assertEqual(tab.overrides_data["accounts"]["other"]["OtherTask"]["值"], 9)

    def test_external_convergence_advances_local_dirty_baseline(self):
        tab = _AccountConfigHarness()
        tab.current_virtual_config = {"普通配置": 6}
        tab.current_task = _DummyTask()
        tab.current_account_key = "acc"
        tab.current_editable_keys = ["普通配置"]
        tab.current_base_values = {"普通配置": 1}
        tab.current_original_values = {"普通配置": 5}
        tab.current_map_account_key = ""
        latest = {"accounts": {"acc": {"_DummyTask": {"普通配置": 6}}}}

        with patch(
            "src.gui.AccountConfigTab.update_overrides",
            side_effect=lambda updater: updater(latest),
        ):
            tab._save_pending_changes()

        self.assertEqual(tab.current_original_values["普通配置"], 6)
        tab.current_virtual_config["普通配置"] = 5
        self.assertTrue(tab._has_current_task_changes())


class TestBattleConfigOverrides(unittest.TestCase):
    def make_battle_task(self, config):
        task = object.__new__(BattleMixin)
        task.config = config
        task.battle_config_manager = BattleConfigManager({"启动技能点数": 2})
        # Mock log_debug to avoid errors
        task.log_debug = lambda msg: None
        return task

    def test_task_config_uses_independent_switch(self):
        task = object.__new__(BattleMixin)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}

        task._register_battle_config()

        mode_type = task.config_type[BATTLE_CONFIG_MODE_KEY]
        # 开关类型：无 options，sub_configs 以 True 为键
        self.assertNotIn("options", mode_type)
        independent_keys = mode_type["sub_configs"][True]
        # independent_keys 应包含 DEFAULT_BATTLE_CONFIG 中排除 BATTLE_GROUP_CONFIGS 的键
        expected = [
            KEY_ULT_RELEASE_MODE,
            KEY_START_SKILL_POINT,
            KEY_COMPLETE_NOTIFY,
            KEY_NO_NUMBER_OPERATION_INTERVAL,
            KEY_BATTLE_INITIAL_WAIT,
            KEY_ROTATION_SEQUENCE,
            KEY_COND_SEQUENCE,
            KEY_INSTANT_ULT,
            KEY_INSTANT_LINK,
            KEY_SKILL_ALLOWLIST,
        ]
        self.assertEqual(independent_keys, expected)

    def test_task_config_overrides_global_battle_config(self):
        task = self.make_battle_task(
            {
                BATTLE_CONFIG_MODE_KEY: True,
                "启动技能点数": 3,
            }
        )

        self.assertEqual(task.get_battle_config("启动技能点数"), 3)

    def test_global_mode_ignores_independent_battle_value(self):
        task = self.make_battle_task(
            {
                BATTLE_CONFIG_MODE_KEY: False,
                "启动技能点数": 3,
            }
        )

        self.assertEqual(task.get_battle_config("启动技能点数"), 2)


class TestUseIndependentParsing(unittest.TestCase):
    """测试「使用独立配置」值的解析和回退行为"""

    def make_task(self):
        task = object.__new__(BattleMixin)
        task.log_debug = lambda msg: None
        return task

    def test_legacy_dropdown_use_independent_string(self):
        """旧下拉框值「使用独立配置」应解析为 True"""
        task = self.make_task()
        self.assertTrue(task._parse_use_independent("使用独立配置"))

    def test_legacy_dropdown_use_global_string(self):
        """旧下拉框值「使用全局配置」应解析为 False"""
        task = self.make_task()
        self.assertFalse(task._parse_use_independent("使用全局配置"))

    def test_string_true_variations(self):
        """常见的字符串 true 表示应解析为 True"""
        task = self.make_task()
        for value in ["true", "True", "TRUE", "1", "yes", "Yes", "YES", "on", "On", "ON"]:
            with self.subTest(value=value):
                self.assertTrue(task._parse_use_independent(value))

    def test_string_false_variations(self):
        """常见的字符串 false 表示应解析为 False"""
        task = self.make_task()
        for value in ["false", "False", "FALSE", "0", "no", "No", "NO", "off", "Off", "OFF"]:
            with self.subTest(value=value):
                self.assertFalse(task._parse_use_independent(value))

    def test_invalid_string_returns_false(self):
        """无效字符串应回退到 False（默认值）"""
        task = self.make_task()
        for value in ["invalid", "random_text", "2", "maybe"]:
            with self.subTest(value=value):
                self.assertFalse(task._parse_use_independent(value))


class TestUseIndependentBattleConfigSelection(unittest.TestCase):
    """测试不同「使用独立配置」值下的战斗配置选择行为"""

    def make_task_with_value(self, use_independent_value):
        task = object.__new__(BattleMixin)
        task.config = {
            BATTLE_CONFIG_MODE_KEY: use_independent_value,
            "启动技能点数": 3,
        }
        task.battle_config_manager = BattleConfigManager({"启动技能点数": 2})
        task.log_debug = lambda msg: None
        return task

    def test_legacy_string_use_independent_selects_task_config(self):
        """旧字符串值「使用独立配置」应选择任务配置"""
        task = self.make_task_with_value("使用独立配置")
        self.assertEqual(task.get_battle_config("启动技能点数"), 3)

    def test_legacy_string_use_global_selects_global_config(self):
        """旧字符串值「使用全局配置」应选择全局配置"""
        task = self.make_task_with_value("使用全局配置")
        self.assertEqual(task.get_battle_config("启动技能点数"), 2)


if __name__ == "__main__":
    unittest.main()
