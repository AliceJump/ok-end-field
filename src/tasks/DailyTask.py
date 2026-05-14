from qfluentwidgets import FluentIcon

from src.tasks.account.account_mixin import AccountMixin
from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
from src.tasks.daily.daily_buy_mixin import DailyBuyMixin
from src.tasks.daily.daily_liaison_mixin import DailyLiaisonMixin
from src.tasks.daily.daily_routine_mixin import DailyRoutineMixin
from src.tasks.daily.daily_shop_mixin import DailyShopMixin
from src.tasks.daily.daily_trade_mixin import DailyTradeMixin
from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.mixin.end_command_mixin import EndCommandMixin

class DailyTask(
    DailyBuyMixin,  # 买物资
    DailyBattleMixin,  # 刷体力
    DailyTradeMixin,  # 买卖货
    DailyShopMixin,  # 买信用商店
    DailyRoutineMixin,  # 其它
    DailyLiaisonMixin,  # 送礼
    EndCommandMixin,
    AccountMixin
):
    """日常任务聚合执行器。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "子任务开关用⭐标出，自上而下顺序执行，最后执行『日常奖励』。\n如果出现反复按ESC的情形，请调高『设置/主界面单次动作后延迟』（建议1.5以上）。"
        self.icon = FluentIcon.SYNC
        self.support_schedule_task = True
        self.support_multi_account = True
        self.config_description.update(
            {
                "仅退出游戏": "是否在完成所有任务后仅退出游戏，开启后会自动关闭游戏进程,但不关闭软件\n开启发生异常时终止游戏时此选项不生效",
                "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
            }
        )
        self.add_end_command_config(
            enable_description="是否在日常任务末尾执行一次外部命令行程序。",
            command_description=(
                "需要执行的命令行内容。\n"
                "建议：优先绝对路径；路径或参数含空格时按系统 shell 规则加引号。\n"
                "开启『结尾外部命令等待退出』可支持多账户模式。\n"
                "可选填写『结尾外部命令起始于』作为命令工作目录。"
            ),
        )
        self.default_config.update({"⭐传送到帝江号右侧传送点": True, "发生异常时终止游戏": False, "仅退出游戏": False})
        self.add_exit_after_config()
        if self.debug:
            self.default_config.update({"重复测试的次数": 1})

    def build_task_plan(self):
        return [
            ("⭐送礼", self.execute_gift_task),
            ("⭐帝江号一键存放", self.boat_one_key_store),
            ("⭐收邮件", self.claim_mail),
            ("⭐据点兑换", self.exchange_outpost_goods),
            ("⭐转交运送委托", self.delivery_send_others),
            ("⭐转交委托奖励领取", self.claim_delivery_rewards),
            ("⭐造装备", self.make_weapon),
            ("⭐简易制作", self.make_simply),
            ("⭐收信用", self.collect_credit),
            ("⭐帝江号收菜", self.boat_claim_rewards),
            ("⭐买信用商店", self.credit_shop),
            ("⭐买卖货", self.buy_sell),
            ("⭐刷体力", self.battle),
            ("⭐买物资", self.buy_staple_goods),
            ("⭐活动奖励", self.claim_activity_rewards),
            ("⭐日常奖励", self.claim_daily_rewards),
            ("⭐传送到帝江号右侧传送点", lambda: self.transfer_to_home_point(box=self.box.right)),
            ("⭐执行结尾外部命令", self.launch_end_command_non_blocking),
        ]

    def run(self):
        """日常任务主入口。"""
        repeat_times = self.config.get("重复测试的次数", 1) if self.debug else 1
        DailyTaskRunner(self, self.build_task_plan()).run(repeat_times=repeat_times)
