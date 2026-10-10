from ok import Box
from qfluentwidgets import FluentIcon

from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class ZipLineScanDebugTask(ZipLineMixin):
    """仅扫描当前画面的滑索图标并实时画框，不执行对中、OCR 或交互。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "滑索扫描实测"
        self.group_name = "工具与调试"
        self.description = (
            "持续复用滑索模板候选扫描，仅检测当前画面的白色与黄色/金黄色滑索图标并实时画框；"
            "不会转动视角、识别距离、移动角色或执行交互。绿框=白色候选，黄框=黄色/金黄色候选。"
        )
        self.icon = FluentIcon.SEARCH
        self.visible = self.debug
        self.default_config.update({"扫描间隔(秒)": 0.15})
        self.config_description.update({"扫描间隔(秒)": "每次当前画面滑索模板扫描后的等待时间。"})

    def run(self):
        interval_value = self.config.get("扫描间隔(秒)", 0.15)
        interval = max(
            0.0,
            float(0.15 if interval_value is None or interval_value == "" else interval_value),
        )
        self.log_info("开始滑索扫描实测：仅扫描当前画面并画框，不执行任何对中或交互", notify=True)

        scan_count = 0
        last_summary = None
        while True:
            scan_count += 1
            frame = self.next_frame()
            candidates = self._detect_zip_line_icon_candidates(frame)

            white_boxes = []
            gold_boxes = []
            for index, candidate in enumerate(candidates, start=1):
                box = Box(
                    candidate["x"],
                    candidate["y"],
                    candidate["width"],
                    candidate["height"],
                )
                state = str(candidate["state"])
                score = float(candidate["score"])
                radius = float(candidate["radius"])
                box.name = f"zipline_{state}_{index}_r{radius:.2f}"
                box.confidence = score
                if state == "white":
                    white_boxes.append(box)
                else:
                    gold_boxes.append(box)

            self.draw_boxes("zipline_scan_white", white_boxes, color="green", debug=True)
            self.draw_boxes("zipline_scan_gold", gold_boxes, color="yellow", debug=True)

            summary = (len(white_boxes), len(gold_boxes))
            if summary != last_summary or scan_count % 20 == 0:
                self.log_info(
                    f"[{scan_count}] 滑索候选: 白色={summary[0]}, 黄色/金黄色={summary[1]}, 总计={len(candidates)}"
                )
                last_summary = summary

            self.sleep(interval)
