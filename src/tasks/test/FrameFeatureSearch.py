"""每隔 N 秒自动截图并保存到 screenshots 目录。"""
from datetime import datetime
from pathlib import Path

import cv2
from qfluentwidgets import FluentIcon
from ok import Logger
from src.core.BaseEfTask import BaseEfTask
from src.config import make_bottom_left_black
from src.data.FeatureList import FeatureList as fL

logger = Logger.get_logger(__name__)


class FrameFeatureSearch(BaseEfTask):
    """每隔固定秒数对游戏窗口截图并保存，点击启动后持续运行直到手动停止。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "帧特征搜索"
        self.group_name = "工具与调试"
        self.description = "每隔指定秒数截图保存，用于数据采集 / YOLO 样本收集"
        self.icon = FluentIcon.SEARCH
        self.visible = self.debug
        self.default_config = {
            '间隔秒数': 0.5,
            "检测特征": fL.terminal_enter
        }
        self.config_description = {
            '间隔秒数': '每次截图的间隔时间（秒），最小 0.1 秒',
        }
        self.config_type = {
            "检测特征" : {
                "type": "drop_down",
                "options": [feature for feature in fL],
            }
        }

    def run(self):
        interval = max(0.1, float(self.config.get('间隔秒数', 0.5)))
        while True:
            self.sleep(interval)
            result =  self.find_one(feature=self.config.get("检测特征"))
            if result:
                self.log_info(f"检测到特征{result.name}, 置信度: {result.confidence:.2f}")
            else:
                self.log_info(f"未检测到特征{self.config.get('检测特征')}")
