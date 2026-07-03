import time
import re
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.mixin.map_mixin import MapMixin

secondary_objective_direction_dot = [
    fL.secondary_objective_direction_dot,
    fL.secondary_objective_direction_dot_light,
    fL.secondary_objective_direction_dot_light_two,
    fL.secondary_objective_direction_dot_light_three
]

class Test(NavigationMixin, MapMixin):
    """
    简单箭头角度读取测试
    直接调用 get_arrow_angle() 并持续输出当前角度
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "测试"
        self.group_name = "工具与调试"
        self.description = "蓝点归中测试"

        self.interval = 0.3  # 读取间隔（秒）

    def run(self):
        DailyDemoFeature(self).go_to_DemoGraphic()
        