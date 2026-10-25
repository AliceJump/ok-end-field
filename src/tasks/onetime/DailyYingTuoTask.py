"""一键日常专用的影拓丰碑任务，与独立影拓丰碑任务分别保存配置。"""

from qfluentwidgets import FluentIcon

from src.tasks.onetime.YingTuoTask import YingTuoTask


class DailyYingTuoTask(YingTuoTask):
    """复用影拓丰碑流程，但使用独立的日常配置和账号覆盖段。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常影拓丰碑"
        self.description = "一键日常专用，配置独立于「影拓丰碑」。"
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.support_multi_account = True
