from qfluentwidgets import FluentIcon

from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class TestDemoGraphic(Common, MapMixin, ZipLineMixin):
    """演算入口导航测试（对应 Test.py 历史版本：4937251）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "演算入口导航测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "导航到演武集算『演算』入口"
        self.visible = self.debug

        self.daily_demo = DailyDemoFeature.__new__(DailyDemoFeature)
        self.daily_demo._task = self

    def run(self):
        return self.daily_demo.go_to_demo_graphic()
