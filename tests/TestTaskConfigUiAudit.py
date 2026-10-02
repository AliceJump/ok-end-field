import unittest

from src.core.config_migration import apply_value_migrations
from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.DeliveryTask import DeliveryTask, _legacy_delivery_run_mode
from src.tasks.trigger.ItemNavigatorTask import (
    MAP_SOURCE_ACCOUNT,
    MAP_SOURCE_AUTO,
    MAP_SOURCE_MANUAL,
    _legacy_map_source,
)


class TestConfigUiMigrations(unittest.TestCase):
    def test_daily_legacy_bools_become_fixed_group_lists(self):
        config = {
            "⭐送礼": True,
            "⭐帝江号整理": False,
            "⭐帝江号收菜": True,
            "⭐刷体力": False,
            "⭐演算": True,
            "⭐自动送货": True,
            "⭐传送到帝江号右侧传送点": False,
        }
        migrated, _ = apply_value_migrations(dict(config), DailyTask.config_value_migrations)
        self.assertEqual(migrated[DailyTask.CFG_BOAT_TASKS], ["送礼", "帝江号收菜"])
        self.assertEqual(migrated[DailyTask.CFG_BATTLE_TASKS], ["演算"])
        self.assertEqual(migrated[DailyTask.CFG_DELIVERY_TASKS], ["自动送货"])
        self.assertEqual(migrated[DailyTask.CFG_TAIL_TASKS], [])

    def test_delivery_conflicting_legacy_flags_preserve_old_accept_priority(self):
        config = {"仅接取": True, "仅送货": True}
        self.assertEqual(_legacy_delivery_run_mode(config, DeliveryTask.CFG_RUN_MODE), DeliveryTask.RUN_ONLY_ACCEPT)

    def test_delivery_only_deliver_migrates_to_single_mode(self):
        config = {"仅接取": False, "仅送货": True}
        self.assertEqual(_legacy_delivery_run_mode(config, DeliveryTask.CFG_RUN_MODE), DeliveryTask.RUN_ONLY_DELIVER)

    def test_map_source_migration_preserves_old_precedence(self):
        self.assertEqual(_legacy_map_source({"content": "x", "地图账号": "a"}, "地图数据来源"), MAP_SOURCE_MANUAL)
        self.assertEqual(_legacy_map_source({"content": "", "地图账号": "a"}, "地图数据来源"), MAP_SOURCE_ACCOUNT)
        self.assertEqual(_legacy_map_source({"content": "", "地图账号": ""}, "地图数据来源"), MAP_SOURCE_AUTO)


class TestUniqueSubConfigParents(unittest.TestCase):
    def test_same_child_under_different_parents_is_rejected(self):
        task = object.__new__(DailyTask)
        task.config_type = {
            "父项A": {"sub_configs": {True: ["共享子项"]}},
            "父项B": {"sub_configs": {"模式": ["共享子项"]}},
        }
        with self.assertRaisesRegex(ValueError, "共享子项"):
            task.validate_unique_sub_config_parents()

    def test_same_parent_may_show_child_for_multiple_values(self):
        task = object.__new__(DailyTask)
        task.config_type = {
            "父项": {"sub_configs": {"模式A": ["共享子项"], "模式B": ["共享子项"]}},
        }
        self.assertEqual(task.validate_unique_sub_config_parents(), {"共享子项": "父项"})


if __name__ == "__main__":
    unittest.main()
