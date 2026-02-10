import re
import time
from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger
from src.tasks.BaseEfTask import BaseEfTask
logger = Logger.get_logger(__name__)


class TakeDeliveryTask(BaseEfTask, TriggerTask):
    """
    TakeDeliveryTask

    功能：自动接取高价值调度任务。
    逻辑：同时识别“报酬金额”与“调度券类型（图标）”，满足条件则接取，否则刷新。

    配置说明：
    - `target_tickets`: 目标券种，列表。可选值：`ticket_valley`, `ticket_wuling`。
    - `min_reward`: 最低报酬金额（万）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "运送委托接取"
        self.description = "自动抢单"
        self.icon = FluentIcon.ACCEPT
        self.default_config = {"接取谷地券": False, "接取武陵券": True}
        self.wuling_location = ["武陵城"]
        self.valley_location = ["供能高地", "矿脉源区", "源石研究园"]
        self._last_refresh_ts=0

    def merge_left_right_groups(self):
        """
        OCR 左右区域并按指定规则分组后合并为行（对象级）
        每个 row:
        - elems: OCRItem 列表
        - box:   (x1, y1, x2, y2)
        """

        def split_items_by_marker(items: list, marker: str):
            """
            按 item.name 中的 marker 分组
            marker 归入上一组
            返回: list[list[item]]
            """
            groups = []
            current = []

            for item in items:
                name = getattr(item, "name", "").strip()
                if not name:
                    continue

                current.append(item)

                if marker in name:
                    groups.append(current)
                    current = []

            if current:
                groups.append(current)

            return groups

        screen_scale_y1_y2 = {
            1.5:    (254/1280, 1134/1280),   # 3:2
            1.0:    (0.1271, 0.8561+(0.8561-0.1271)/11),   # 1:1
            9/16:   (0.075,  0.7916),   # 9:16
            16/9:    (290/1080,  926/1080-(926-290)/5/1080),   # 16:9
        }

        x_ranges = [
            (0.4776, 0.5505),
            (0.8438, 0.9167),
            (0.3141, 0.3641),
        ]

        screen_scale_areas = {
            ratio: [
                [x1, y1, x2, y2]
                for (x1, x2) in x_ranges
            ]
            for ratio, (y1, y2) in screen_scale_y1_y2.items()
        }

        area=screen_scale_areas[self.width / self.height]
        # === 区域定义 ===
        left_box = self.box_of_screen(area[0][0], area[0][1], area[0][2], area[0][3])
        right_box = self.box_of_screen(area[1][0], area[1][1], area[1][2], area[1][3])
        mid_box = self.box_of_screen(area[2][0], area[2][1], area[2][2], area[2][3])

        # === OCR ===
        left_items = self.ocr(box=left_box)
        right_items = self.ocr(box=right_box)
        mid_items = self.ocr(box=mid_box)

        # === 基础清洗 ===
        left_items = [i for i in left_items if getattr(i, "name", "").strip()]
        right_items = [i for i in right_items if getattr(i, "name", "").strip()]
        mid_items = [i for i in mid_items if getattr(i, "name", "").strip()]
        # === 分组 ===
        left_groups = split_items_by_marker(left_items, "查看位置")
        right_groups = split_items_by_marker(right_items, "接取运送委托")
        rows = []

        count = min(len(left_groups), len(right_groups), len(mid_items))
        for i in range(count):
            elems = left_groups[i] + [mid_items[i]] + right_groups[i]
            rows.append({"elems": elems})

        return rows

    def detect_ticket_type(self, row):
        if not row or not row.get("elems"):
            return None
        first_name = row["elems"][0].name
        if any(k in first_name for k in self.wuling_location):
            return "ticket_wuling"

        if any(k in first_name for k in self.valley_location):
            return "ticket_valley"
        return None
    def other_run(self):
        cx = int(self.width * 0.5)
        cy = int(self.height * 0.5)
        for _ in range(6):
            self.scroll(cx, cy, -8)
            self.sleep(0.2)
        self.sleep(2.0)
        # 读取券种配置
        # enable_valley = self.config.get("接取谷地券", False)
        enable_wuling = True
        ticket_types = []
        # if enable_valley:
        #     ticket_types.append("ticket_valley")
        if enable_wuling:
            ticket_types.append("ticket_wuling")

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return None
        while True:
            rows = self.merge_left_right_groups()
            for row in rows:
                if row:
                    ticket_type = self.detect_ticket_type(row)
                    if ticket_type == "ticket_wuling" and enable_wuling:
                        if (
                            "易损" in row["elems"][2].name
                            and "不易损" not in row["elems"][2].name
                        ):
                            self.click(
                                row["elems"][-1],
                                after_sleep=2,
                                down_time=0.1,
                                move_back=True,
                            )
                            return True
                    # elif ticket_type == "ticket_valley" and enable_valley:
                    #     if "极易损" in row["elems"][2].name:
                    #         self.click(
                    #             row["elems"][-1],
                    #             after_sleep=2,
                    #             down_time=0.1,
                    #             move_back=True,
                    #         )
                    #         return True
            self.log_info("未找到符合条件(金额+类型)的委托，准备刷新重试")
            for i in range(2):
                if last_refresh_box := self.wait_ocr(match="刷新", box="bottom_right"):
                    now = time.time()
                    last = getattr(self, "_last_refresh_ts", 0.0)
                    wait = max(0.0, 5.4 - (now - last))
                    if wait > 0:
                        self.sleep(wait)
                    self.click(last_refresh_box, move_back=True)
                    self._last_refresh_ts = time.time()
                    self.sleep(3.0)  # 等待刷新内容加载
                else:
                    self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                    time.sleep(1.0)

    def run(self):
        cx = int(self.width * 0.5)
        cy = int(self.height * 0.5)
        for _ in range(6):
            self.scroll(cx, cy, -8)
            self.sleep(0.2)
        self.sleep(2.0)
        # 读取券种配置
        enable_valley = self.config.get("接取谷地券", False)
        enable_wuling = self.config.get("接取武陵券", True)
        ticket_types = []
        if enable_valley:
            ticket_types.append("ticket_valley")
        if enable_wuling:
            ticket_types.append("ticket_wuling")

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return None
        while True:
            rows = self.merge_left_right_groups()
            for row in rows:
                if row:
                    ticket_type = self.detect_ticket_type(row)
                    if ticket_type == "ticket_wuling" and enable_wuling:
                        if (
                            "易损" in row["elems"][2].name
                            and "不易损" not in row["elems"][2].name
                        ):
                            self.click(
                                row["elems"][-1],
                                after_sleep=2,
                                down_time=0.1,
                                move_back=True,
                            )
                            return True
                    elif ticket_type == "ticket_valley" and enable_valley:
                        if "极易损" in row["elems"][2].name:
                            self.click(
                                row["elems"][-1],
                                after_sleep=2,
                                down_time=0.1,
                                move_back=True,
                            )
                            return True
            self.log_info("未找到符合条件(金额+类型)的委托，准备刷新重试")
            for i in range(2):
                if last_refresh_box:=self.wait_ocr(match="刷新", box="bottom_right"):
                    now = time.time()
                    last = getattr(self, "_last_refresh_ts", 0.0)
                    wait = max(0.0, 5.4 - (now - last))
                    if wait > 0:
                        self.sleep(wait)
                    self.click(last_refresh_box, move_back=True)
                    self._last_refresh_ts = time.time()
                    self.sleep(3.0)  # 等待刷新内容加载
                else:
                    self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                    time.sleep(1.0)
