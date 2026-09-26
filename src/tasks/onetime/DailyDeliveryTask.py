"""一键日常专用的自动送货任务，与独立送货任务分别保存配置。"""

from qfluentwidgets import FluentIcon

from src.tasks.onetime.DeliveryTask import DeliveryTask


class DailyDeliveryTask(DeliveryTask):
    """复用自动送货流程，但使用独立的日常配置和账号覆盖段。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常自动送货"
        self.description = "一键日常专用，配置独立于「自动送货」。"
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
