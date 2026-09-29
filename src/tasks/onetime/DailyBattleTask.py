"""一键日常专用的刷体力任务，与独立刷体力任务分别保存配置。"""

from qfluentwidgets import FluentIcon

from src.tasks.onetime.BattleTask import BattleTask


class DailyBattleTask(BattleTask):
    """复用刷体力流程，但使用独立的日常配置和账号覆盖段。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常刷体力"
        self.description = "一键日常专用，配置独立于「刷体力」。"
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.support_multi_account = True
