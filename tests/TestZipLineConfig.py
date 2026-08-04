# -*- coding: utf-8 -*-
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


if __name__ == "__main__":
    unittest.main()
