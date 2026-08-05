from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list


class DailyRegionalRunner:
    """按地区编排地区建设相关日常操作。"""

    OPTIONS = ["据点兑换", "买物资", "买卖货"]
    DEFAULT_OPTIONS = ["据点兑换", "买卖货"]

    def __init__(self, task):
        self._task = task
        # 标记买卖货流程中「买物资」回调是否真的被触发（buy_sell 可能跳过回调）
        self._buy_ran = False

    def __getattr__(self, name):
        return getattr(self._task, name)

    def run(self):
        selected = set(self.config.get("⭐地区建设", self.DEFAULT_OPTIONS) or [])
        enabled_outpost = "据点兑换" in selected
        enabled_buy = "买物资" in selected
        enabled_trade = "买卖货" in selected

        for area in areas_list:
            self.log_info(f"开始处理地区建设: {area}")
            if enabled_outpost:
                if not self._task.daily_routine.exchange_outpost_goods(
                    target_areas=[area],
                    keep_area_context=True,
                ):
                    self.log_info(f"据点兑换失败: {area}")

            if enabled_trade:
                # 买卖货：买入后通过 after_buy 回调执行「买物资」。
                # buy_sell 在地区未启用/未找到货物/缺少买卖价时会跳过回调，
                # 因此用 _buy_ran 标记，回调未执行时单独补一次买物资。
                # 任一步骤失败均只记录日志，继续处理下一地区。
                self._buy_ran = False
                trade_ok = self._task.daily_trade.buy_sell(
                    target_areas=[area],
                    keep_area_context=True,
                    after_buy=self._buy_staple_after_trade if enabled_buy else None,
                )
                if not trade_ok:
                    self.log_info(f"买卖货失败: {area}")
                if enabled_buy and not self._buy_ran:
                    self.log_info("买卖货未执行买物资回调，单独执行买物资")
                    self._buy_staple_in_area(area)
            elif enabled_buy:
                # 仅买物资：先进入物资调度，再执行购买。
                self._buy_staple_in_area(area)

            self.safe_back(feature=fL.transaction_overview, once_time_out=3)
            self.log_info(f"完成地区建设: {area}")

        return True

    def _buy_staple_after_trade(self, current_area):
        """买卖货买入后的「买物资」回调。"""
        self._buy_ran = True
        return self._task.daily_buy.buy_staple_goods(
            target_areas=[current_area],
            keep_area_context=True,
        )

    def _buy_staple_in_area(self, area):
        """进入物资调度后执行「买物资」。"""
        if not self._task.to_model_area(area, "物资调度"):
            self.log_info(f"无法进入{area}物资调度，跳过买物资")
            return False
        if not self._task.daily_buy.buy_staple_goods(
            target_areas=[area],
            keep_area_context=True,
        ):
            self.log_info(f"买物资失败: {area}")
        return True
