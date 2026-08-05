# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from src.core.global_config_store import (
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_CONFIG_TYPE,
    ZIP_LINE_DELIVERY_GROUP,
    ZIP_LINE_GATHER_GROUP,
    ZIP_LINE_GROUP_KEY,
)
from src.core import global_config_store


class TestZipLineConfig(unittest.TestCase):
    def test_zipline_config_has_explicit_route_groups(self):
        group_meta = ZIP_LINE_CONFIG_TYPE[ZIP_LINE_GROUP_KEY]
        self.assertEqual(group_meta["options"], [ZIP_LINE_DELIVERY_GROUP, ZIP_LINE_GATHER_GROUP])
        self.assertIn(ZIP_LINE_DELIVERY_GROUP, group_meta["sub_configs"])
        self.assertIn(ZIP_LINE_GATHER_GROUP, group_meta["sub_configs"])

    @patch("src.tasks.account.account_scope_store.update_overrides")
    def test_legacy_account_routes_move_to_shared_zipline_config(self, update_overrides):
        global_config_store._migrate_legacy_zip_line_account_overrides()
        updater = update_overrides.call_args.args[0]
        data = {
            "accounts": {
                "account-1": {
                    "DailyTask": {
                        "是否启用滚动放大视角": True,
                        "武陵城": "12,34",
                        "试验园区": "",
                    },
                    "DeliveryTask": {
                        "通向送货点试验园区": "56",
                    },
                },
                "account-2": {
                    "DailyTask": {
                        "武陵城": "98",
                    },
                },
            }
        }

        migrated = updater(data)

        self.assertEqual(
            migrated["accounts"]["account-1"][ZIP_LINE_CONFIG_NAME],
            {
                "是否启用滚动放大视角": True,
                "武陵城": "12,34",
                "试验园区": "",
                "通向试验园区送货点": "56",
            },
        )
        self.assertEqual(migrated["accounts"]["account-2"][ZIP_LINE_CONFIG_NAME], {"武陵城": "98"})

    def test_legacy_zip_line_global_migration_ignores_account_overrides(self):
        """账号覆盖中的冲突路线值不得污染全局滑索迁移（只从 legacy 任务配置文件收集）。"""
        files = {
            "configs/DeliveryTask.json": {"通向送货点试验园区": "111"},
            "configs/DailyTask.json": {"通向送货点试验园区": "222"},
            "configs/BattleTask.json": {},
            "configs/account_scoped_overrides.json": {
                "accounts": {
                    "acct-1": {"DailyTask": {"通向送货点试验园区": "999"}},
                }
            },
        }

        def fake_read(path):
            return files.get(path.replace(os.sep, "/"), {})

        with patch.object(
            global_config_store,
            "get_relative_path",
            side_effect=lambda *parts: os.path.join(*parts),
        ), patch.object(global_config_store, "read_json_file", side_effect=fake_read):
            values = global_config_store._collect_legacy_zip_line_values()

        # 账号覆盖值 999 不得进入全局迁移
        self.assertNotIn("999", values.values())
        # 迁移值只能来自 legacy 任务配置文件（DeliveryTask=111 / DailyTask=222）
        self.assertIn(values.get("通向试验园区送货点"), ("111", "222"))

    def test_iter_legacy_zip_line_task_data_skips_account_overrides(self):
        """迭代器只遍历 legacy 任务配置文件，不再读取 account_scoped_overrides.json。"""
        files = {
            "configs/DeliveryTask.json": {"通向送货点试验园区": "111"},
            "configs/DailyTask.json": {},
            "configs/BattleTask.json": {},
            "configs/account_scoped_overrides.json": {
                "accounts": {"acct-1": {"DailyTask": {"通向送货点试验园区": "999"}}}
            },
        }
        read_paths = []

        def fake_read(path):
            read_paths.append(path)
            return files.get(path.replace(os.sep, "/"), {})

        with patch.object(
            global_config_store,
            "get_relative_path",
            side_effect=lambda *parts: os.path.join(*parts),
        ), patch.object(global_config_store, "read_json_file", side_effect=fake_read):
            data_list = [
                data for data, _ in global_config_store._iter_legacy_zip_line_task_data()
            ]

        self.assertEqual(len(data_list), 3)
        self.assertFalse(
            any("account_scoped_overrides" in path for path in read_paths),
            "不应读取 account_scoped_overrides.json",
        )


if __name__ == "__main__":
    unittest.main()
