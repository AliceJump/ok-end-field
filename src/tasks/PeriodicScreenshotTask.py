"""每隔 N 秒自动截图并保存到 screenshots 目录。"""
from datetime import datetime
from pathlib import Path

import cv2
from qfluentwidgets import FluentIcon
from ok import Logger
from src.tasks.BaseEfTask import BaseEfTask
from src.config import make_bottom_left_black

logger = Logger.get_logger(__name__)


class PeriodicScreenshotTask(BaseEfTask):
    """每隔固定秒数对游戏窗口截图并保存，点击启动后持续运行直到手动停止。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "定时截图"
        self.group_name = "工具与调试"
        self.group_icon = FluentIcon.CAMERA
        self.description = "每隔指定秒数截图保存，用于数据采集 / YOLO 样本收集"
        self.icon = FluentIcon.CAMERA
        self.default_config = {
            '间隔秒数': 5,
            '保存目录': 'screenshots/periodic',
        }
        self.config_description = {
            '间隔秒数': '每次截图的间隔时间（秒），最小 1 秒',
            '保存目录': '截图保存路径（相对于项目根目录）',
        }

    def run(self):
        interval = max(1.0, float(self.config.get('间隔秒数', 5)))
        save_dir = Path(self.config.get('保存目录', 'screenshots/periodic'))
        save_dir.mkdir(parents=True, exist_ok=True)

        self.log_info(f"开始定时截图，间隔 {interval} 秒，保存至 {save_dir}")
        count = 0

        while True:
            frame = self.next_frame()
            if frame is not None:
                frame = make_bottom_left_black(frame)
                filename = save_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_cap_{count:04d}.png"
                cv2.imwrite(str(filename), frame)
                count += 1
                if count == 1 or count % 10 == 0:
                    self.log_info(f"[{count}] 截图已保存: {filename}")
                else:
                    self.log_debug(f"[{count}] 截图已保存: {filename}")

            self.sleep(interval)
