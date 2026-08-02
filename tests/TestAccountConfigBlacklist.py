# -*- coding: utf-8 -*-
import unittest

from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.DeliveryTask import DeliveryTask


class TestAccountConfigBlacklist(unittest.TestCase):
    def test_tasks_hide_only_non_account_execution_controls(self):
        self.assertTrue({
            "发生异常时终止游戏",
            "仅退出游戏",
            "自动打开汇总文件",
            "Exit After Task",
            "重复测试的次数",
        }.issubset(DailyTask.account_config_blacklist))
        self.assertTrue({
            "选择测试对象",
            "仅接取",
            "仅送货",
            "完整循环测试区域",
            "发生异常时终止游戏",
            "Exit After Task",
        }.issubset(DeliveryTask.account_config_blacklist))

        self.assertNotIn("配置选择", DailyTask.account_config_blacklist)
        self.assertNotIn("⭐执行外部命令", DailyTask.account_config_blacklist)
        self.assertNotIn("外部命令", DailyTask.account_config_blacklist)
        self.assertNotIn("通向武陵城送货点", DeliveryTask.account_config_blacklist)


if __name__ == "__main__":
    unittest.main()
