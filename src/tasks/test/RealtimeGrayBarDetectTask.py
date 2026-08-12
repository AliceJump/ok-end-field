from qfluentwidgets import FluentIcon
from ok import Box

from src.core.BaseEfTask import BaseEfTask
from src.image.gray_bar_detector import detect_gray_bars


class RealtimeGrayBarDetectTask(BaseEfTask):
    """持续检测关卡灰条，并在调试叠加层中绘制检测框。"""

    DEFAULT_MIN_WIDTH_RATIO = 34 / 1920
    DEFAULT_MAX_WIDTH_RATIO = 78 / 1920
    DEFAULT_Y_MIN_RATIO = 1077 / 1440
    DEFAULT_Y_MAX_RATIO = 1115 / 1440

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "关卡灰条实测扫描"
        self.group_name = "工具与调试"
        self.description = "持续检测关卡灰条；X 始终搜索整张屏幕，并可调整长度和归一化 Y 范围。"
        self.icon = FluentIcon.SEARCH
        self.visible = self.debug
        self.default_config.update({
            "最小长度比例": self.DEFAULT_MIN_WIDTH_RATIO,
            "最大长度比例": self.DEFAULT_MAX_WIDTH_RATIO,
            "Y最小比例": self.DEFAULT_Y_MIN_RATIO,
            "Y最大比例": self.DEFAULT_Y_MAX_RATIO,
            "扫描间隔(秒)": 0.2,
        })
        self.config_description.update({
            "最小长度比例": "灰条最小长度占屏幕宽度的比例；默认 34/1920，约为 0.017708。",
            "最大长度比例": "灰条最大长度占屏幕宽度的比例；默认 78/1920，约为 0.040625。",
            "Y最小比例": "搜索区域的上边界，范围为 0~1。",
            "Y最大比例": "搜索区域的下边界，范围为 0~1，必须大于 Y最小比例。",
            "扫描间隔(秒)": "每次检测后的等待时间。",
        })

    def run(self):
        min_width_ratio = float(self.config.get("最小长度比例", self.DEFAULT_MIN_WIDTH_RATIO) or 0)
        max_width_ratio = float(self.config.get("最大长度比例", self.DEFAULT_MAX_WIDTH_RATIO) or 0)
        y_min_ratio = float(self.config.get("Y最小比例", self.DEFAULT_Y_MIN_RATIO) or 0)
        y_max_ratio = float(self.config.get("Y最大比例", self.DEFAULT_Y_MAX_RATIO) or 1)
        interval = max(0.0, float(self.config.get("扫描间隔(秒)", 0.2) or 0.2))
        if not (0 <= min_width_ratio <= max_width_ratio <= 1 and 0 <= y_min_ratio < y_max_ratio <= 1):
            raise ValueError("长度和Y范围必须是 0~1 的比例，且最小值不得大于最大值")

        self.log_info(
            f"开始关卡灰条实测扫描: x=0~1, y={y_min_ratio:.4f}~{y_max_ratio:.4f}, "
            f"长度比例={min_width_ratio:.6f}~{max_width_ratio:.6f}",
            notify=True,
        )
        scan_count = 0
        last_count = None
        while True:
            scan_count += 1
            frame = self.next_frame()
            bars = detect_gray_bars(
                frame,
                x_min_ratio=0.0,
                x_max_ratio=1.0,
                y_min_ratio=y_min_ratio,
                y_max_ratio=y_max_ratio,
                min_width_ratio=min_width_ratio,
                max_width_ratio=max_width_ratio,
                min_aspect_ratio=3.5,
            )
            self._draw_gray_bar_boxes(bars)
            if len(bars) != last_count or scan_count % 20 == 0:
                self.log_info(f"[{scan_count}] 检测到 {len(bars)} 条关卡灰条")
            last_count = len(bars)
            self.sleep(interval)

    def _draw_gray_bar_boxes(self, bars):
        """启用调试叠加层时，使用与 yolo_detect 相同的画框接口。"""
        if not self._is_debug_overlay_enabled():
            return
        boxes = []
        for index, bar in enumerate(bars, start=1):
            box = Box(bar.x, bar.y, bar.width, bar.height)
            box.name = f"stage_gray_bar_{index}"
            box.confidence = 1.0
            boxes.append(box)
        self.draw_boxes("stage_gray_bars", boxes, color="green", debug=True)