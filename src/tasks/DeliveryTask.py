import ctypes
import re
import time

from src.interaction.Mouse import active_and_send_mouse_delta

user32 = ctypes.windll.user32

from src.tasks.BaseEfTask import BaseEfTask

on_zip_line_tip = ["向目标移动", "离开滑索架"]
on_zip_line_stop = [re.compile(i) for i in on_zip_line_tip]
continue_next = re.compile("下一连接点")
secondary_objective_direction_dot = ["secondary_objective_direction_dot", "secondary_objective_direction_dot_light",
                                     "secondary_objective_direction_dot_light_two"]


class DeliveryTask(BaseEfTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "自动送货"
        self.description = "仅武陵7.31w送货,教程视频 BV1LLc7zFEF9 目前干员别礼,赛希送货效果较好"
        self.ends = ["常沄", "资源", "彦宁", "齐纶"]
        self.config_description = {
            '选择测试对象': "默认是无，表示正常执行送货任务\n也可以选择特定的滑索分叉序列来测试滑索功能\n选择完整循环测试则会依次测试每个送货目标的完整流程\n(需要锁定次要任务在送货任务上或附近)",
        }
        self.default_config.update(
            {
                "功能教程": """https://www.bilibili.com/video/BV1LLc7zFEF9\n分叉终点的滑索的高度最好要低于或等于分叉点滑索的高度\n角色的虚化机制导致拉近视角后头发无法有效遮挡背景以提高识别率""",
                "通向送货点": "36,14",
                "常沄": "14,108,64,109,60",
                "资源": "14,108,64,109",
                "彦宁": "14,108,64,108,59",
                "齐纶": "14,108,106",
                "是否启用滚动放大视角": True,
                "仅接取": False,
                "仅送货": False,
                "选择测试对象": "无",
            }
        )
        self.config_type["选择测试对象"] = {
            "type": "drop_down",
            "options": ["无", "通向送货点"] + self.ends + ["完整循环测试"],
        }
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
            16 / 9: (290 / 1080, 926 / 1080),  # 16:9
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
        self.next_frame()  # 确保拿到最新的截图
        left_items = self.ocr(box=left_box)
        right_items = self.ocr(box=right_box)
        mid_items = self.ocr(box=mid_box)

        # === 基础清洗 ===
        left_items = [i for i in left_items if getattr(i, "name", "").strip()]
        right_items = [i for i in right_items if getattr(i, "name", "").strip()]
        mid_items = [i for i in mid_items if getattr(i, "name", "").strip()]
        # === 分组 ===
        left_groups = [
            g for g in split_items_by_marker(left_items, "查看位置") if len(g) >= 2
        ]

        right_groups = [
            g for g in split_items_by_marker(right_items, "接取运送委托") if len(g) >= 2
        ]
        available_left = left_groups.copy()
        available_mid = mid_items.copy()

        rows = []

        for rg in right_groups:
            if rg[0].y < rg[1].y:
                rg_min_y = rg[0].y
                rg_max_y = rg[1].y + rg[1].height
            else:
                rg_min_y = rg[1].y
                rg_max_y = rg[0].y + rg[0].height

            matched_left = None
            matched_mid = None

            # ===== 找 left =====
            for lg in available_left:
                ys = [e.y for e in lg]
                if min(ys) >= rg_min_y and max(ys) <= rg_max_y:
                    matched_left = lg
                    break

            # ===== 找 mid =====
            for m in available_mid:
                if rg_min_y <= m.y <= rg_max_y:
                    matched_mid = m
                    break

            # ===== 任意成功就 remove =====
            if matched_left:
                available_left.remove(matched_left)

            if matched_mid:
                available_mid.remove(matched_mid)

            # ===== 构建 elems（顺序必须固定）=====
            elems = []

            if matched_left:
                elems += matched_left

            if matched_mid:
                elems += [matched_mid]

            elems += rg

            # ===== 不足5个不加入 =====
            if len(elems) >= 5:
                min_x = min(e.x for e in [elems[0], elems[-1]])
                max_x = max(e.x for e in [elems[0], elems[-1]])
                min_y = min(e.y for e in [elems[-2], elems[-1]])
                max_y = max(e.y + e.height for e in [elems[-2], elems[-1]])
                rows.append({"elems": elems, "box": (min_x, min_y, max_x, max_y)})

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
        self.ensure_main(time_out=120)
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.to_model_area("武陵", "仓储节点")
        delivery_box = self.wait_ocr(match="运送委托列表", time_out=5)
        if delivery_box:
            self.click(delivery_box[0], move_back=True, after_sleep=0.5)
            active_and_send_mouse_delta(self.hwnd.hwnd, only_activate=True)
        cx = int(self.width * 0.5)
        cy = int(self.height * 0.5)
        for _ in range(6):
            self.scroll(cx, cy, -8)
            self.sleep(0.2)
        self.wait_ui_stable(refresh_interval=1)
        enable_wuling = True
        ticket_types = []
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
                            x, y, to_x, to_y = row["box"]
                            if self.find_feature(feature_name="7_31w_wuling",
                                                 box=self.box_of_screen(x / self.width, y / self.height,
                                                                        to_x / self.width, to_y / self.height),
                                                 threshold=0.98):
                                self.click(
                                    row["elems"][-1],
                                    after_sleep=2,
                                    down_time=0.1,
                                    move_back=True,
                                )
                                return True
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
                    self.wait_ui_stable(refresh_interval=1) # 刷新后界面稳定的时间可能会比平常长一些，尤其是网络较慢的时候
                    break
                else:
                    self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                    time.sleep(1.0)

    def zip_line_list_go(self, zip_line_list):
        for zip_line in zip_line_list:
            self.align_ocr_or_find_target_to_center(
                re.compile(str(zip_line)),
                is_num=True,
                need_scroll=self.config.get("是否启用滚动放大视角"),
                ocr_frame_processor=self.isolate_white_yellow_text,
                max_time=100,
                tolerance=20,
            )
            self.log_info(f"成功将滑索调整到{zip_line}的中心")
            self.click(after_sleep=0.5)
            start = time.time()
            while True:
                self.next_frame()
                self.send_key("e")
                self.sleep(0.1)
                result = self.ocr(
                    match=on_zip_line_stop,
                    box="bottom",
                    log=True,
                )
                if result:
                    break
                if time.time() - start > 60:
                    raise Exception("滑索超时，强制退出")
        self.sleep(1)
        self.click(key="right")

    def on_zip_line_start(self, delivery_to):
        start = time.time()
        self.sleep(1)
        self.next_frame()
        while not self.ocr(match=on_zip_line_stop,frame=self.next_frame(), box="bottom", log=True):
            self.sleep(0.1)
            if time.time() - start > 60:
                raise Exception("滑索超时，强制退出")
        zip_line_list_str = self.config.get(delivery_to)
        zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
        self.zip_line_list_go(zip_line_list)

    def task_to_transfer_point(self):
        self.ensure_main()
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

        self.back(after_sleep=2)

        result = self.find_feature(feature_name="transfer_point", box=self.box_of_screen(0.01, 0.01, 0.99, 0.99),
                                   threshold=0.8)
        if not result:
            return False
        self.click(result)

        result = self.wait_ocr(match="传送", box="bottom_right", time_out=10, log=True)
        if not result:
            return False

        self.click(result)
        return True

    def to_storage_point_and_back_zip_line(self, only_zip_line=False):
        if self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=60, log=True):
            if self.wait_ocr(match="工业", box="top_left", time_out=2, log=True):
                self.send_key("tab", after_sleep=1)
            self.send_key("f", after_sleep=2)
            self.zip_line_list_go([int(i) for i in self.config.get('通向送货点').split(
                ",")])  # 需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            if only_zip_line:
                return True
            if self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=2, log=True):
                self.send_key("v", after_sleep=1)
                self.send_key("f", after_sleep=2)
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                    threshold=0.8,
                    ocr=False,
                    max_time=40,
                    raise_if_fail=False,
                    need_scroll=self.config.get("是否启用滚动放大视角"),
                )
                self.click(key="right")
            for i in range(40):
                self.sleep(2)
                self.send_key("v")
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                    threshold=0.7,
                    only_x=True,
                    ocr=False,
                    need_scroll=self.config.get("是否启用滚动放大视角"),
                )
                self.move_keys(
                    "w",
                    1,
                )
                if self.wait_ocr(match="仓储节点", box="bottom_right", time_out=2, log=True):
                    if self.wait_ocr(match="取货", box="bottom_right", time_out=2, log=True):
                        self.send_key("f")
                    break
            while not self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=10, log=True):
                self.move_keys("s", 1)
            return True
        return False

    def to_end_and_submit(self, end_pattern):
        self.ensure_main()
        if self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=30, log=True):
            self.send_key("v")
            self.send_key("f")
            self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                threshold=0.8,
                ocr=False,
                raise_if_fail=False,
                need_scroll=self.config.get("是否启用滚动放大视角"),
            )
            self.click(key="right")
        for i in range(40):
            self.sleep(2)
            self.send_key("v")
            self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                threshold=0.8,
                only_x=True,
                ocr=False,
                need_scroll=True,
            )
            self.move_keys(
                "w",
                0.5,
            )
            self.sleep(1)
            if self.wait_ocr(
                    match=end_pattern, box="bottom_right", time_out=2, log=True
            ):
                self.send_key("f", after_sleep=2)
                if not self.find_feature(feature_name="reward_ok"):
                    self.skip_dialog()
                    self.wait_click_ocr(match="确认", settle_time=2, after_sleep=2)
                self.wait_pop_up(after_sleep=2)
                break

    # def run(self):
    #     zip_line_list_str=self.config.get(self.config.get("选择测试对象"))
    #     if zip_line_list_str:
    #         zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
    #         self.zip_line_list_go(zip_line_list)
    def run(self):
        if not self._logged_in:
            self.ensure_main(time_out=240)
        else:
            self.ensure_main()
        if self.config.get("选择测试对象") == "无":
            for _ in range(3):
                if self.config.get("仅接取"):
                    self.other_run()
                    break
                else:
                    if not self.config.get("仅送货"):
                        self.other_run()
                        self.wait_click_ocr(match=re.compile("送达"), box="bottom_right", settle_time=4, time_out=10,
                                            after_sleep=10, log=True)
                    if not self.task_to_transfer_point():
                        return
                    if not self.to_storage_point_and_back_zip_line():
                        return
                    ends_list_pattern_dict = {re.compile(end): end for end in self.ends}
                    results = self.wait_ocr(
                        match=list(ends_list_pattern_dict.keys()), box="left", time_out=10, log=True
                    )
                    self.send_key("f", after_sleep=2)
                    end_pattern = None
                    if not results:
                        raise Exception("未识别到送货目标")

                    for result in results:
                        for pattern in ends_list_pattern_dict:
                            m = pattern.search(result.name)
                            if m:
                                end_pattern = pattern
                                self.on_zip_line_start(ends_list_pattern_dict[pattern])
                                break
                    self.to_end_and_submit(end_pattern)
                    if self.config.get("仅送货"):
                        break
        elif self.config.get("选择测试对象") == "完整循环测试":
            for end in self.ends:
                self.task_to_transfer_point()
                self.to_storage_point_and_back_zip_line(only_zip_line=True)
                self.wait_ocr(match="登上滑索架", box="bottom_right", time_out=2, log=True)
                self.send_key("f", after_sleep=2)
                self.on_zip_line_start(end)
                self.sleep(2)
        else:
            zip_line_list_str = self.config.get(self.config.get("选择测试对象"))
            if zip_line_list_str:
                zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
                self.zip_line_list_go(zip_line_list)
