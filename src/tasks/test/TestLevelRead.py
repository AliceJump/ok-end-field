from qfluentwidgets import FluentIcon

from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class TestLevelRead(Common, MapMixin, ZipLineMixin):
    """等级读取测试（对应 Test.py 历史版本：0bf222f）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "等级读取测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "读取演武集算关卡界面的当前等级"
        self.visible = self.debug

        self.daily_demo = DailyDemoFeature.__new__(DailyDemoFeature)
        self.daily_demo._task = self

    def run(self):
        level = self.daily_demo.read_level()
        if level < 0:
            return False
        self.log_info(f"当前等级: {level}")
        return True
