import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask


def _make_runner(
    config_options,
    buy_sell_result=True,
    went_friend_boat_result=False,
    after_buy_called=True,
    exchange_result=True,
    buy_staple_result=True,
    to_model_area_result=True,
):
    """构造一个带 mock 的 RegionalBuildTask 实例，用于地区建设分支测试。

    after_buy_called: buy_sell 是否真的触发 after_buy 回调
                      （为 False 时模拟地区未启用/未找到货物/缺买卖价跳过的场景）
    went_friend_boat_result: buy_sell 是否报告「已前往好友帝江号」。
    """
    runner = object.__new__(RegionalBuildTask)
    runner._buy_ran = False
    runner.config = SimpleNamespace(get=lambda key, default=None: config_options)
    runner.exchange_outpost_goods = Mock(return_value=exchange_result)
    runner.buy_staple_goods = Mock(return_value=buy_staple_result)
    runner.to_model_area = Mock(return_value=to_model_area_result)
    runner.safe_back = Mock()
    runner.ensure_main = Mock()
    runner.log_info = Mock()
    runner.tr = lambda message, **kwargs: message

    def buy_sell_impl(target_areas=None, keep_area_context=False, after_buy=None):
        if after_buy is not None and after_buy_called and target_areas:
            after_buy(target_areas[0])
        return buy_sell_result, went_friend_boat_result

    runner.buy_sell = Mock(side_effect=buy_sell_impl)
    return runner


class TestDailyRegionalRunner(unittest.TestCase):
    AREAS = ["武陵", "试验园区"]

    def run_runner(self, runner):
        with patch("src.tasks.onetime.RegionalBuildTask.areas_list", self.AREAS):
            return runner.run_regional()

    # ── 仅「买物资」──
    def test_buy_only_navigates_to_material_dispatch_before_buy(self):
        runner = _make_runner(["买物资"])
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 必须先进入物资调度再购买
        self.assertEqual(
            runner.to_model_area.call_args_list[0].args,
            ("武陵", "物资调度"),
        )
        # 每个地区都执行了购买
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        # 仅买物资时不应触发买卖货
        runner.buy_sell.assert_not_called()

    # ── 买物资 + 买卖货，回调正常触发 ──
    def test_buy_with_trade_uses_after_buy_callback(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # buy_sell 的 after_buy 回调触发了 buy_staple_goods
        self.assertEqual(runner.buy_sell.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        # 回调已执行，不重复补充
        runner.to_model_area.assert_not_called()

    # ── 买物资 + 买卖货，回调被跳过 → 补充执行 ──
    def test_buy_retried_when_after_buy_callback_skipped(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 回调被跳过，应补充进入物资调度执行买物资
        self.assertEqual(runner.buy_sell.call_count, 2)
        self.assertEqual(runner.to_model_area.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)

    # ── 买卖货失败 → 记录日志并继续下一地区 ──
    def test_trade_failure_logs_and_continues(self):
        runner = _make_runner(["买卖货"], buy_sell_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买卖货失败", logged)

    # ── 已前往好友帝江号 → 直接回主界面、跳过 safe_back、继续下一区域 ──
    def test_trade_went_friend_boat_ensure_main_skips_safe_back_and_continues(self):
        runner = _make_runner(["买卖货"], went_friend_boat_result=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 每个区域前往好友船后都直接 ensure_main，不再走 safe_back 逐层返回
        self.assertEqual(runner.ensure_main.call_count, 2)
        runner.safe_back.assert_not_called()
        # 继续处理下一区域
        self.assertEqual(runner.buy_sell.call_count, 2)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("已前往好友帝江号", logged)

    # ── 未前往好友船时仍走原 safe_back 返回路径，不调用 ensure_main ──
    def test_trade_not_went_friend_boat_keeps_safe_back_path(self):
        runner = _make_runner(["买卖货"], went_friend_boat_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        runner.ensure_main.assert_not_called()
        self.assertEqual(runner.safe_back.call_count, 2)

    # ── 据点兑换失败 → 记录日志并继续下一地区 ──
    def test_outpost_failure_logs_and_continues(self):
        runner = _make_runner(["据点兑换"], exchange_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("据点兑换失败", logged)

    # ── 仅买物资时无法进入物资调度 → 记录日志并继续 ──
    def test_buy_only_to_model_area_failure_logs_and_continues(self):
        runner = _make_runner(["买物资"], to_model_area_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("无法进入", logged)

    # ── 仅买物资时购买失败 → 记录日志并继续 ──
    def test_buy_only_buy_staple_failure_logs_and_continues(self):
        runner = _make_runner(["买物资"], buy_staple_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买物资失败", logged)

    # ── 买物资 + 买卖货，回调被跳过且补充购买失败 → 记录日志并继续 ──
    def test_fallback_buy_failure_logs_and_continues(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=False, buy_staple_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买物资失败", logged)

    # ── 买物资 + 买卖货 + 据点兑换：回调正常，不重复补充 ──
    def test_all_options_callback_not_duplicated(self):
        runner = _make_runner(["据点兑换", "买物资", "买卖货"], after_buy_called=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        self.assertEqual(runner.exchange_outpost_goods.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        runner.to_model_area.assert_not_called()


if __name__ == "__main__":
    unittest.main()
