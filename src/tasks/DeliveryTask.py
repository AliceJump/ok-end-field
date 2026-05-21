import re
import time
import webbrowser
from datetime import datetime
from dataclasses import dataclass
from typing import List, Tuple

from ok import Box, TaskDisabledException
from qfluentwidgets import FluentIcon

from src.data.delivery_area import (
    DEFAULT_DELIVERY_AREA,
    DELIVERY_AREA_CONFIG,
    DELIVERY_TARGET_TICKET_NUM_OPTIONS,
)
from src.data.delivery_area_service import (
    extract_delivery_location,
    get_accept_feature_labels,
    get_delivery_locations,
    get_delivery_target_ocr_pattern,
    get_delivery_targets,
    get_full_cycle_targets,
    get_task_model_area,
    get_transfer_search_area,
)
from src.data.FeatureList import FeatureList as fL
from src.tasks.account.account_mixin import AccountMixin
from src.tasks.sequence_parser import parse_int_sequence
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

secondary_objective_direction_dot = [fL.secondary_objective_direction_dot, fL.secondary_objective_direction_dot_light,
                                     fL.secondary_objective_direction_dot_light_two, fL.secondary_objective_direction_dot_light_three]


@dataclass
class DeliveryRow:
    """运输委托行对象 - 包含OCR元素和坐标信息
    
    Attributes:
        elems: OCR识别的元素列表
        box: 行的边界框(x1, y1, x2, y2)
    """
    elems: List[Box]  # OCRItem列表
    box: Tuple[float, float, float, float]  # (x1, y1, x2, y2)


