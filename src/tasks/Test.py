import time
import random

from qfluentwidgets import FluentIcon
from PySide6.QtWidgets import QApplication

from src.tasks.BaseEfTask import BaseEfTask


class Test(BaseEfTask):
    _round_robin_index = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "窗口箭头绘制测试"
        self.description = "在实际游戏窗口上绘制测试箭头，验证归一化坐标和窗口粗度自适应。"
        self.icon = FluentIcon.SEARCH
        self.set_window_arrow_style(
            arrow_color=(0, 255, 0),
            shaft_width_norm=0.01,
            head_angle_deg=28.0,
            head_len_ratio=0.35,
        )

    def run(self):
        try:
            width, height = self.get_window_arrow_size()
            dpi_scale = self.get_window_arrow_dpi_scale()
            if width <= 0 or height <= 0:
                self.log_info("未获取到有效窗口尺寸，无法绘制箭头", notify=True)
                return

            shaft_width_norm = 0.01
            if max(width, height) >= 3000:
                shaft_width_norm = 0.018
            elif max(width, height) >= 1800:
                shaft_width_norm = 0.014

            self.log_info(
                f"开始窗口箭头测试: size={width}x{height}, dpi_scale={dpi_scale}, shaft_width_norm={shaft_width_norm}",
                notify=True,
            )

            center_x = width * 0.08
            center_y = height * 0.42
            max_length = min(width, height) * 0.08

            for angle_deg in range(0, 360, 10):
                requested_length = max_length * random.uniform(1.15, 1.9)
                success = self.draw_window_arrow_from_center(
                    center_x,
                    center_y,
                    max_length=max_length,
                    draw_length=requested_length,
                    angle_deg=angle_deg,
                    shaft_width_norm=shaft_width_norm,
                    color=(0, 255, 0),
                )
                self.log_info(
                    f"转圈测试 angle={angle_deg} requested_length={requested_length:.1f} max_length={max_length:.1f} success={success}",
                    notify=False,
                )
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()
                time.sleep(0.12)

            time.sleep(1)

            self.log_info("窗口箭头测试完成", notify=True)
        finally:
            self.clear_window_arrows()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
