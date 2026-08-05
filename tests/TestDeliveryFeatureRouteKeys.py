# -*- coding: utf-8 -*-
"""DeliveryFeature 滑索路线键注册回归测试（#163 Major1）。

覆盖：
- task.config 为 None（DailyTask.__init__ 阶段）时 _register_route_keys 不崩溃，只注册 default_config；
- 运行时（run_daily）config 已填充路线键与滚动开关，且不覆盖用户已有键。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.data.delivery_area import DEFAULT_DELIVERY_AREA
from src.tasks.onetime.DeliveryTask import DeliveryFeature, DeliveryTask


class TestDeliveryFeatureRouteKeys(unittest.TestCase):
    ROUTE_KEY = "通向武陵城送货点"
    END_KEY = "武陵"

    def make_feature(self, task):
        feature = object.__new__(DeliveryFeature)
        feature._task = task
        feature.to_delivery_point_config_keys = [self.ROUTE_KEY]
        feature.ends = [self.END_KEY]
        return feature

    def test_register_route_keys_skips_runtime_config_when_none(self):
        """初始化阶段 task.config 为 None 时不崩溃，仅注册 default_config。"""
        task = SimpleNamespace(default_config={}, config=None)
        feature = self.make_feature(task)
        feature._register_route_keys()
        self.assertIn(self.ROUTE_KEY, task.default_config)
        self.assertIn(self.END_KEY, task.default_config)
        self.assertIn(DeliveryTask.CFG_SCROLL_ENABLE, task.default_config)
        self.assertIs(task.default_config[DeliveryTask.CFG_SCROLL_ENABLE], False)
        self.assertEqual(task.default_config[self.ROUTE_KEY], "")

    def test_register_route_keys_fills_runtime_config(self):
        """运行时 config 已就绪时补齐路线键与滚动开关，且不覆盖用户已有键。"""
        task = SimpleNamespace(default_config={}, config={"已有键": 1})
        feature = self.make_feature(task)
        feature._register_route_keys()
        self.assertEqual(task.config[self.ROUTE_KEY], "")
        self.assertEqual(task.config[self.END_KEY], "")
        self.assertIs(task.config[DeliveryTask.CFG_SCROLL_ENABLE], False)
        self.assertEqual(task.config["已有键"], 1)

    def test_run_daily_registers_route_keys(self):
        """run_daily 无条件注册路线键（即使未切换地区）。"""
        task = SimpleNamespace(default_config={}, config={})
        feature = self.make_feature(task)
        feature.delivery_area = DEFAULT_DELIVERY_AREA
        feature._configure_delivery_area = Mock()
        feature._run_single_delivery_cycle = Mock()
        feature._daily_delivery_mode = False
        self.assertTrue(feature.run_daily())
        self.assertIn(self.ROUTE_KEY, task.config)
        self.assertIn(self.END_KEY, task.config)
        self.assertIs(task.config[DeliveryTask.CFG_SCROLL_ENABLE], False)
        feature._run_single_delivery_cycle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
