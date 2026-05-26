import time
import re
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.tasks.BaseEfTask import BaseEfTask


class Test(BaseEfTask):
    """
    简单箭头角度读取测试
    直接调用 get_arrow_angle() 并持续输出当前角度
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "箭头角度实时读取"
        self.description = "持续读取并显示当前箭头的角度"

        self.interval = 0.3  # 读取间隔（秒）

    def run(self):
        temp=self.runtime_locale
        self.log_info(f"当前运行时语言环境: {temp}")
        text=self.lang.daily_liaison_mixin.k_27d2b829
        self.log_info(f"当前文本为{str(text)}")
        self.log_info("开始")