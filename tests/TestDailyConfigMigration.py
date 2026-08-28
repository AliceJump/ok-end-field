import unittest

from src.core.config_migration import (
    apply_value_migrations,
    legacy_bool_switch_to_list,
    merge_bool_options,
)

# 与 DailyTask.config_value_migrations 保持一致的迁移表（仅用于单元测试，
# 不触发 DailyTask 的 GUI 依赖）。生产迁移表由任务类属性声明。
BOAT_STAGES = ["收集线索", "制造舱", "培养舱"]
ACTIVITY_REWARDS = ["周常奖励", "理智补给", "刮刮乐"]

VALUE_MIGRATIONS = {
    "⭐地区建设": merge_bool_options(
        {
            "据点兑换": "⭐据点兑换",
            "买物资": "⭐买物资",
            "买卖货": "⭐买卖货",
        }
    ),
    "⭐帝江号收菜": legacy_bool_switch_to_list(
        ops_key="帝江号收菜操作",
        defaults=BOAT_STAGES,
    ),
    "⭐活动奖励": legacy_bool_switch_to_list(
        ops_key="活动奖励",
        defaults=ACTIVITY_REWARDS,
    ),
}


class TestDailyConfigMigration(unittest.TestCase):
    """CodeRabbit 线程4/8：旧版日常配置键 → 多选列表键迁移。"""

    def _apply(self, config):
        return apply_value_migrations(dict(config), VALUE_MIGRATIONS)

    # ── 地区建设：旧布尔键 → 新多选列表 ──
    def test_regional_bools_merged_into_selected_options(self):
        config = {"⭐据点兑换": True, "⭐买物资": False, "⭐买卖货": True}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐地区建设"], ["据点兑换", "买卖货"])

    def test_regional_all_disabled_yields_empty_list(self):
        config = {"⭐据点兑换": False, "⭐买物资": False, "⭐买卖货": False}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐地区建设"], [])

    def test_regional_no_legacy_keys_untouched(self):
        config = {"其他配置": 1}
        result, modified = self._apply(config)
        self.assertFalse(modified)
        self.assertNotIn("⭐地区建设", result)

    def test_regional_existing_new_key_preserved(self):
        config = {"⭐地区建设": ["买物资"], "⭐据点兑换": True}
        result, modified = self._apply(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐地区建设"], ["买物资"])

    # ── 帝江号收菜：旧布尔开关 + 操作列表 → 列表 ──
    def test_boat_legacy_enabled_maps_ops_list(self):
        config = {"⭐帝江号收菜": True, "帝江号收菜操作": ["制造舱"]}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], ["制造舱"])

    def test_boat_legacy_enabled_without_ops_uses_defaults(self):
        config = {"⭐帝江号收菜": True}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], BOAT_STAGES)

    def test_boat_legacy_disabled_maps_empty(self):
        config = {"⭐帝江号收菜": False, "帝江号收菜操作": ["收集线索", "制造舱"]}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐帝江号收菜"], [])

    def test_boat_already_list_untouched(self):
        config = {"⭐帝江号收菜": ["制造舱"]}
        result, modified = self._apply(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐帝江号收菜"], ["制造舱"])

    # ── 活动奖励：旧布尔开关 + 列表 → 列表 ──
    def test_reward_legacy_partial_selection(self):
        config = {"⭐活动奖励": True, "活动奖励": ["理智补给"]}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐活动奖励"], ["理智补给"])

    def test_reward_legacy_disabled_maps_empty(self):
        config = {"⭐活动奖励": False, "活动奖励": ["周常奖励"]}
        result, modified = self._apply(config)
        self.assertTrue(modified)
        self.assertEqual(result["⭐活动奖励"], [])

    def test_reward_already_list_untouched(self):
        config = {"⭐活动奖励": ["周常奖励", "刮刮乐"]}
        result, modified = self._apply(config)
        self.assertFalse(modified)
        self.assertEqual(result["⭐活动奖励"], ["周常奖励", "刮刮乐"])


if __name__ == "__main__":
    unittest.main()
