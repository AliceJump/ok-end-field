from qfluentwidgets import FluentIcon

from src.tasks.mixin.mouse_scan_mixin import MouseScanMixin


class TestDragScan(MouseScanMixin):
    """区域扫描测试（对应 Test.py 历史版本：632868c）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "区域扫描测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = "按住鼠标蛇形扫描指定矩形区域"
        self.visible = self.debug

    def run(self):
        self.drag_scan_area(
            (354 / 1920, 382 / 1080),
            (1526 / 1920, 768 / 1080),
        )