class DeliveryTask(AccountMixin, ZipLineMixin, MapMixin):
    """运输委托自动化任务类 - 处理游戏中的送货操作"""

    # 配置键名常量
    CFG_TARGET_TICKET_NUM = "目标券数"
    CFG_SCROLL_ENABLE = "是否启用滚动放大视角"
    CFG_TEST_TARGET = "选择测试对象"
    CFG_ONLY_ACCEPT = "仅接取"
    CFG_ONLY_DELIVER = "仅送货"
    CFG_TUTORIAL = "教程"
    CFG_DELIVERY_AREA = "地区切换"
    CFG_TO_DELIVERY_POINT = "通向送货点"
    CFG_FULL_CYCLE_LOCATION = "完整循环测试区域"
    TUTORIAL_LINK = "https://www.bilibili.com/video/BV1LLc7zFEF9"
    TUTORIAL_TIPS = "游戏内开启全屏模式时请确保游戏内分辨率与你的屏幕分辨率一致"

    # 配置值常量
    TEST_NONE = "无"
    TEST_FULL_CYCLE = "完整循环测试"

    def _configure_delivery_area(self, area_name: str):
        if area_name not in DELIVERY_AREA_CONFIG:
            area_name = DEFAULT_DELIVERY_AREA
        self.delivery_area = area_name
        self.full_cycle_locations = get_delivery_locations(self.delivery_area)
        self.ends = get_delivery_targets(self.delivery_area)
        self.to_delivery_point_config_keys = list(
            dict.fromkeys(
                [self._to_delivery_point_config_key(location_name) for location_name in self.full_cycle_locations]
            )
        )
        self.default_config_group.update({"滑索配置": self.to_delivery_point_config_keys + self.ends})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({"_enabled": True})
        self.name = "自动送货"
        self.description = "根据地区配置自动送货,教程视频 BV1LLc7zFEF9"
        self._configure_delivery_area(DEFAULT_DELIVERY_AREA)
        self.support_schedule_task = True
        self.support_multi_account = True
        self.config_description.update({
            self.CFG_SCROLL_ENABLE: "启用后在对齐滑索时会自动滚动放大视角\n可能会提高对齐成功率，但也可能导致对齐成功率下降较为明显\n建议启用此项时不要使用非白发或有白帽角色",
            self.CFG_DELIVERY_AREA: "通过下拉框切换送货地区配置",
            self.CFG_TEST_TARGET: "默认是无，表示正常执行相关任务\n也可以选择特定的滑索分叉序列来测试滑索功能\n选择完整循环测试则会依次测试每个送货目标的完整流程\n(需要锁定次要任务在送货任务上或附近)",
            self.CFG_ONLY_ACCEPT: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n仅接取当前地区委托，不送货',
            self.CFG_ONLY_DELIVER: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n接取当前地区委托后启动自动识别送货',
            self.CFG_FULL_CYCLE_LOCATION: "仅在“完整循环测试”时生效，用于限定测试的小区域（当前地区可选地点）",
            self.CFG_TUTORIAL: self.TUTORIAL_TIPS,
            "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
        })
        self.default_config.update(
            {
                self.CFG_TARGET_TICKET_NUM: "119000",
                **{x: "" for x in self.to_delivery_point_config_keys + self.ends},
                self.CFG_SCROLL_ENABLE: False,
                self.CFG_DELIVERY_AREA: self.delivery_area,
                self.CFG_ONLY_ACCEPT: False,
                self.CFG_ONLY_DELIVER: False,
                self.CFG_TEST_TARGET: self.TEST_NONE,
                self.CFG_FULL_CYCLE_LOCATION: self.full_cycle_locations[0],
                "发生异常时终止游戏": False,
            }
        )
        self.config_type[self.CFG_TUTORIAL] = {
            "type": "button",
            "text": "打开教程",
            "icon": FluentIcon.LINK,
            "callback": self.open_tutorial_link,
        }
        self.config_type[self.CFG_TEST_TARGET] = {
            "type": "drop_down",
            "options": [self.TEST_NONE] + self.to_delivery_point_config_keys + self.ends + [self.TEST_FULL_CYCLE],
        }
        self.config_type[self.CFG_DELIVERY_AREA] = {
            "type": "drop_down",
            "options": list(DELIVERY_AREA_CONFIG.keys()),
        }
        self.config_type[self.CFG_TARGET_TICKET_NUM] = {
            "type": "drop_down",
            "options": DELIVERY_TARGET_TICKET_NUM_OPTIONS,
        }
        self.config_type[self.CFG_FULL_CYCLE_LOCATION] = {
            "type": "drop_down",
            "options": self.full_cycle_locations,
        }
        self._accepted_delivery_location = None
        self._last_refresh_ts = 0
        self.try_time = 0
        self.add_exit_after_config()

    def open_tutorial_link(self, *_):
        webbrowser.open(self.TUTORIAL_LINK)

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
            16 / 9: (250 / 1080, 926 / 1080),  # 16:9 宽高比（主流显示器，如 1920x1080 / 2560x1440）
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
            str: 票券类型("ticket_delivery_area")或None
        """
        first_name = row.elems[0].name
        if extract_delivery_location(first_name, self.delivery_area):
            return "ticket_delivery_area"
        return None

    def _resolve_transfer_search_box(self, area_config):
        """将传送搜索配置解析为可用 box，支持 preset 与坐标两种配置。"""
        if area_config is None:
            return None
        if isinstance(area_config, str):
            return getattr(self.box, area_config, None)
        if not isinstance(area_config, dict):
            return None

        preset = area_config.get("preset")
        if preset:
            return getattr(self.box, preset, None)

        if all(k in area_config for k in ("x", "y", "to_x", "to_y")):
            return self.box_of_screen(
                area_config["x"],
                area_config["y"],
                area_config["to_x"],
                area_config["to_y"],
            )
        return None

    def _get_transfer_search_box_by_location(self, location_name):
        area_config = get_transfer_search_area(location_name, self.delivery_area)
        return self._resolve_transfer_search_box(area_config)

    def _remember_delivery_location(self, row: DeliveryRow):
        """从接取到的委托行里缓存地点信息，供后续传送点搜索复用。"""
        first_name = row.elems[0].name
        self._accepted_delivery_location = extract_delivery_location(first_name, self.delivery_area)
        if self._accepted_delivery_location:
            self.log_info(f"已缓存委托地点: {self._accepted_delivery_location}")
        else:
            self.log_info(f"未能从委托行识别地点，将直接判定送货失败: {first_name}")

    def _to_delivery_point_config_key(self, location_name: str | None) -> str:
        if location_name is None:
            return self.CFG_TO_DELIVERY_POINT
        default_location = self.full_cycle_locations[0] if self.full_cycle_locations else None
        if location_name == default_location:
            return self.CFG_TO_DELIVERY_POINT
        return f"{self.CFG_TO_DELIVERY_POINT}{location_name}"

    def _resolve_to_delivery_point_config_key(self) -> str | None:
        location_name = self._accepted_delivery_location
        if not location_name:
            self.log_info("未缓存委托地点，无法选择对应的送货点滑索配置")
            return None

        location_key = self._to_delivery_point_config_key(location_name)
        if self.config.get(location_key):
            return location_key

        self.log_info(f"委托地点({location_name})未配置送货点滑索参数: {location_key}")
        return None

    def other_run(self):
        """接取运输委托的主流程
        
        Returns:
            bool: 成功返回True，失败返回False
        """
        self.try_time = 0
        self.ensure_main(time_out=120)
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.to_model_area(get_task_model_area(self.delivery_area), "仓储节点")
        delivery_box = self.wait_ocr(match="运送委托列表", time_out=5)
        if delivery_box:
            self.click(delivery_box[0], move_back=True, after_sleep=0.5)
            self.switch_to_area_delivery_list(self.delivery_area)
        else:
            self.log_info("未找到‘运送委托列表’，退出")
            return False
        self.wait_ui_stable(refresh_interval=1)
        enable_delivery_area = True
        ticket_types = []
        if enable_delivery_area:
            ticket_types.append("ticket_delivery_area")

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return None
        start_time = time.time()
        while True:
            if time.time() - start_time > 600:
                self.log_info("接单尝试时间过长，退出")
                return False
            rows = self.merge_left_right_groups()
            for row in rows:
                if row:
                    ticket_type = self.detect_ticket_type(row)
                    if ticket_type == "ticket_delivery_area" and enable_delivery_area:
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
                            feature_list = get_accept_feature_labels(self.delivery_area, target_num)
                            if not feature_list:
                                self.log_info(f"当前地区({self.delivery_area})未配置目标券数({target_num})对应接单特征")
                                continue
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
                                self.try_time += 1
                                if self.try_time > 5:
                                    self.log_info("尝试次数过多，退出")
                                    return False
                                self.next_frame()
                                accepted_successfully = not self.wait_ocr(
                                    match="接取运送委托",
                                    box=self.box.bottom_right,
                                    time_out=1
                                )
                                if accepted_successfully:
                                    self._remember_delivery_location(row)
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
                    self.sleep(1.0)

    def to_storage_point_and_back_zip_line(self, only_zip_line=False):
        """从仓储点出发，乘坐滑索到送货点
        
        Args:
            only_zip_line: True时仅乘坐出发滑索，False时乘至仓储点
        
        Returns:
            bool: 成功返回True，失败返回False
        """
        if result := self.wait_ocr(match="登上滑索架", box=self.box.bottom_right, time_out=60, log=True):
            if self.wait_ocr(match="工业", box=self.box.top_left, time_out=2, log=True):
                self.press_key("tab", after_sleep=1)
            self.click_with_alt(result[0], after_sleep=2)
            to_delivery_point_key = self._resolve_to_delivery_point_config_key()
            if not to_delivery_point_key:
                return False
            zip_line_list = parse_int_sequence(self.config.get(to_delivery_point_key))
            if not zip_line_list:
                self.log_info(f"送货点滑索配置为空: {to_delivery_point_key}")
                return False
            self.zip_line_list_go(zip_line_list,
                                  need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                                  target=(secondary_objective_direction_dot, "feature"),
                                  need_v=True)  # 需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            if only_zip_line:
                return True
            for i in range(40):
                self.sleep(2)
                self.press_key("v")
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                    threshold=0.7,
                    only_x=True,
                    ocr=False,
                )
                self.move_keys(
                    "w",
                    1,
                )
                if self.wait_ocr(match="仓储节点", box=self.box.bottom_right, settle_time=1, time_out=2, log=True):
                    self.wait_click_ocr(
                        match=re.compile("取货"),
                        box=self.box.bottom_right,
                        time_out=2,
                        log=True,
                        after_sleep=2,
                        alt=True,
                        recheck_time=1
                    )
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
        for i in range(40):
            self.sleep(2)
            self.press_key("v", after_sleep=1)
            self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                threshold=0.8,
                only_x=True,
                ocr=False
            )
            self.move_keys(
                "w",
                0.5,
            )
            self.sleep(1)
            if end_pattern == re.compile("资源"):
                end_pattern = re.compile("交货")
            if self.wait_click_ocr(
                match=end_pattern,
                box=self.box.bottom_right,
                settle_time=1,
                time_out=2,
                log=True,
                after_sleep=2,
                alt=True,
            ):
                if not self.find_reward_ok():
                    self.skip_dialog()
                self.wait_pop_up(after_sleep=2)
                break

    def _resolve_transfer_point_search_box(self):
        """根据当前委托区域选择传送点搜索区域。"""
        cached_box = self._get_transfer_search_box_by_location(self._accepted_delivery_location)
        if cached_box:
            return cached_box
        if self._accepted_delivery_location:
            self.log_info(f"委托地点({self._accepted_delivery_location})未配置传送搜索区域，送货失败")
        return None

    def _try_recover_delivery_location_on_map(self):
        """地图已打开时，尝试从右上角地点信息回填委托地点缓存。"""
        if self._accepted_delivery_location:
            return False
        if not hasattr(self, "delivery_area"):
            return False

        try:
            delivery_locations = get_delivery_locations(self.delivery_area)
            if not delivery_locations:
                return False

            ocr_results = self.wait_ocr(
                match=delivery_locations,
                box=self.box.top_right,
                time_out=4,
                log=True,
            )
            if not ocr_results:
                return False

            location_text = str(getattr(ocr_results[0], "name", "")).strip()
            if not location_text:
                return False

            location_name = extract_delivery_location(location_text, self.delivery_area)
            if not location_name:
                return False

            self._accepted_delivery_location = location_name
            self.log_info(f"通过地图自动回填送货地点: {location_name}")
            return True
        except Exception:
            return False

    def _resolve_transfer_point_search_box_after_map_open(self, fallback_box):
        """地图打开后重新解析传送点搜索区域，便于后续扩展或回填逻辑插入。"""
        if not self._accepted_delivery_location:
            self._try_recover_delivery_location_on_map()
        return self._resolve_transfer_point_search_box() or fallback_box

    def _run_single_delivery_cycle(self):
        if self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE:
            ends_list_pattern_dict = {}
            for end in self.ends:
                pattern = get_delivery_target_ocr_pattern(self.delivery_area, end)
                ends_list_pattern_dict[pattern] = end
            for _ in range(3):
                self._accepted_delivery_location = None
                if not self._logged_in:
                    self.ensure_main(time_out=600)
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
                        self.wait_click_ocr(
                            match=re.compile("送达"),
                            box=self.box.bottom_right,
                            settle_time=4,
                            time_out=10,
                            after_sleep=10,
                            log=True,
                        )
                    success = None
                    for attempt in range(3):
                        success = self.task_to_transfer_point(
                            search_box_resolver=self._resolve_transfer_point_search_box_after_map_open,
                        )
                        if success:
                            break
                    if not success:
                        if self._accepted_delivery_location:
                            self.log_info("未能确定传送点搜索区域（地点未缓存或配置缺失），终止本轮送货")
                        else:
                            self.log_info("未缓存委托地点，送货失败")
                        return
                    if not self.to_storage_point_and_back_zip_line():
                        return
                    results = self.wait_ocr(
                        match=list(ends_list_pattern_dict.keys()), box=self.box.left, time_out=10, log=True
                    )
                    self.wait_click_ocr(
                        match="登上滑索架",
                        box=self.box.bottom_right,
                        time_out=2,
                        log=True,
                        after_sleep=2,
                        alt=True,
                    )
                    end_pattern = None
                    if not results:
                        raise Exception("未识别到送货目标")

                    for result in results:
                        for pattern in ends_list_pattern_dict:
                            m = pattern.search(result.name)
                            if m:
                                end_pattern = pattern
                                self.on_zip_line_start(
                                    ends_list_pattern_dict[pattern],
                                    need_scroll=self.config.get(self.CFG_SCROLL_ENABLE),
                                    target=(secondary_objective_direction_dot, "feature")
                                )
                                break
                    self.to_end_and_submit(end_pattern)
                    if self.config.get(self.CFG_ONLY_DELIVER):
                        break
        elif self.config.get(self.CFG_TEST_TARGET) == self.TEST_FULL_CYCLE:
            test_location = self.config.get(self.CFG_FULL_CYCLE_LOCATION)
            full_cycle_targets = get_full_cycle_targets(self.delivery_area, test_location)
            if not full_cycle_targets:
                self.log_info(f"未配置测试区域({test_location})的送货目标")
                return
            self._accepted_delivery_location = test_location
            for end in full_cycle_targets:
                transfer_search_box = self._get_transfer_search_box_by_location(test_location) or self.box.bottom
                self.task_to_transfer_point(self.box.bottom)
                self.to_storage_point_and_back_zip_line(only_zip_line=True)
                if self.wait_click_ocr(
                    match="登上滑索架",
                    box=self.box.bottom_right,
                    settle_time=1,
                    time_out=2,
                    log=True,
                    after_sleep=2,
                    alt=True,
                ):
                    self.log_info("未找到登上滑索架，测试失败")
                    self.on_zip_line_start(
                        end, need_v=False,
                        need_scroll=self.config.get(self.CFG_SCROLL_ENABLE)
                        )
                    self.sleep(2)
        else:
            zip_line_list_str = self.config.get(self.config.get(self.CFG_TEST_TARGET))
            if zip_line_list_str:
                zip_line_list = parse_int_sequence(zip_line_list_str)
                self.zip_line_list_go(zip_line_list, need_scroll=self.config.get(self.CFG_SCROLL_ENABLE))

    def run(self):
        """运输委托任务的主入口，支持与日常任务一致的多账号执行逻辑。"""
        try:
            current_area = self.config.get(self.CFG_DELIVERY_AREA, DEFAULT_DELIVERY_AREA)
            if current_area not in DELIVERY_AREA_CONFIG:
                self.log_info(f"配置的地区({current_area})无效，回退为默认地区({DEFAULT_DELIVERY_AREA})")
                current_area = DEFAULT_DELIVERY_AREA
            if current_area != self.delivery_area:
                self._configure_delivery_area(current_area)
                if self.config.get(self.CFG_FULL_CYCLE_LOCATION) not in self.full_cycle_locations:
                    self.config[self.CFG_FULL_CYCLE_LOCATION] = self.full_cycle_locations[0]
                self.config_type[self.CFG_TEST_TARGET]["options"] = (
                    [self.TEST_NONE] + self.to_delivery_point_config_keys + self.ends + [self.TEST_FULL_CYCLE]
                )
                self.config_type[self.CFG_FULL_CYCLE_LOCATION]["options"] = self.full_cycle_locations
                for key in self.to_delivery_point_config_keys + self.ends:
                    self.config.setdefault(key, "")
            allow_multi = (
                self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE
                and not self.config.get(self.CFG_ONLY_ACCEPT)
                and not self.config.get(self.CFG_ONLY_DELIVER)
            )
            for repeat_idx, repeat_times in self.iter_multi_account_context(
                repeat_times=1,
                empty_accounts_message="多账户模式已开启，但账号列表为空，自动送货任务结束",
                account_log_suffix="自动送货",
                allow_multi_account=allow_multi,
            ):
                self._run_single_delivery_cycle()

        except Exception as e:
            self.handle_task_exception(e, 'DeliveryTask_Exception')
