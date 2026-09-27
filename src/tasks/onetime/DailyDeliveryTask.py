"""一键日常专用的自动送货任务，与独立送货任务分别保存配置。"""

import shutil
from pathlib import Path

from qfluentwidgets import FluentIcon

from src.core.paths import config_path
from src.tasks.onetime.DeliveryTask import DeliveryTask


class DailyDeliveryTask(DeliveryTask):
    """只保留日常接单参数，复用自动送货的完整执行流程。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常自动送货"
        self.description = "一键日常专用，配置独立于「自动送货」。"
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR

        # 日常任务负责账号循环与完成后退出；测试入口、仅接取/仅送货等
        # 独立任务选项不应出现在日常专属卡片，也不参与此任务的运行。
        daily_keys = {"_enabled", self.CFG_TARGET_TICKET_NUM, self.CFG_DELIVERY_AREA}
        self.default_config = {key: value for key, value in self.default_config.items() if key in daily_keys}
        self.config_description = {
            key: value for key, value in self.config_description.items() if key in daily_keys
        }
        self.config_type = {key: value for key, value in self.config_type.items() if key in daily_keys}

    def load_config(self):
        """清理旧日常调试选项前备份配置，保留用户此前保存的原值。"""
        config_file = Path(config_path("DailyDeliveryTask.json"))
        backup_file = Path(config_path("daily_delivery_simplify_backup", "DailyDeliveryTask.json"))
        if config_file.is_file() and not backup_file.exists():
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_file, backup_file)
        super().load_config()
