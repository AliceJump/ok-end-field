import os
import tempfile
import unittest
from typing import ClassVar
from unittest.mock import patch

from ok.util.file import read_json_file, write_json_file

from src.core import config_migration, global_config_store
from src.core.BaseEfTask import BaseEfTask
from src.tasks.account.account_scope_store import get_account_task_overrides
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.end_command_mixin import EndCommandMixin


class TestConfigKeyCopy(unittest.TestCase):
    def test_mro_tables_keep_child_precedence_without_repeating_inherited_tables(self):
        class Parent:
            config_key_migrations: ClassVar = {"old": "parent", "inherited": "kept"}
            config_value_migrations: ClassVar = {"value": "parent"}

        class Child(Parent):
            config_key_migrations: ClassVar = {"old": "child"}
            config_value_migrations: ClassVar = {"value": "child"}

        class Task(Child):
            pass

        keys, values = config_migration.collect_config_migrations(Task)
        self.assertEqual(keys, {"old": "child", "inherited": "kept"})
        self.assertEqual(values, {"value": "child"})

    def test_base_load_migrates_account_aliases_before_binding_config(self):
        from ok import BaseTask
        from ok.util.config import Config

        class Task(BaseEfTask, EndCommandMixin):
            pass

        task = object.__new__(Task)
        with tempfile.TemporaryDirectory() as tmp, patch.object(Config, "config_folder", tmp):
            path = os.path.join(tmp, "account_scoped_overrides.json")
            write_json_file(
                path,
                {
                    "accounts": {
                        "user": {
                            "Task": {
                                "⭐执行结尾外部命令": False,
                                "结尾外部命令": "old command",
                                "外部命令": "new command",
                            },
                            "OtherTask": {"结尾外部命令": "untouched"},
                        }
                    }
                },
            )
            with (
                patch("src.tasks.daily.split_config_migrator.maybe_run_pending_config_imports"),
                patch("src.core.BaseEfTask.migrate_account_battle_mode_selectors"),
                patch.object(BaseTask, "load_config"),
            ):
                task.load_config()
                task.load_config()
            segment = get_account_task_overrides("user", "Task")
            self.assertIs(segment["⭐执行外部命令"], False)
            self.assertEqual(segment["外部命令"], "new command")
            self.assertEqual(segment["结尾外部命令"], "old command")
            self.assertEqual(get_account_task_overrides("user", "OtherTask"), {"结尾外部命令": "untouched"})

    def test_account_battle_source_rename_and_conversion_preserve_false(self):
        from ok.util.config import Config

        with tempfile.TemporaryDirectory() as tmp, patch.object(Config, "config_folder", tmp):
            path = os.path.join(tmp, "account_scoped_overrides.json")
            write_json_file(
                path,
                {
                    "accounts": {
                        "user": {
                            "BattleTask": {
                                "战斗配置": "使用独立配置",
                                "后台结束战斗通知": True,
                                "完成通知": False,
                            }
                        }
                    }
                },
            )
            keys, values = config_migration.collect_config_migrations(BattleMixin)
            config_migration.migrate_account_config("BattleTask", keys, values)
            segment = get_account_task_overrides("user", "BattleTask")
            self.assertIs(segment["使用独立配置"], True)
            self.assertIs(segment["完成通知"], False)

    def test_base_load_keeps_sparse_daily_switches_inherited(self):
        from ok import BaseTask
        from ok.util.config import Config

        from src.tasks.onetime.DailyTask import DailyTask

        task = object.__new__(DailyTask)
        with tempfile.TemporaryDirectory() as tmp, patch.object(Config, "config_folder", tmp):
            path = os.path.join(tmp, "account_scoped_overrides.json")
            write_json_file(path, {"accounts": {"user": {"DailyTask": {"⭐送礼": False}}}})
            with (
                patch("src.tasks.daily.split_config_migrator.maybe_run_pending_config_imports"),
                patch.object(BaseTask, "load_config"),
            ):
                task.load_config()
            self.assertEqual(get_account_task_overrides("user", "DailyTask"), {"⭐送礼": False})

    def test_regional_account_migration_keeps_partial_options_inherited(self):
        from ok import BaseTask
        from ok.util.config import Config

        from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask

        cases = [
            ({"⭐买物资": True}, {"据点兑换", "买物资", "买卖货"}),
            ({"⭐据点兑换": False}, {"买卖货"}),
            ({"⭐地区建设": False, "⭐买物资": True}, set()),
            ({"⭐地区建设": [], "⭐买物资": True}, set()),
            ({"⭐地区建设": ["买物资"], "⭐据点兑换": True}, {"买物资"}),
        ]
        for overrides, expected in cases:
            with (
                self.subTest(overrides=overrides),
                tempfile.TemporaryDirectory() as tmp,
                patch.object(Config, "config_folder", tmp),
            ):
                path = os.path.join(tmp, "account_scoped_overrides.json")
                write_json_file(path, {"accounts": {"user": {"RegionalBuildTask": overrides}}})
                task = object.__new__(RegionalBuildTask)
                with (
                    patch("src.tasks.daily.split_config_migrator.maybe_run_pending_config_imports"),
                    patch.object(BaseTask, "load_config"),
                ):
                    task.load_config()
                    task.load_config()
                segment = get_account_task_overrides("user", "RegionalBuildTask")
                self.assertEqual(segment, overrides)
                task.config = {"⭐地区建设": ["据点兑换", "买卖货"]}
                with patch.object(task, "_account_override_for", return_value=segment):
                    self.assertEqual(task._regional_options(), expected)
                    if overrides == {"⭐买物资": True}:
                        task.config["⭐地区建设"] = []
                        self.assertEqual(task._regional_options(), {"买物资"})

    def migrate(self, directory, *, replace_defaults):
        module = global_config_store if replace_defaults else config_migration
        with patch.object(module, "config_path", side_effect=lambda name: os.path.join(directory, name)):
            if replace_defaults:
                module._migrate_key_names_in_file("Task", {"old": "new"}, {"new": ""})
            else:
                module.migrate_config_file_keys("Task", {"old": "new"})

    def test_file_migrations_preserve_their_distinct_default_policies(self):
        cases = [
            ({"old": "user", "new": ""}, {"old": "user", "new": ""}, {"old": "user", "new": "user"}),
            ({"old": ""}, {"old": "", "new": ""}, {"old": ""}),
            ({"old": "user", "new": "chosen"}, {"old": "user", "new": "chosen"}, {"old": "user", "new": "chosen"}),
            ({"new": "chosen"}, {"old": "chosen", "new": "chosen"}, {"old": "chosen", "new": "chosen"}),
        ]
        for initial, missing_only, replace_default in cases:
            for replace_defaults, expected in ((False, missing_only), (True, replace_default)):
                with (
                    self.subTest(initial=initial, replace_defaults=replace_defaults),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    path = os.path.join(tmp, "Task.json")
                    write_json_file(path, initial)
                    self.migrate(tmp, replace_defaults=replace_defaults)
                    self.assertEqual(read_json_file(path), expected)
                    self.migrate(tmp, replace_defaults=replace_defaults)
                    self.assertEqual(read_json_file(path), expected)

    def test_missing_or_invalid_config_files_are_not_written(self):
        for replace_defaults in (False, True):
            with self.subTest(replace_defaults=replace_defaults), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "Task.json")
                self.migrate(tmp, replace_defaults=replace_defaults)
                self.assertFalse(os.path.exists(path))
                write_json_file(path, ["invalid config"])
                self.migrate(tmp, replace_defaults=replace_defaults)
                self.assertEqual(read_json_file(path), ["invalid config"])
