import os
import tempfile
import unittest
from unittest.mock import patch

from ok.util import config as config_module
from ok.util.file import read_json_file, write_json_file
from src.core import config_migration, global_config_store
from src.core.global_config_store import ZIP_LINE_CONFIG_NAME
from src.tasks.account import account_scope_store


class TestZipLineConfig(unittest.TestCase):
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

        with (
            patch.object(
                global_config_store,
                "get_relative_path",
                side_effect=lambda *parts: os.path.join(*parts),
            ),
            patch.object(global_config_store, "read_json_file", side_effect=fake_read),
        ):
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

        with (
            patch.object(
                global_config_store,
                "get_relative_path",
                side_effect=lambda *parts: os.path.join(*parts),
            ),
            patch.object(global_config_store, "read_json_file", side_effect=fake_read),
        ):
            data_list = [data for data, _ in global_config_store._iter_legacy_zip_line_task_data()]

        self.assertEqual(len(data_list), 3)
        self.assertFalse(
            any("account_scoped_overrides" in path for path in read_paths),
            "不应读取 account_scoped_overrides.json",
        )

    # ---- 端到端回归测试（#165 CodeRabbit review）----

    ZIP_LINE_MIGRATIONS = {
        "通向送货点": "通向武陵城送货点",
        "通向送货点试验园区": "通向试验园区送货点",
    }

    def _write_configs(self, root: str, files: dict):
        """在临时仓库根下写 configs/ 下的 JSON 文件。"""
        configs_dir = os.path.join(root, "configs")
        os.makedirs(configs_dir, exist_ok=True)
        for name, data in files.items():
            write_json_file(os.path.join(configs_dir, name), data)

    def _read_config(self, root: str, name: str):
        return read_json_file(os.path.join(root, "configs", name))

    def test_migrate_config_file_keys_delivery_task_e2e(self):
        """DeliveryTask 端到端：migrate_config_file_keys 把旧键复制到新键，旧键保留（回滚安全）。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "DeliveryTask.json": {
                        "通向送货点": "12,34",
                        "通向送货点试验园区": "56",
                        "目标券数": 119000,
                    },
                },
            )
            with patch.object(
                config_migration,
                "get_relative_path",
                side_effect=lambda *parts: os.path.join(tmp, *parts),
            ):
                config_migration.migrate_config_file_keys("DeliveryTask", self.ZIP_LINE_MIGRATIONS)

            data = self._read_config(tmp, "DeliveryTask.json")
            self.assertEqual(data["通向武陵城送货点"], "12,34")
            self.assertEqual(data["通向试验园区送货点"], "56")
            # 旧键保留，确保回滚安全；无关键不受影响
            self.assertEqual(data["通向送货点"], "12,34")
            self.assertEqual(data["通向送货点试验园区"], "56")
            self.assertEqual(data["目标券数"], 119000)

    def test_migrate_config_file_keys_daily_task_e2e(self):
        """DailyTask 端到端：migrate_config_file_keys 把旧键复制到新键。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "DailyTask.json": {
                        "通向送货点试验园区": "99",
                        "是否启用滚动放大视角": True,
                    },
                },
            )
            with patch.object(
                config_migration,
                "get_relative_path",
                side_effect=lambda *parts: os.path.join(tmp, *parts),
            ):
                config_migration.migrate_config_file_keys("DailyTask", self.ZIP_LINE_MIGRATIONS)

            data = self._read_config(tmp, "DailyTask.json")
            self.assertEqual(data["通向试验园区送货点"], "99")
            self.assertEqual(data["通向送货点试验园区"], "99")
            self.assertEqual(data["是否启用滚动放大视角"], True)

    def test_get_global_config_first_init_full_migration(self):
        """首次调用 get_global_config(ZIP_LINE_CONFIG_NAME) 触发全局迁移 + 账号覆盖迁移 + 迁移标记。"""
        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "DeliveryTask.json": {"通向送货点": "12,34"},
                    "DailyTask.json": {"通向送货点试验园区": "56"},
                    "account_scoped_overrides.json": {
                        "accounts": {
                            "acct-1": {"DailyTask": {"通向送货点试验园区": "999"}},
                        },
                    },
                },
            )
            state_path = os.path.join(tmp, "configs", "_global_config_migrations.json")
            backup_dir = os.path.join(tmp, "configs", "global_config_migration_backup")
            store_path = os.path.join(tmp, "configs", "account_scoped_overrides.json")

            previous_configs = global_config_store._CONFIGS.copy()
            global_config_store._CONFIGS.clear()
            try:
                with (
                    patch.object(
                        global_config_store,
                        "get_relative_path",
                        side_effect=lambda *parts: os.path.join(tmp, *parts),
                    ),
                    patch.object(
                        config_module, "get_relative_path", side_effect=lambda *parts: os.path.join(tmp, *parts)
                    ),
                    patch.object(global_config_store, "_MIGRATION_STATE_PATH", state_path),
                    patch.object(global_config_store, "_MIGRATION_BACKUP_DIR", backup_dir),
                    patch.object(account_scope_store, "_STORE_PATH", store_path),
                ):
                    config = global_config_store.get_global_config(ZIP_LINE_CONFIG_NAME)

                # 全局结果：旧键值复制到新键
                self.assertEqual(config.get("通向武陵城送货点"), "12,34")
                self.assertEqual(config.get("通向试验园区送货点"), "56")
                # 账号覆盖迁移：acct-1 的共享 Zip Line Config 拿到旧键值
                # （update_overrides 会用 account_registry 把 acct-1 解析为内部 id）
                overrides = read_json_file(store_path)
                migrated_accounts = [tasks for tasks in overrides["accounts"].values() if ZIP_LINE_CONFIG_NAME in tasks]
                self.assertEqual(len(migrated_accounts), 1)
                self.assertEqual(migrated_accounts[0][ZIP_LINE_CONFIG_NAME]["通向试验园区送货点"], "999")
                # 迁移标记写入
                state = read_json_file(state_path)
                self.assertTrue(state.get("zip_line_account_overrides_v1"))
                self.assertIn(ZIP_LINE_CONFIG_NAME, state.get("global_config_store_v2_task_scoped", []))
            finally:
                global_config_store._CONFIGS.clear()
                global_config_store._CONFIGS.update(previous_configs)

    def test_task_zip_line_values_survive_task_config_construction(self):
        """任务侧 load_config 顺序：滑索值在框架 verify_config 删键前转存全局。

        任务 default_config 不含滑索键，框架 Config 构造（verify_config）会丢弃并
        写回删除任务文件中不在 default 的键。若先让任务加载，全局侧 legacy 收集
        就再也读不到这些滑索值。即使迁移标记已打（"Zip Line Config" in
        migrated_options），migrate_task_zip_line_values_to_global 的兜底转存
        也应保证滑索值继承到全局 Zip Line Config.json。
        """
        from ok.util.config import Config
        from src.core.global_config_store import (
            ZIP_LINE_SCROLL_KEY,
            migrate_task_zip_line_values_to_global,
        )

        with tempfile.TemporaryDirectory() as tmp:
            self._write_configs(
                tmp,
                {
                    "DeliveryTask.json": {
                        "_enabled": True,
                        "目标券数": ["119000"],
                        "通向送货点": "40",  # 旧键送货滑索值
                        "通向武陵城送货点": "50",  # 新键送货滑索值
                        "常沄": "60",
                    },
                    "DailyTask.json": {
                        "_enabled": True,
                        "枢纽区": "222",  # 淤积点滑索值
                        "武陵城": "45",
                    },
                    "Zip Line Config.json": {
                        ZIP_LINE_SCROLL_KEY: False,
                        "通向武陵城送货点": "",
                        "常沄": "",
                        "枢纽区": "",
                        "武陵城": "",
                    },
                    "account_scoped_overrides.json": {"accounts": {}},
                    "_global_config_migrations.json": {
                        "global_config_store_v2_task_scoped": [
                            "Game Hotkey Config",
                            "Ensure Main Once Action Sleep",
                            "Battle Config",
                            "Zip Line Config",
                        ],
                        "zip_line_account_overrides_v1": True,
                    },
                },
            )

            delivery_default = {
                "_enabled": True,
                "目标券数": ["119000"],
                "地区切换": "武陵城",
                "仅接取": False,
                "仅送货": False,
                "测试对象": "无",
            }
            daily_default = {"_enabled": True, "使用独立配置": False, "⭐刷体力": True}
            state_path = os.path.join(tmp, "configs", "_global_config_migrations.json")
            backup_dir = os.path.join(tmp, "configs", "global_config_migration_backup")
            store_path = os.path.join(tmp, "configs", "account_scoped_overrides.json")

            previous_configs = global_config_store._CONFIGS.copy()
            global_config_store._CONFIGS.clear()
            try:
                with (
                    patch.object(
                        global_config_store,
                        "get_relative_path",
                        side_effect=lambda *parts: os.path.join(tmp, *parts),
                    ),
                    patch.object(
                        config_module, "get_relative_path", side_effect=lambda *parts: os.path.join(tmp, *parts)
                    ),
                    patch.object(global_config_store, "_MIGRATION_STATE_PATH", state_path),
                    patch.object(global_config_store, "_MIGRATION_BACKUP_DIR", backup_dir),
                    patch.object(account_scope_store, "_STORE_PATH", store_path),
                ):
                    # 模拟任务侧 load_config：迁移键名 → 转存全局 → 框架 Config 构造（删滑索键）
                    config_migration.migrate_config_file_keys("DeliveryTask", self.ZIP_LINE_MIGRATIONS)
                    migrate_task_zip_line_values_to_global("DeliveryTask")
                    Config("DeliveryTask", delivery_default)

                    migrate_task_zip_line_values_to_global("DailyTask")
                    Config("DailyTask", daily_default)

                    zlc = global_config_store.get_global_config(ZIP_LINE_CONFIG_NAME)

                # 任务文件滑索键已被框架删除（模拟真实行为）……
                self.assertNotIn("通向武陵城送货点", self._read_config(tmp, "DeliveryTask.json"))
                self.assertNotIn("枢纽区", self._read_config(tmp, "DailyTask.json"))
                # ……但全局已完整继承（迁移标记已打场景下的兜底转存）
                self.assertEqual(zlc.get("通向武陵城送货点"), "50")
                self.assertEqual(zlc.get("常沄"), "60")
                self.assertEqual(zlc.get("枢纽区"), "222")
                self.assertEqual(zlc.get("武陵城"), "45")
                # Zip Line Config.json 文件同样已落盘
                saved = self._read_config(tmp, "Zip Line Config.json")
                self.assertEqual(saved["通向武陵城送货点"], "50")
                self.assertEqual(saved["枢纽区"], "222")
            finally:
                global_config_store._CONFIGS.clear()
                global_config_store._CONFIGS.update(previous_configs)


if __name__ == "__main__":
    unittest.main()
