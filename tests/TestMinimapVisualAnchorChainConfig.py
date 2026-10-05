"""关键帧视觉锚定链校正配置改名与迁移测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core.config_migration import (
    copy_migrated_config_keys,
    migrate_account_task_config_keys,
)
from src.localization.minimap_position_mixin import MinimapPositionMixin


class TestMinimapVisualAnchorChainConfig(unittest.TestCase):
    def test_legacy_keys_copy_to_canonical_names(self):
        config = {
            "在线位姿图": False,
            "在线位姿图窗口(拍)": 42,
            "在线位姿图关键帧锚点权重": 17.5,
        }

        modified = copy_migrated_config_keys(
            config,
            MinimapPositionMixin.config_key_migrations,
        )

        self.assertTrue(modified)
        self.assertFalse(config["关键帧视觉锚定链校正"])
        self.assertEqual(config["关键帧视觉锚定链校正窗口(拍)"], 42)
        self.assertEqual(config["关键帧视觉锚定链校正关键帧锚点权重"], 17.5)

    def test_existing_canonical_value_wins(self):
        config = {
            "在线位姿图": False,
            "关键帧视觉锚定链校正": True,
        }

        copy_migrated_config_keys(
            config,
            MinimapPositionMixin.config_key_migrations,
        )

        self.assertTrue(config["关键帧视觉锚定链校正"])

    def test_missing_data_and_rerun_are_stable(self):
        config = {}
        modified = copy_migrated_config_keys(
            config,
            MinimapPositionMixin.config_key_migrations,
        )
        self.assertFalse(modified)

        legacy_only = {"在线位姿图": True}
        self.assertTrue(
            copy_migrated_config_keys(
                legacy_only,
                MinimapPositionMixin.config_key_migrations,
            )
        )
        snapshot = dict(legacy_only)
        self.assertFalse(
            copy_migrated_config_keys(
                legacy_only,
                MinimapPositionMixin.config_key_migrations,
            )
        )
        self.assertEqual(legacy_only, snapshot)

    @patch("src.tasks.account.account_scope_store.update_overrides")
    def test_account_overrides_follow_the_same_migration(self, update_overrides):
        migrate_account_task_config_keys(
            "MinimapPositionTask",
            MinimapPositionMixin.config_key_migrations,
        )
        updater = update_overrides.call_args.args[0]
        data = {
            "accounts": {
                "account-1": {
                    "MinimapPositionTask": {
                        "在线位姿图": False,
                        "在线位姿图窗口(拍)": 30,
                    },
                    "OtherTask": {
                        "在线位姿图": "untouched",
                    },
                }
            }
        }

        migrated = updater(data)

        overrides = migrated["accounts"]["account-1"]["MinimapPositionTask"]
        self.assertFalse(overrides["关键帧视觉锚定链校正"])
        self.assertEqual(overrides["关键帧视觉锚定链校正窗口(拍)"], 30)
        self.assertEqual(
            migrated["accounts"]["account-1"]["OtherTask"]["在线位姿图"],
            "untouched",
        )


if __name__ == "__main__":
    unittest.main()
