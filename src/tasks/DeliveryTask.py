import re
import time
import win32gui
import ctypes
import win32con
from src.tasks.TakeDeliveryTask import TakeDeliveryTask

user32 = ctypes.windll.user32


from src.tasks.BaseEfTask import BaseEfTask

on_zip_line_stop = re.compile("向目标移动")
continue_next = re.compile("下一连接点")


class DeliveryTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "半自动送货"
        self.description = '选择后上滑索时启动'
        self.ends = ["常沄", "资源", "彦宁", "齐纶"]
        self.default_config.update({
            '说明': '需填写滑索分叉序列(例如"108,64,109",是指上滑索后找108m的滑索并试图滑向它,后面依次为第一个分叉点,第二个分叉点...)',
            '通向送货点的滑索分叉序列':'36,14',
            '常沄': "14,108,64,109,60",
            '资源': "14,108,64,109",
            '彦宁': "14,108,64,108,59",
            '齐纶': "14,108,106"
        })
        self.lv_regex = re.compile(r"(?i)lv|\d{2}")
        self.last_target = None
        self.wuling_location = ["武陵城"]
        self.valley_location = ["供能高地", "矿脉源区", "源石研究园"]
        self._last_refresh_ts = 0

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
            1.5: (254 / 1280, 1134 / 1280),  # 3:2
            1.0: (0.1271, 0.8561 + (0.8561 - 0.1271) / 11),  # 1:1
            9 / 16: (0.075, 0.7916),  # 9:16
            16 / 9: (290 / 1080, 926 / 1080 - (926 - 290) / 5 / 1080),  # 16:9
        }

        x_ranges = [
            (0.4776, 0.5505),
            (0.8438, 0.9167),
            (0.3141, 0.3641),
        ]

        screen_scale_areas = {
            ratio: [[x1, y1, x2, y2] for (x1, x2) in x_ranges]
            for ratio, (y1, y2) in screen_scale_y1_y2.items()
        }

        area = screen_scale_areas[self.width / self.height]
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
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.send_key("y", down_time=0.05, after_sleep=0.5)
        storage_box = self.wait_ocr(match="仓储节点", time_out=5)
        if storage_box:
            self.click(storage_box[0], move_back=True, after_sleep=0.5)
        else:
            self.log_error("未找到‘仓储节点’按钮，任务中止。")
            return

        delivery_box = self.wait_ocr(match="运送委托列表", time_out=5)
        if delivery_box:
            self.click(delivery_box[0], move_back=True, after_sleep=0.5)
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

    def zip_line_list_go(self, zip_line_list):
        for zip_line in zip_line_list:
            self.align_ocr_or_find_target_to_center(re.compile(str(zip_line)))
            self.log_info(f"成功将滑索调整到{zip_line}的中心")
            self.click(after_sleep=0.5)
            start = time.time()
            while not self.ocr(match=on_zip_line_stop, box="bottom", log=True):
                self.send_key("e")
                self.sleep(0.5)
                if time.time() - start > 60:
                    raise Exception("滑索超时，强制退出")
        self.sleep(1)
        self.click(key="right")
    def on_zip_line_start(self, delivery_to):
        start = time.time()
        while not self.ocr(match=on_zip_line_stop, box="bottom", log=True):
            self.sleep(2)
            if time.time() - start > 60:
                raise Exception("滑索超时，强制退出")
        zip_line_list_str=self.config.get(delivery_to)
        zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
        self.zip_line_list_go(zip_line_list)

    def task_to_transfer_point(self):
        if not self.wait_ocr(match=["工业","探索"], box="top_left", time_out=10, log=True):
            raise Exception("未在主页面")
        self.send_key("j", after_sleep=2)

        result = self.find_feature(
            feature_name="one_task_to_map", threshold=0.8, box="bottom_right"
        )
        if not result:
            return False
        self.click(result, after_sleep=2)

        if not self.wait_click_ocr(
            match="标记显示管理", box="bottom_left", time_out=10, log=True
        ):
            return False

        if not self.wait_click_ocr(
            match="清空选中", box="bottom_left", time_out=10, log=True
        ):
            return False

        self.back()

        result = self.find_feature(feature_name="transfer_point",box=self.box_of_screen(0.01,0.01,0.99,0.99), threshold=0.8)
        if not result:
            return False
        self.click(result)

        result = self.wait_ocr(match="传送", box="bottom_right", time_out=10, log=True)
        if not result:
            return False

        self.click(result)
        return True
    def to_storage_point_and_back_zip_line(self):
        if self.wait_ocr(match="登上滑索架", box="bottom_right",time_out=30, log=True):
            if self.wait_ocr(match="工业",box="top_left", time_out=2, log=True):
                self.send_key("tab" , after_sleep=1)
            self.send_key("f", after_sleep=2)
            self.zip_line_list_go([int(i) for i in self.config.get('通向送货点的滑索分叉序列').split(",")])#需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            for i in range(40):
                self.sleep(2)
                self.send_key("v")
                self.align_ocr_or_find_target_to_center(
                    "secondary_objective_direction_dot",
                    box=self.box_of_screen(
                        (1920 - 1550) / 1920,
                        150 / 1080,
                        1550 / 1920,
                        (1080 - 150) / 1080,
                    ),
                    threshold=0.7,
                    ocr=False,
                )
                self.move_keys(
                    "w",
                    1,
                )
                if self.wait_ocr(match="仓储节点", box="bottom_right", time_out=2, log=True):
                    if result:=self.wait_ocr(match="取货", box="bottom_right", time_out=2, log=True):
                        self.send_key("f")
                    break
            while not self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=10, log=True):
                self.move_keys("s", 1)

    def to_end_and_submit(self, end_pattern):
        if not self.wait_ocr(
            match=["工业", "探索"], box="top_left", time_out=10, log=True
        ):
            raise Exception("未在主页面")
        self.send_key("v")
        self.send_key("f")
        self.align_ocr_or_find_target_to_center(
            "secondary_objective_direction_dot",
            box=self.box_of_screen(
                (1920 - 1550) / 1920,
                150 / 1080,
                1550 / 1920,
                (1080 - 150) / 1080,
            ),
            threshold=0.6,
            ocr=False,
            raise_if_fail=False,
        )
        self.click(key="right")
        for i in range(40):
            self.sleep(2)
            self.send_key("v")
            self.align_ocr_or_find_target_to_center(
                "secondary_objective_direction_dot",
                box=self.box_of_screen(
                    (1920 - 1550) / 1920,
                    150 / 1080,
                    1550 / 1920,
                    (1080 - 150) / 1080,
                ),
                threshold=0.6,
                ocr=False,
            )
            self.move_keys(
                "w",
                0.5,
            )
            if self.wait_ocr(
                match=end_pattern, box="bottom_right", time_out=2, log=True
            ):
                self.send_key("f")
                break

    def run(self):
        for i in range(3):
            self.other_run()
            self.wait_click_ocr(match=re.compile("送达"), box="bottom_right",settle_time=4, time_out=10, log=True)
            self.task_to_transfer_point()
            self.to_storage_point_and_back_zip_line()
            ends_list_pattern_dict = {re.compile(end): end for end in self.ends}
            results = self.wait_ocr(
                match=list(ends_list_pattern_dict.keys()), box="left", time_out=10, log=True
            )
            self.send_key("f", after_sleep=2)
            end_pattern=None
            if not results:
                raise Exception("未识别到送货目标")

            for result in results:
                for pattern in ends_list_pattern_dict:
                    m = pattern.search(result.name)
                    if m:
                        self.on_zip_line_start(ends_list_pattern_dict[pattern])
                        break
            self.to_end_and_submit(end_pattern)
            count=0
            while True:
                if count>30:
                    raise Exception("提交后未检测到奖励界面，提交失败")
                result= self.find_one(feature_name="reward_ok",box="bottom")
                if result:
                    self.click(result)
                    break
                self.sleep(1)
                count+=1
