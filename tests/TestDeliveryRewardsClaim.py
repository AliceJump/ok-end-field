# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.daily.misc.daily_logistics_mixin import DailyLogisticsMixin


class TestClaimDeliveryRewardsStatus(unittest.TestCase):
    """_claim_delivery_rewards_in_current_node 的显式返回状态（线程5）。"""

    def make_claim_feature(self, node_found=True, results=None, pop_up=True):
        feature = object.__new__(DailyLogisticsMixin)
        feature.log_info = Mock()
        feature.wait_click_ocr = Mock(return_value=node_found)
        feature.wait_ocr = Mock(return_value=results or [])
        feature.click = Mock()
        feature.wait_pop_up = Mock(return_value=pop_up)
        feature.box = SimpleNamespace(top_left=object(), bottom_right=object())
        feature.lang = SimpleNamespace(
            daily_routine_mixin=SimpleNamespace(
                k_41a9fd98="我转交的委托",
                k_bf856c96="确认",
            )
        )
        return feature

    def test_claim_missing_node_returns_false(self):
        feature = self.make_claim_feature(node_found=False)
        self.assertFalse(feature._claim_delivery_rewards_in_current_node())

    def test_claim_no_results_returns_true(self):
        feature = self.make_claim_feature(node_found=True, results=[])
        self.assertTrue(feature._claim_delivery_rewards_in_current_node())
        feature.click.assert_not_called()

    def test_claim_confirm_failure_returns_false(self):
        feature = self.make_claim_feature(
            node_found=True, results=[SimpleNamespace(name="奖励")], pop_up=False
        )
        self.assertFalse(feature._claim_delivery_rewards_in_current_node())

    def test_claim_success_returns_true(self):
        feature = self.make_claim_feature(
            node_found=True, results=[SimpleNamespace(name="奖励")], pop_up=True
        )
        self.assertTrue(feature._claim_delivery_rewards_in_current_node())


class TestDeliverySendOthersClaimRetry(unittest.TestCase):
    """delivery_send_others 仅在领取成功时置位 claim_rewards_done（线程5）。

    第一个地区领取失败时，后续地区应重试领取，而不是跳过。
    """

    def make_send_feature(self, claim_results):
        feature = object.__new__(DailyLogisticsMixin)
        feature.info_set = Mock()
        feature.log_info = Mock()
        feature.mark_task_failure = Mock()
        feature.to_model_area = Mock(return_value=True)
        feature.ensure_main = Mock()
        feature.press_key = Mock()
        feature.click = Mock()
        feature.click_confirm = Mock(return_value=True)
        feature.blind_spot_speed_up = Mock(return_value=True)
        feature.wait_click_feature = Mock(return_value=True)
        feature.box_of_screen = Mock(return_value=object())
        feature.box = SimpleNamespace(
            top_left=object(), bottom=object(), bottom_left=object()
        )
        feature.lang = SimpleNamespace(
            daily_routine_mixin=SimpleNamespace(
                k_view_quote="查看报价",
                k_298d3284="本地仓储节点",
                k_573c7c18="货物A",
                k_8f2058a8="货物B",
                k_1dd73947="转交运送委托",
            )
        )
        # 领取奖励按序列返回（覆盖 _claim_delivery_rewards_in_current_node）
        feature._claim_delivery_rewards_in_current_node = Mock(
            side_effect=claim_results
        )
        # 找本地仓储节点与「转交运送委托」按钮均返回 True
        feature.wait_click_ocr = Mock(return_value=True)
        # 找货物 → 返回一项，让每个地区转交一次后退出
        feature.wait_ocr = Mock(return_value=[SimpleNamespace(name="货物A")])
        return feature

    def run_send_others(self, feature):
        with patch(
            "src.tasks.daily.misc.daily_logistics_mixin.areas_list",
            ["武陵", "试验园区"],
        ):
            return feature.delivery_send_others()

    def test_first_area_fails_rewards_retried_in_second_area(self):
        feature = self.make_send_feature([False, True])
        result = self.run_send_others(feature)

        self.assertTrue(result)
        # 第一个地区领取失败(False)，第二个地区重试(True) → 领取被调用两次
        self.assertEqual(
            feature._claim_delivery_rewards_in_current_node.call_count, 2
        )

    def test_first_area_success_skips_second_area_claim(self):
        feature = self.make_send_feature([True])
        result = self.run_send_others(feature)

        self.assertTrue(result)
        # 第一个地区领取成功，第二个地区不再重复领取
        self.assertEqual(
            feature._claim_delivery_rewards_in_current_node.call_count, 1
        )

    def test_all_areas_fail_keeps_retrying(self):
        feature = self.make_send_feature([False, False])
        result = self.run_send_others(feature)

        self.assertTrue(result)
        # 两个地区都失败 → 每个地区都尝试领取一次
        self.assertEqual(
            feature._claim_delivery_rewards_in_current_node.call_count, 2
        )


if __name__ == "__main__":
    unittest.main()
