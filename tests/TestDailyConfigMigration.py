# -*- coding: utf-8 -*-
import unittest

from src.tasks.onetime.DailyTask import migrate_legacy_daily_config


class TestDailyConfigMigration(unittest.TestCase):
    """CodeRabbit 线程4/8：旧版日常配置键 → 多选列表键迁移。"""

    # ── 地区建设：旧布尔键 → 新多选列表 ──
    def test_regional_bools_merged_into_selected_options(self):
        config = {"⭐据点兑换": True, "⭐买物资": False, "⭐买卖货": True}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐地区建设"], ["据点兑换", "买卖货"])

    def test_regional_all_disabled_yields_empty_list(self):
        config = {"⭐据点兑换": False, "⭐买物资": False, "⭐买卖货": False}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐地区建设"], [])

    def test_regional_no_legacy_keys_untouched(self):
        config = {"其他配置": 1}
        result, modified = migrate_legacy_daily_config(config)
        self.assertFalse(modified)
        self.assertNotIn("⭐地区建设", result)

    def test_regional_existing_new_key_preserved(self):
        config = {"⭐地区建设": ["买物资"], "⭐据点兑换": True}
        result, modified = migrate_legacy_daily_config(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐地区建设"], ["买物资"])

    # ── 帝江号收菜：旧布尔开关 + 操作列表 → 列表 ──
    def test_boat_legacy_enabled_maps_ops_list(self):
        config = {"⭐帝江号收菜": True, "帝江号收菜操作": ["制造舱"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], ["制造舱"])

    def test_boat_legacy_enabled_without_ops_uses_defaults(self):
        config = {"⭐帝江号收菜": True}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], ["收集线索", "制造舱", "培养舱"])

    def test_boat_legacy_disabled_maps_empty(self):
        config = {"⭐帝江号收菜": False, "帝江号收菜操作": ["收集线索", "制造舱"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], [])

    def test_boat_already_list_untouched(self):
        config = {"⭐帝江号收菜": ["制造舱"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐帝江号收菜"], ["制造舱"])

    # ── 活动奖励：旧布尔开关 + 列表 → 列表 ──
    def test_reward_legacy_partial_selection(self):
        config = {"⭐活动奖励": True, "活动奖励": ["理智补给"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐活动奖励"], ["理智补给"])

    def test_reward_legacy_disabled_maps_empty(self):
        config = {"⭐活动奖励": False, "活动奖励": ["周常奖励"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐活动奖励"], [])

    def test_reward_already_list_untouched(self):
        config = {"⭐活动奖励": ["周常奖励", "刮刮乐"]}
        result, modified = migrate_legacy_daily_config(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐活动奖励"], ["周常奖励", "刮刮乐"])


if __name__ == "__main__":
    unittest.main()
