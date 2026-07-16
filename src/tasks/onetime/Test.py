import time
import re
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.core.BaseEfTask import BaseEfTask
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.mouse_scan_mixin import MouseScanMixin

secondary_objective_direction_dot = [
    fL.secondary_objective_direction_dot,
    fL.secondary_objective_direction_dot_light,
    fL.secondary_objective_direction_dot_light_two,
    fL.secondary_objective_direction_dot_light_three
]

class Test(MouseScanMixin):
    """
    简单箭头角度读取测试
    直接调用 get_arrow_angle() 并持续输出当前角度
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "测试"

    def run(self):
        self.drag_scan_area((354/1920,382/1080),(1526/1920,768/1080))
        