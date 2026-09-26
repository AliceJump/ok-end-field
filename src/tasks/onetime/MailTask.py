from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.tasks.daily.misc.daily_logistics_mixin import DailyLogisticsMixin
from src.tasks.mixin.common import Common


class MailTask(Common, DailyLogisticsMixin):
    """收邮件子任务：前往「邮箱」领取全部邮件附件。

    可独立运行；也可由 ``DailyFeature`` 包装后接入日常任务（日常不继承本类）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "收邮件"
        self.icon = Icons.Collect
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往「邮箱」领取全部邮件附件。"

    def run(self):
        self.ensure_main(time_out=420)
        self.run_mail()

    def run_mail(self):
        """收邮件流程，独立运行与被日常执行（DailyFeature）共用。"""
        return self.claim_mail()
