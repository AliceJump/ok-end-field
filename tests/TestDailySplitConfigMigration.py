import os
import tempfile
import unittest
from unittest.mock import patch

from ok.util.config import Config
from ok.util.file import read_json_file, write_json_file

from src.tasks.daily import split_config_migrator
from src.tasks.daily.split_config_migrator import (
    DAILY_SPLIT_IMPORTS,
    run_pending_config_imports,
)


def _pilot_table():
    """返回只含试点批次的声明表，避免生产表后续扩展影响断言。"""
    return {"daily_split_pilot_v1": DAILY_SPLIT_IMPORTS["daily_split_pilot_v1"]}


class TestDailySplitConfigMigration(unittest.TestCase):
    def _write_configs(self, configs_dir, files):
        os.makedirs(configs_dir, exist_ok=True)
        for name, data in files.items():
            write_json_file(os.path.join(configs_dir, name), data)

    def _read_config(self, configs_dir, name):
        return read_json_file(os.path.join(configs_dir, name))

    def _run(self, configs_dir, table):
        """把 Config.config_folder 指向临时目录后执行迁移，返回完成批次列表。"""
        with patch.object(Config, "config_folder", configs_dir):
            return run_pending_config_imports(table)

    def test_plain_key_copied_to_target_and_source_kept(self):
        """参数键从 DailyTask.json 复制到 CreditCollectTask.json，来源键保留（回滚安全）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(configs, {"DailyTask.json": {"尝试仅收培育室": False, "⭐收信用": True}})
            self._run(configs, _pilot_table())

            data = self._read_config(configs, "CreditCollectTask.json")
            self.assertEqual(data["尝试仅收培育室"], False)
            source = self._read_config(configs, "DailyTask.json")
            self.assertEqual(source["尝试仅收培育室"], False)

    def test_existing_target_value_not_overwritten(self):
        """目标任务已有该键（用户已自行配置）时不覆盖（setdefault 语义）。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(
                configs,
                {
                    "DailyTask.json": {"尝试仅收培育室": False},
                    "CreditCollectTask.json": {"尝试仅收培育室": True},
                },
            )
            self._run(configs, _pilot_table())

            data = self._read_config(configs, "CreditCollectTask.json")
            self.assertEqual(data["尝试仅收培育室"], True)

    def test_missing_source_key_creates_no_target_file(self):
        """来源文件缺失该键时不写目标文件，避免留下空配置文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(configs, {"DailyTask.json": {"⭐收信用": True}})
            self._run(configs, _pilot_table())

            self.assertFalse(os.path.isfile(os.path.join(configs, "CreditCollectTask.json")))

    def test_batch_marker_accumulates_and_skips_completed(self):
        """完成批次记入状态文件，重复执行不再产生动作。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(configs, {"DailyTask.json": {"尝试仅收培育室": True}})
            executed = self._run(configs, _pilot_table())
            self.assertEqual(executed, ["daily_split_pilot_v1"])

            state = read_json_file(os.path.join(configs, "_daily_split_migrations.json"))
            self.assertIn("daily_split_pilot_v1", state.get("completed_batches", []))

            second = self._run(configs, _pilot_table())
            self.assertEqual(second, [])

    def test_backup_created_for_source_configs(self):
        """首次执行批次前，来源配置文件备份到 daily_split_migration_backup/<批次>/。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(configs, {"DailyTask.json": {"尝试仅收培育室": True}})
            self._run(configs, _pilot_table())

            backup_file = os.path.join(
                configs, "daily_split_migration_backup", "daily_split_pilot_v1", "DailyTask.json"
            )
            self.assertTrue(os.path.isfile(backup_file))
            self.assertEqual(read_json_file(backup_file)["尝试仅收培育室"], True)

    def test_account_overrides_moved_to_target_segment(self):
        """账号覆盖存储中 DailyTask 段的参数键复制到目标类名段，旧段保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(
                configs,
                {
                    "DailyTask.json": {"尝试仅收培育室": True},
                    "account_scoped_overrides.json": {
                        "accounts": {
                            "acct-1": {"DailyTask": {"尝试仅收培育室": False, "⭐收信用": True}},
                        }
                    },
                },
            )
            self._run(configs, _pilot_table())

            store = read_json_file(os.path.join(configs, "account_scoped_overrides.json"))
            # update_overrides 会把测试用账号键规范化为内部 id，按任务段查找断言
            target_segments = [
                tasks for tasks in store["accounts"].values() if isinstance(tasks.get("CreditCollectTask"), dict)
            ]
            self.assertEqual(len(target_segments), 1)
            self.assertEqual(target_segments[0]["CreditCollectTask"]["尝试仅收培育室"], False)
            # 旧段保留（回滚安全），非参数键不搬
            source_segments = [
                tasks for tasks in store["accounts"].values() if isinstance(tasks.get("DailyTask"), dict)
            ]
            self.assertEqual(len(source_segments), 1)
            self.assertEqual(source_segments[0]["DailyTask"]["尝试仅收培育室"], False)
            self.assertEqual(source_segments[0]["DailyTask"]["⭐收信用"], True)

    def test_callable_entry_transforms_value(self):
        """callable 条目基于来源与目标配置计算值，返回 _NO_MIGRATION 时跳过。"""
        from src.core.config_migration import _NO_MIGRATION

        def transform(source_config, target_config, target_key):
            if not source_config.get("旧开关"):
                return _NO_MIGRATION
            return ["选项A"]

        table = {"batch_callable": {"SomeTargetTask": {"DailyTask": {"新键": transform}}}}
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(configs, {"DailyTask.json": {"旧开关": True}})
            self._run(configs, table)

            data = self._read_config(configs, "SomeTargetTask.json")
            self.assertEqual(data["新键"], ["选项A"])

    def test_daily_battle_and_delivery_configs_do_not_change_standalone_tasks(self):
        """日常专属配置从旧日常卡片迁入，已有独立任务配置保持原值。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(
                configs,
                {
                    "DailyTask.json": {
                        "体力本": "技能提升",
                        "刷本序列": ["干员经验", "技能提升"],
                        "使用独立配置": True,
                        "目标券数": ["73100"],
                        "地区切换": "四号谷地",
                    },
                    "BattleTask.json": {"体力本": "干员经验"},
                    "DeliveryTask.json": {"目标券数": ["119000"], "地区切换": "武陵城"},
                },
            )
            self._run(configs, DAILY_SPLIT_IMPORTS)

            daily_battle = self._read_config(configs, "DailyBattleTask.json")
            daily_delivery = self._read_config(configs, "DailyDeliveryTask.json")
            self.assertEqual(daily_battle["体力本"], "技能提升")
            self.assertEqual(daily_battle["刷本序列"], ["干员经验", "技能提升"])
            self.assertTrue(daily_battle["使用独立配置"])
            self.assertEqual(daily_delivery["目标券数"], ["73100"])
            self.assertEqual(daily_delivery["地区切换"], "四号谷地")
            self.assertEqual(self._read_config(configs, "BattleTask.json")["体力本"], "干员经验")
            standalone_delivery = self._read_config(configs, "DeliveryTask.json")
            self.assertEqual(standalone_delivery["目标券数"], ["119000"])
            self.assertEqual(standalone_delivery["地区切换"], "武陵城")

    def test_v3_uses_v2_backup_after_old_daily_keys_were_removed(self):
        """已运行 phase2 的用户仍能从备份恢复日常刷体力和送货参数。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            backup_dir = os.path.join(configs, "daily_split_migration_backup", "daily_split_v2")
            self._write_configs(
                configs,
                {
                    "DailyTask.json": {"⭐刷体力": True, "⭐自动送货": True},
                    "_daily_split_migrations.json": {
                        "completed_batches": ["daily_split_pilot_v1", "daily_split_v2"]
                    },
                },
            )
            self._write_configs(
                backup_dir,
                {"DailyTask.json": {"体力本": "干员进阶", "目标券数": ["79800"]}},
            )
            self._run(configs, DAILY_SPLIT_IMPORTS)

            self.assertEqual(self._read_config(configs, "DailyBattleTask.json")["体力本"], "干员进阶")
            self.assertEqual(self._read_config(configs, "DailyDeliveryTask.json")["目标券数"], ["79800"])

    def test_v3_restores_account_overrides_from_v2_backup(self):
        """旧日常账号段被清理后，日常专属配置仍可按原账号恢复。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            backup_dir = os.path.join(configs, "daily_split_migration_backup", "daily_split_v2")
            self._write_configs(
                configs,
                {
                    "_daily_split_migrations.json": {
                        "completed_batches": ["daily_split_pilot_v1", "daily_split_v2"]
                    },
                    "account_scoped_overrides.json": {
                        "account_registry": {"acc_test": {"username": "玩家A", "aliases": ["玩家A"]}},
                        "accounts": {"acc_test": {"DailyTask": {"⭐刷体力": True}}},
                    },
                },
            )
            self._write_configs(
                backup_dir,
                {
                    "account_scoped_overrides.json": {
                        "accounts": {
                            "玩家A": {
                                "DailyTask": {
                                    "体力本": "技能提升",
                                    "目标券数": ["73100"],
                                    "⭐地区建设": True,
                                    "⭐据点兑换": True,
                                }
                            }
                        }
                    }
                },
            )
            self._run(configs, DAILY_SPLIT_IMPORTS)

            store = self._read_config(configs, "account_scoped_overrides.json")
            tasks = store["accounts"]["acc_test"]
            self.assertEqual(tasks["DailyBattleTask"]["体力本"], "技能提升")
            self.assertEqual(tasks["DailyDeliveryTask"]["目标券数"], ["73100"])
            self.assertEqual(tasks["RegionalBuildTask"]["⭐地区建设"], ["据点兑换"])

    def test_legacy_account_overrides_convert_to_lists(self):
        """旧布尔覆盖转换为列表，同时保留目标段已有的有效列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            configs = os.path.join(tmp, "configs")
            self._write_configs(
                configs,
                {
                    "DailyTask.json": {"⭐帝江号收菜": True, "⭐活动奖励": True},
                    "account_scoped_overrides.json": {
                        "accounts": {
                            "acct-1": {
                                "DailyTask": {
                                    "⭐帝江号收菜": True,
                                    "帝江号收菜操作": ["收集线索"],
                                    "⭐活动奖励": False,
                                    "⭐据点兑换": True,
                                    "⭐买物资": False,
                                    "⭐买卖货": True,
                                },
                                "ActivityRewardTask": {"⭐活动奖励": ["周常奖励"]},
                            }
                        }
                    },
                },
            )
            self._run(configs, DAILY_SPLIT_IMPORTS)

            store = self._read_config(configs, "account_scoped_overrides.json")
            tasks = next(iter(store["accounts"].values()))
            self.assertEqual(tasks["BoatHarvestTask"]["⭐帝江号收菜"], ["收集线索"])
            self.assertEqual(tasks["RegionalBuildTask"]["⭐地区建设"], ["据点兑换", "买卖货"])
            self.assertEqual(tasks["ActivityRewardTask"]["⭐活动奖励"], ["周常奖励"])

    def test_maybe_run_only_once_per_process(self):
        """maybe_run_pending_config_imports 进程内只真正执行一次。

        首次调用执行未完成批次并置进程内标记；第二次调用直接跳过。
        """
        from src.tasks.daily.split_config_migrator import maybe_run_pending_config_imports

        original_flag = split_config_migrator._PROCESS_DONE
        split_config_migrator._PROCESS_DONE = False
        try:
            table = {"batch_once": {"SomeTargetTask": {"DailyTask": {"键": "键"}}}}
            with tempfile.TemporaryDirectory() as tmp:
                configs = os.path.join(tmp, "configs")
                self._write_configs(configs, {"DailyTask.json": {"键": 1}})
                with (
                    patch.object(split_config_migrator, "DAILY_SPLIT_IMPORTS", table),
                    patch.object(Config, "config_folder", configs),
                    patch.object(
                        split_config_migrator, "_run_batch", wraps=split_config_migrator._run_batch
                    ) as run_batch,
                ):
                    maybe_run_pending_config_imports()
                    maybe_run_pending_config_imports()
                self.assertEqual(run_batch.call_count, 1)
        finally:
            split_config_migrator._PROCESS_DONE = original_flag


if __name__ == "__main__":
    unittest.main()
