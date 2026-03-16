import re
import time
from dataclasses import dataclass
from typing import List, Tuple

from ok import Box

from src.data.FeatureList import FeatureList as fL
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

secondary_objective_direction_dot = [fL.secondary_objective_direction_dot, fL.secondary_objective_direction_dot_light,
                                     fL.secondary_objective_direction_dot_light_two]


@dataclass
class DeliveryRow:
    """运输委托行对象 - 包含OCR元素和坐标信息
    
    Attributes:
        elems: OCR识别的元素列表
        box: 行的边界框(x1, y1, x2, y2)
    """
    elems: List[Box]  # OCRItem列表
    box: Tuple[float, float, float, float]  # (x1, y1, x2, y2)


class DeliveryTask(ZipLineMixin, MapMixin):
    """运输委托自动化任务类 - 处理游戏中的送货操作"""

    # 配置键名常量
    CFG_TARGET_TICKET_NUM = "目标券数"
    CFG_SCROLL_ENABLE = "是否启用滚动放大视角"
    CFG_TEST_TARGET = "选择测试对象"
    CFG_ONLY_ACCEPT = "仅接取"
    CFG_ONLY_DELIVER = "仅送货"
    CFG_TUTORIAL = "教程"
    CFG_TO_DELIVERY_POINT = "通向送货点"

    # 配置值常量
    TEST_NONE = "无"
    TEST_FULL_CYCLE = "完整循环测试"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.name = "自动送货"
        self.description = "仅武陵7.98w/7.31w送货,教程视频 BV1LLc7zFEF9"
        self.support_schedule_task = True
        self.ends = ["常沄", "资源", "彦宁", "齐纶"]
        self.config_description = {
            self.CFG_SCROLL_ENABLE: "启用后在对齐滑索时会自动滚动放大视角\n可能会提高对齐成功率，但也可能导致对齐成功率下降较为明显\n建议启用此项时不要使用非白发或有白帽角色",
            self.CFG_TEST_TARGET: "默认是无，表示正常执行相关任务\n也可以选择特定的滑索分叉序列来测试滑索功能\n选择完整循环测试则会依次测试每个送货目标的完整流程\n(需要锁定次要任务在送货任务上或附近)",
            self.CFG_ONLY_ACCEPT: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n仅接取7.31w武陵委托，不送货',
            self.CFG_ONLY_DELIVER: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n接取武陵委托后启动自动识别送货',
        }
        self.default_config.update(
            {
                self.CFG_TUTORIAL: "https://www.bilibili.com/video/BV1LLc7zFEF9\n游戏内开启全屏模式时请确保游戏内分辨率与你的屏幕分辨率一致",
                self.CFG_TARGET_TICKET_NUM: "79800",
                self.CFG_TO_DELIVERY_POINT: "36,14",
                "常沄": "14,108,64,109,60",
                "资源": "14,108,64,109",
                "彦宁": "14,108,64,108,59",
                "齐纶": "14,108,106",
                self.CFG_SCROLL_ENABLE: False,
                self.CFG_ONLY_ACCEPT: False,
                self.CFG_ONLY_DELIVER: False,
                self.CFG_TEST_TARGET: self.TEST_NONE,
            }
        )
        self.config_type[self.CFG_TEST_TARGET] = {
            "type": "drop_down",
            "options": [self.TEST_NONE, self.CFG_TO_DELIVERY_POINT] + self.ends + [self.TEST_FULL_CYCLE],
        }
        self.config_type[self.CFG_TARGET_TICKET_NUM] = {
            "type": "drop_down",
            "options": ["79800", "73100"],
        }
        self.wuling_location = ["武陵城"]
        self.valley_location = ["供能高地", "矿脉源区", "源石研究园"]
        self._last_refresh_ts = 0
        self.try_time=0
        self.add_exit_after_config()

    def merge_left_right_groups(self) -> List[DeliveryRow]:
        """合并OCR左右区域结果，按规则分组为行对象
        
        Returns:
            list[DeliveryRow]: 运输委托行列表
        """

        def split_items_by_marker(items: list, marker: str):
            """
            按item.name中的marker分组，marker归入上一组
            
            Args:
                items: OCRItem列表
                marker: 分组标记字符串
            
            Returns:
                list: 分组后的OCRItem列表
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
            1.5: (254 / 1280, 1134 / 1280),  # 3:2 宽高比（常见于部分平板与轻薄本，如 3000x2000）
            1.0: (0.1271, 0.8561 + (0.8561 - 0.1271) / 11),  # 1:1 宽高比（方屏/窗口接近正方形）
            9 / 16: (0.075, 0.7916),  # 9:16 宽高比（竖屏，如手机投屏）
            16 / 9: (290 / 1080, 926 / 1080),  # 16:9 宽高比（主流显示器，如 1920x1080 / 2560x1440）
        }

        screen_scale_desc = {
            1.5: "3:2（如 3000x2000）",
            1.0: "1:1（方屏/接近方屏窗口）",
            9 / 16: "9:16（竖屏）",
            16 / 9: "16:9（如 1920x1080、2560x1440）",
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
        ratio = self.width / self.height
        area = screen_scale_areas.get(ratio)
        if area is None:
            supported = "、".join(
                f"{k:.4f} -> {v}" for k, v in screen_scale_desc.items()
            )
            raise ValueError(
                f"不支持的屏幕比例: {ratio:.6f}（当前分辨率: {self.width}x{self.height}）。"
                f"支持的比例有：{supported}。"
                "请调整游戏窗口比例"
            )
        # === 区域定义 ===
        left_box = self.box_of_screen(area[0][0], area[0][1], area[0][2], area[0][3])
        right_box = self.box_of_screen(area[1][0], area[1][1], area[1][2], area[1][3])
        mid_box = self.box_of_screen(area[2][0], area[2][1], area[2][2], area[2][3])

        areas = [
            ("left", left_box, 10),
            ("right", right_box, 10),
            ("mid", mid_box, 5),
        ]

        # 期望比例
        expected_ratio = [2, 2, 1]  # 左:右:中
        total_ratio = sum(expected_ratio)

        results = {name: [] for name, _, _ in areas}
        start_time = time.time()

        while True:
            self.next_frame()  # 拿到最新截图

            # OCR
            for name, box, _ in areas:
                results[name] = self.ocr(
                    match=re.compile(r"[\u4e00-\u9fff]+"),
                    box=box,
                    log=True,
                    threshold=0.8
                )

            # 实际项数
            counts = [len(results[name]) for name, _, _ in areas]

            # 超时退出
            if time.time() - start_time > 2:
                break

            # 检查最小项数
            min_ok = all(c >= min_count for c, (_, _, min_count) in zip(counts, areas))

            # 检查比例
            total_count = sum(counts)
            if total_count % total_ratio != 0:
                ratio_ok = False
            else:
                unit = total_count // total_ratio
                ratio_ok = all(c == r * unit for c, r in zip(counts, expected_ratio))

            # 同时满足最小项数和比例才算 OK
            if min_ok and ratio_ok:
                break  # 满足条件，退出循环
            else:
                self.sleep(0.1)  # 不满足，等待再重扫一次

        # 最终 OCR 结果
        left_items = results["left"]
        right_items = results["right"]
        mid_items = results["mid"]

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
                rows.append(DeliveryRow(elems=elems, box=(min_x, min_y, max_x, max_y)))

        return rows

    def detect_ticket_type(self, row: DeliveryRow) -> str | None:
        """检测行对象中的票券类型
        
        Args:
            row: DeliveryRow运输委托行对象
        
        Returns:
            str: 票券类型("ticket_wuling"或"ticket_valley")或None
        """
        first_name = row.elems[0].name
        if any(k in first_name for k in self.wuling_location):
            return "ticket_wuling"

        if any(k in first_name for k in self.valley_location):
            return "ticket_valley"
        return None

    def other_run(self):
        """接取运输委托的主流程
        
        Returns:
            bool: 成功返回True，失败返回False
        """
        self.try_time=0
        self.ensure_main(time_out=120)
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.to_model_area("武陵", "仓储节点")
        delivery_box = self.wait_ocr(match="运送委托列表", time_out=5)
        if delivery_box:
            self.click(delivery_box[0], move_back=True, after_sleep=0.5)
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
        start_time=time.time()
        while True:
            if time.time()-start_time>600:
                self.log_info("接单尝试时间过长，退出")
                return False
            rows = self.merge_left_right_groups()
            for row in rows:
                if row:
                    ticket_type = self.detect_ticket_type(row)
                    if ticket_type == "ticket_wuling" and enable_wuling:
                        if (
                                "易损" in row.elems[2].name
                                and "不易损" not in row.elems[2].name
                        ):
                            target_num = self.config.get(self.CFG_TARGET_TICKET_NUM)
                            x, y, to_x, to_y = row.box
                            box = self.box_of_screen(
                                x / self.width,
                                y / self.height,
                                to_x / self.width,
                                to_y / self.height,
                            )
                            if self.width >= 3800:  # 4K
                                feature_list = [
                                    fL.wuling_7_31w_4k,
                                    fL.wuling_7_31w_dark_4k,
                                ]
                            elif self.width >= 2500:  # 2K
                                feature_list = [
                                    fL.wuling_7_31w_2k,
                                    fL.wuling_7_31w_dark_2k,
                                ]
                            else:  # 1080
                                feature_list = [
                                    fL.wuling_7_31w,
                                    fL.wuling_7_31w_dark,
                                ]
                            if target_num == "79800":
                                for i in range(len(feature_list)):
                                    feature_list[i] = feature_list[i].replace("7_31w", "7_98w")
                            result = None
                            for feature_name in feature_list:
                                result = self.find_feature(
                                    feature_name=feature_name,
                                    box=box,
                                    threshold=0.98,
                                )
                                if result:
                                    break
                            if result:
                                self.click(
                                    row.elems[-1],
                                    after_sleep=2,
                                    down_time=0.1,
                                    move_back=True,
                                )
                                self.log_info("疑似已经接取委托")
                                self.try_time+=1
                                if self.try_time>5:
                                    self.log_info("尝试次数过多，退出")
                                    return False
                                self.next_frame()
                                if not self.wait_ocr(match="接取运送委托", box=self.box.bottom_right, time_out=1):
                                    self.log_info("接取成功")
                                    return True
                                else:
                                    self.log_info("接取失败，可能委托被抢了，继续寻找")
            self.log_info("未找到符合条件(金额+类型)的委托，准备刷新重试")
            for i in range(2):
                if last_refresh_box := self.wait_ocr(match="刷新", box=self.box.bottom_right):
                    now = time.time()
                    last = getattr(self, "_last_refresh_ts", 0.0)
                    wait = max(0.0, 5.4 - (now - last))
                    if wait > 0:
                        self.sleep(wait)
                    self.click(last_refresh_box, move_back=True)
                    self._last_refresh_ts = time.time()
                    self.wait_ui_stable(refresh_interval=1)  # 刷新后界面稳定的时间可能会比平常长一些，尤其是网络较慢的时候
                    break
                else:
                    self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                    time.sleep(1.0)

    def to_storage_point_and_back_zip_line(self, only_zip_line=False):
        """从仓储点出发，乘坐滑索到送货点
        
        Args:
            only_zip_line: True时仅乘坐出发滑索，False时乘至仓储点
        
        Returns:
            bool: 成功返回True，失败返回False
        """
        if self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=60, log=True):
            if self.wait_ocr(match="工业", box=self.box.top_left, time_out=2, log=True):
                self.press_key("tab", after_sleep=1)
            self.press_key('f', after_sleep=2)
            self.zip_line_list_go([int(i) for i in self.config.get(self.CFG_TO_DELIVERY_POINT).split(",")],
                                  need_scroll=self.config.get(self.CFG_SCROLL_ENABLE))  # 需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            if only_zip_line:
                return True
            if self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=2, log=True):
                self.press_key("v", after_sleep=1)
                self.press_key('f', after_sleep=2)
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                    threshold=0.8,
                    ocr=False,
                    max_time=40,
                    raise_if_fail=False,
                    need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                )
                self.click(key="right", after_sleep=2)
            for i in range(40):
                self.sleep(2)
                self.press_key("v")
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                    threshold=0.7,
                    only_x=True,
                    ocr=False,
                    need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                )
                self.move_keys(
                    "w",
                    1,
                )
                if self.wait_ocr(match="仓储节点", box=self.box.bottom_right, time_out=2, log=True):
                    if self.wait_ocr(match="取货", box=self.box.bottom_right, time_out=2, log=True):
                        self.press_key('f')
                    break
            while not self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=2, log=True):
                self.move_keys("s", 1)
            return True
        return False

    def to_end_and_submit(self, end_pattern):
        """从仓储点出发到目标点并提交委托
        
        Args:
            end_pattern: 目标点的正则匹配模式
        """
        self.ensure_main()
        if self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=30, log=True):
            self.press_key("v", after_sleep=1)
            self.press_key('f', after_sleep=2)
            self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                threshold=0.8,
                ocr=False,
                raise_if_fail=False,
                need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
            )
            self.click(key="right", after_sleep=2)
        for i in range(40):
            self.sleep(2)
            self.press_key("v", after_sleep=1)
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
                    match=end_pattern, box=self.box.bottom_right, time_out=2, log=True
            ):
                self.press_key('f', after_sleep=2)
                if not self.find_reward_ok():
                    self.skip_dialog()
                    self.wait_click_ocr(match="确认", settle_time=2, after_sleep=2)
                self.wait_pop_up(after_sleep=2)
                break

    def run(self):
        """运输委托任务的主入口，支持多种运行模式"""
        if self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE:
            ends_list_pattern_dict = {}
            for end in self.ends:
                if end == "常沄":
                    pattern = re.compile(r"常[沄云汶运法]")
                else:
                    pattern = re.compile(end)

                ends_list_pattern_dict[pattern] = end
            for _ in range(3):
                if not self._logged_in:
                    self.ensure_main(time_out=240)
                else:
                    self.ensure_main()
                self.back(after_sleep=2)
                self.ensure_main()
                if self.config.get(self.CFG_ONLY_ACCEPT):
                    self.other_run()
                    break
                else:
                    if not self.config.get(self.CFG_ONLY_DELIVER):
                        if not self.other_run():
                            return
                        self.wait_click_ocr(match=re.compile("送达"), box=self.box.bottom_right, settle_time=4,
                                            time_out=10,
                                            after_sleep=10, log=True)
                    success = None
                    for _ in range(3):
                        success = self.task_to_transfer_point()
                        if success:
                            break
                    if not success:
                        self.log_info("前往传送点失败，重试...")
                        return
                    if not self.to_storage_point_and_back_zip_line():
                        return
                    results = self.wait_ocr(
                        match=list(ends_list_pattern_dict.keys()), box=self.box.left, time_out=10, log=True
                    )
                    self.press_key('f', after_sleep=2)
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
                    if self.config.get(self.CFG_ONLY_DELIVER):
                        break
        elif self.config.get(self.CFG_TEST_TARGET) == self.TEST_FULL_CYCLE:
            for end in self.ends:
                self.task_to_transfer_point(self.box.bottom)
                self.to_storage_point_and_back_zip_line(only_zip_line=True)
                self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=2, log=True)
                self.press_key('f', after_sleep=2)
                self.on_zip_line_start(end)
                self.sleep(2)
        else:
            zip_line_list_str = self.config.get(self.config.get(self.CFG_TEST_TARGET))
            if zip_line_list_str:
                zip_line_list = [int(i) for i in zip_line_list_str.split(",")]
                self.zip_line_list_go(zip_line_list, need_scroll=self.config.get(self.CFG_SCROLL_ENABLE))
