import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.config_migration import apply_value_migrations
from src.tasks.onetime.ActivityRewardTask import ActivityRewardTask
from src.tasks.onetime.BoatHarvestTask import BoatHarvestTask
from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.DeliveryTask import DeliveryTask, _legacy_delivery_run_mode
from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask


class TestConfigUiMigrations(unittest.TestCase):
    def test_old_child_false_survives_file_account_load_and_save(self):
        from ok.util.config import Config
        from ok.util.file import read_json_file, write_json_file

        from src.tasks.account.account_scope_store import get_account_task_overrides

        for task_type, key in (
            (BoatHarvestTask, "⭐帝江号收菜"),
            (ActivityRewardTask, "⭐活动奖励"),
            (RegionalBuildTask, "⭐地区建设"),
        ):
            with (
                self.subTest(task_type=task_type),
                tempfile.TemporaryDirectory() as tmp,
                patch.object(Config, "config_folder", tmp),
            ):
                task_name = task_type.__name__
                path = str(Path(tmp) / f"{task_name}.json")
                write_json_file(path, {key: False})
                write_json_file(
                    str(Path(tmp) / "account_scoped_overrides.json"),
                    {"accounts": {"user": {task_name: {key: False}}}},
                )
                task = task_type(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
                with patch("src.tasks.daily.split_config_migrator.maybe_run_pending_config_imports"):
                    task.load_config()
                    task.config.save_file()
                    task.load_config()
                self.assertEqual(task.config[key], [])
                self.assertEqual(read_json_file(path)[key], [])
                self.assertEqual(get_account_task_overrides("user", task_name)[key], [])

    def test_child_task_legacy_false_is_not_replaced_by_enabled_defaults(self):
        from src.core.config_migration import collect_config_migrations

        for task_type, key in (
            (BoatHarvestTask, "⭐帝江号收菜"),
            (ActivityRewardTask, "⭐活动奖励"),
            (RegionalBuildTask, "⭐地区建设"),
        ):
            with self.subTest(task_type=task_type):
                _, tables = collect_config_migrations(task_type)
                migrated, _ = apply_value_migrations({key: False}, tables)
                self.assertEqual(migrated[key], [])
                migrated, changed = apply_value_migrations(migrated, tables)
                self.assertFalse(changed)
                self.assertEqual(migrated[key], [])

    def test_account_daily_partial_switch_keeps_other_tasks_inherited(self):
        task = object.__new__(DailyTask)
        task.config = {DailyTask.CFG_BOAT_TASKS: ["帝江号整理"]}
        with patch.object(task, "_account_override_for", return_value={"⭐送礼": False}):
            self.assertFalse(task._task_selected(DailyTask.CFG_BOAT_TASKS, "送礼"))
            self.assertTrue(task._task_selected(DailyTask.CFG_BOAT_TASKS, "帝江号整理"))
            self.assertFalse(task._task_selected(DailyTask.CFG_BOAT_TASKS, "帝江号收菜"))

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
