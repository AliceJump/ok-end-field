from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.tasks.daily.daily_credit_mixin import DailyCreditMixin
from src.tasks.mixin.common import Common


class CreditCollectTask(Common, DailyCreditMixin):
    """收信用子任务：前往好友的「帝江号」交流与助力，并收取信用。

    可独立运行；也可由 ``DailyFeature`` 包装后接入日常任务（日常不继承本类）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "收信用"
        self.icon = Icons.Interact
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往好友的「帝江号」进行交流与助力，并前往「采购中心/信用交易所」收取信用。"
        # 账号配置页收集标记：本任务参数可按账号覆盖（多账户独立配置）
        self.support_multi_account = True
        self.default_config.update(
            {
                "尝试仅收培育室": True,
            }
        )
        self.config_description.update(
            {
                "尝试仅收培育室": (
                    "若选项开启，则优先尝试仅助力好友「帝江号」上的「培养仓」。\n如果不能，至少助力一次其它舱室。"
                ),
            }
        )

    def run(self):
        self.ensure_main(time_out=420)
        self.run_credit_collect()

    def run_credit_collect(self):
        """收信用流程，独立运行与被日常执行（DailyFeature）共用。"""
        self.collect_credit()
