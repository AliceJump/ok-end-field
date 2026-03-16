from qfluentwidgets import FluentIcon

from src.data.world_map import areas_list, stages_list, stages_dict
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.tasks.daily.daily_routine_mixin import DailyRoutineMixin
from src.tasks.daily.daily_shop_mixin import DailyShopMixin
from src.tasks.daily.daily_trade_mixin import DailyTradeMixin
from src.tasks.mixin.common import Common


class DailyTask(
    DailyLiaisonMixin,
    DailyTradeMixin,
    DailyRoutineMixin,
    DailyShopMixin,
    DailyBattleMixin
):
    """日常任务聚合执行器。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "一键收菜\n反复按esc请前往设置调整主界面单次动作后延迟，建议1.5秒以上"
        self.icon = FluentIcon.SYNC
        self.support_schedule_task = True
        buy_sell = dict()
        for area in areas_list:
            buy_sell[f"{area}买入价"] = 900
            buy_sell[f"{area}卖出价"] = 4500
            buy_sell[area] = True
        self.stages_list = stages_list
        self.default_config.update(buy_sell)
        self.default_config.update({"优先送礼对象": list(self.can_contact_dict.keys())[0]})

        self.default_config.update(
            {
                "送礼任务最多尝试次数": 2,
                "送礼": True,
                "据点兑换": True,
                "转交运送委托": True,
                "转交委托奖励领取": True,
                "造装备": True,
                "收信用": True,
                "尝试仅收培育室": False,
                "收集线索": True,
                "制造舱": True,
                "买信用商店": False,
                "买卖货": True,
                "刷体力": True,
                "日常奖励": True,
            }
        )
        self.config_type["体力本"] = {"type": "drop_down", "options": self.stages_list}
        self.config_type["优先送礼对象"] = {"type": "drop_down", "options": list(self.can_contact_dict.keys())}
        self.config_description.update({"尝试仅收培育室": '前置是启用收信用'})
        self.add_exit_after_config()
        self.config_description.update({
            "尝试仅收培育室": "在好友交流助力时，优先尝试仅收取培育室的助力,但每次至少助力一次舱室",
        })
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def run(self):
        """日常任务主入口。"""
        self.log_info("开始执行日常任务...", notify=True)
        repeat_times = 1
        if self.debug:
            repeat_times = self.config.get("重复测试的次数", 1)
            self.log_info(f"当前为调试模式，重复执行 {repeat_times} 次")
        if not self._logged_in:
            self.ensure_main(time_out=240)
        else:
            self.ensure_main()
        tasks = [  # 确保在主界面
            ("送礼", self.execute_gift_task),
            ("据点兑换", self.exchange_outpost_goods),
            ("转交运送委托", self.delivery_send_others),
            ("转交委托奖励领取", self.claim_delivery_rewards),
            ("造装备", self.make_weapon),
            ("收信用", self.collect_credit),
            ("收集线索", self.collect_clue),
            ("制造舱", self.up_make_room_num),
            ("买信用商店", self.credit_shop),
            ("买卖货", self.buy_sell),
            ("刷体力", self.battle),
            ("日常奖励", self.claim_daily_rewards),
        ]
        all_fail_tasks = []
        if self.debug:
            for repeat_idx in range(repeat_times):
                self.log_info(f"开始第 {repeat_idx + 1}/{repeat_times} 轮任务执行")
                failed_tasks = []
                for key, func in tasks:
                    if not self.execute_task(key, func):
                        failed_tasks.append(key)
                if failed_tasks:
                    all_fail_tasks.append((repeat_idx + 1, failed_tasks))
                    self.log_info(f"第 {repeat_idx + 1} 轮 | 失败任务: {failed_tasks}", notify=True)
                else:
                    self.log_info(f"第 {repeat_idx + 1} 轮 | 日常完成!", notify=True)
            if all_fail_tasks:
                self.log_info(f"重复测试完成，失败统计: {all_fail_tasks}", notify=True)
            else:
                self.log_info("所有重复测试均成功完成!", notify=True)
        else:
            failed_tasks = []
            for key, func in tasks:
                if not self.execute_task(key, func):
                    failed_tasks.append(key)
            if failed_tasks:
                self.log_info(f"以下任务未完成或失败: {failed_tasks}", notify=True)
            else:
                self.log_info("日常完成!", notify=True)

    def execute_task(self, key, func):
        """统一执行单个子任务。"""
        if isinstance(key, str):
            if not self.config.get(key, False):
                return True

        self.log_info(f"开始任务: {key}")
        self.ensure_main()
        result = func()

        if result is False:
            self.log_info(f"任务 {key} 执行失败", notify=True)
            return False
        return True
