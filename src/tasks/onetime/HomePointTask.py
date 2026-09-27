from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class HomePointTask(Common, LiaisonMixin):
    """传送到帝江号右侧传送点子任务：日常任务收尾传送，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "传送到帝江号右侧传送点"
        self.icon = Icons.Navigation
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "传送到「帝江号」右侧传送点，作为日常任务的收尾。"

    def run(self):
        self.ensure_main(time_out=420)
        self.run_home_point()

    def run_home_point(self):
        """收尾传送流程，独立运行与被日常执行（DailyFeature）共用。"""
        return self.transfer_to_home_point(box=self.box.right)
