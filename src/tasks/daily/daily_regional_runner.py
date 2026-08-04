from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list


class DailyRegionalRunner:
    """按地区编排地区建设相关日常操作。"""

    OPTIONS = ["据点兑换", "买物资", "买卖货"]
    DEFAULT_OPTIONS = ["据点兑换", "买卖货"]

    def __init__(self, task):
        self._task = task

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
                self._task.daily_routine.exchange_outpost_goods(
                    target_areas=[area],
                    keep_area_context=True,
                )

            materials_after_trade_buy = None
            if enabled_buy and enabled_trade:
                materials_after_trade_buy = lambda current_area: self._task.daily_buy.buy_staple_goods(
                    target_areas=[current_area],
                    keep_area_context=True,
                )

            if enabled_trade:
                self._task.daily_trade.buy_sell(
                    target_areas=[area],
                    keep_area_context=True,
                    after_buy=materials_after_trade_buy,
                )
            elif enabled_buy:
                self._task.daily_buy.buy_staple_goods(
                    target_areas=[area],
                    keep_area_context=True,
                )

            self.safe_back(feature=fL.transaction_overview, once_time_out=3)
            self.log_info(f"完成地区建设: {area}")

        return True
