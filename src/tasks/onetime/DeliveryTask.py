import re
import webbrowser
from dataclasses import dataclass
from typing import List, Tuple

from ok import Box
from qfluentwidgets import FluentIcon

from src.icons import Icons
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
from src.core.sequence_parser import parse_int_sequence
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

secondary_objective_direction_dot = [
    fL.secondary_objective_direction_dot,
    fL.secondary_objective_direction_dot_light,
    fL.secondary_objective_direction_dot_light_two,
    fL.secondary_objective_direction_dot_light_three,
    fL.secondary_objective_direction_dot_light_fourth
]


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

    requires_foreground = True  # 送货需要移动/导航

    # 配置键名常量
    CFG_TARGET_TICKET_NUM = "目标券数"
    CFG_TEST_TARGET = "选择测试对象"
    CFG_ONLY_ACCEPT = "仅接取"
    CFG_ONLY_DELIVER = "仅送货"
    CFG_TUTORIAL = "教程"
    CFG_DELIVERY_AREA = "地区切换"
    CFG_TO_DELIVERY_POINT = "通向武陵城送货点"
    CFG_FULL_CYCLE_LOCATION = "完整循环测试区域"
    TUTORIAL_LINK = "https://www.bilibili.com/video/BV1LLc7zFEF9"
    TUTORIAL_TIPS = "游戏内开启全屏模式时请确保游戏内分辨率与你的屏幕分辨率一致"

    # 滑索配置键迁移：旧键 → 新键
    config_key_migrations = {
        "通向送货点": "通向武陵城送货点",
        "通向送货点试验园区": "通向试验园区送货点",
    }

    account_config_blacklist = {
        CFG_TEST_TARGET,
        CFG_ONLY_ACCEPT,
        CFG_ONLY_DELIVER,
        CFG_FULL_CYCLE_LOCATION,
        "发生异常时终止游戏",
        "Exit After Task",
    }

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({"_enabled": True})
        self.name = "自动送货"
        self.icon = Icons.Deliver
        self.group_name = "运输委托"
        self.description = "根据地区配置自动送货，教程视频 BV1LLc7zFEF9"

        self._configure_delivery_area(DEFAULT_DELIVERY_AREA)
        self.support_schedule_task = True
        self.support_multi_account = True
        self.config_description.update({
            self.CFG_DELIVERY_AREA: "通过下拉框切换送货地区配置",
            self.CFG_TEST_TARGET: "默认是无，表示正常执行相关任务\n也可以选择特定的滑索分叉序列来测试滑索功能\n选择完整循环测试则会依次测试每个送货目标的完整流程\n(需要锁定次要任务在送货任务上或附近)",
            self.CFG_ONLY_ACCEPT: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n仅接取当前地区委托，不送货',
            self.CFG_ONLY_DELIVER: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n接取当前地区委托后启动自动识别送货',
            self.CFG_TARGET_TICKET_NUM: "目标券数优先级序列，用逗号分隔多个券数。按列表中顺序优先抢前面的券数。\n默认：119000。可选：73100、79800、119000、159000、163000",
            self.CFG_FULL_CYCLE_LOCATION: "仅在“完整循环测试”时生效，用于限定测试的小区域（当前地区可选地点）",
            self.CFG_TUTORIAL: self.TUTORIAL_TIPS,
            "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
        })
        self.default_config.update(
            {
                self.CFG_TARGET_TICKET_NUM: ["119000"],
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
            "sub_configs": {
                self.TEST_NONE: [self.CFG_ONLY_ACCEPT, self.CFG_ONLY_DELIVER],
                self.TEST_FULL_CYCLE: [self.CFG_FULL_CYCLE_LOCATION],
            },
        }
        self.config_type[self.CFG_DELIVERY_AREA] = {
            "type": "drop_down",
            "options": list(DELIVERY_AREA_CONFIG.keys()),
        }
        self.config_type[self.CFG_TARGET_TICKET_NUM] = {
            "options_available": DELIVERY_TARGET_TICKET_NUM_OPTIONS,
            "allow_duplication": False,
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
                f"{k:.3f} -> {v}" for k, v in screen_scale_desc.items()
            )
            raise ValueError(
                f"不支持的屏幕比例: {ratio:.3f}（当前分辨率: {self.width}x{self.height}）。"
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
        start_time = self.active_time()

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
            if self.active_time() - start_time > 2:
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
            g for g in split_items_by_marker(left_items, self.lang.DeliveryTask.k_view_location) if len(g) >= 2
        ]

        right_groups = [
            g for g in split_items_by_marker(right_items, self.lang.DeliveryTask.k_accept_delivery) if len(g) >= 2
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
        if extract_delivery_location(first_name, self.delivery_area, self.lang):
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
        self._accepted_delivery_location = extract_delivery_location(first_name, self.delivery_area, self.lang)
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
        return f"通向{location_name}送货点"

    def _resolve_to_delivery_point_config_key(self) -> str | None:
        location_name = self._accepted_delivery_location
        if not location_name:
            self.log_info("未缓存委托地点，无法选择对应的送货点滑索配置")
            return None

        location_key = self._to_delivery_point_config_key(location_name)
        if self.get_zip_line_config_value(location_key):
            return location_key

        self.log_info(f"委托地点({location_name})未配置送货点滑索参数: {location_key}")
        return None

    def _find_accept_order_results(self, target_nums, box):
        has_configured_feature = False
        for target_num in target_nums:
            labels = get_accept_feature_labels(self.delivery_area, str(target_num))
            if labels:
                has_configured_feature = True
            for feature_name in labels:
                results = self.find_feature(
                    feature=feature_name,
                    box=box,
                    threshold=0.98,
                )
                if results:
                    return results, has_configured_feature
        return [], has_configured_feature

    def accept_order(self):
        """接取运输委托的主流程

        Returns:
            bool: 成功返回True，失败返回False
        """
        self.try_time = 0
        self.ensure_main(time_out=120)
        self.log_info("前置操作：按Y，点击‘仓储节点’，点击‘运送委托列表’")
        self.to_model_area(self.config.get(self.CFG_DELIVERY_AREA), "仓储节点")
        delivery_box = self.wait_ocr(match=self.lang.DeliveryTask.k_ae8fb114, time_out=5)
        if delivery_box:
            self.click(delivery_box[0])
            self.switch_to_area_delivery_list(self.delivery_area)
        else:
            self.log_info("未找到‘运送委托列表’，退出")
            return False
        self.wait_ui_stable(refresh_interval=1)
        ticket_types = ["ticket_delivery_area"]

        if not ticket_types:
            self.log_info("警告: 未启用任何券种，任务退出")
            return None
        self.active_and_send_mouse_delta(0, self.height//2-20, activate=False)
        start_time = self.active_time()
        while True:
            if self.active_time() - start_time > 600:
                self.log_info("接单尝试时间过长，退出")
                return False
            target_nums = parse_int_sequence(self.config.get(self.CFG_TARGET_TICKET_NUM, ["119000"]))
            box = self.box_of_screen(
                0.654, 0.230, 0.699, 0.900
            )
            results, has_configured_feature = self._find_accept_order_results(target_nums, box)
            if not has_configured_feature:
                self.log_info(f"当前地区({self.delivery_area})未配置目标券数({target_nums})对应接单特征")
                return False
            if results:
                for result in results:
                    self.click(
                        (result.x + result.width)/self.width + (0.873 - 0.691),
                        (result.y + result.height)/self.height,
                        down_time=0.1,
                    )
                    self.log_info("疑似已经接取委托")
                    self.try_time += 1
                    if self.try_time > 5:
                        self.log_info("尝试次数过多，退出")
                        return False
                    accepted_successfully = self.wait_click_feature(
                        feature=fL.get_exchange_ticket,
                        time_out=3,
                        raise_if_not_found=False,
                    ) or (
                        self.wait_feature(feature=fL.one_task_to_map)
                    )
                    if accepted_successfully:
                        self.log_info("接取成功")
                        return True
                    else:
                        self.log_info("接取失败，可能委托被抢了，继续寻找")
            self.log_info("未找到符合条件(金额+类型)的委托，准备刷新重试")
            refresh_btn_start = self.active_time()
            while True:
                if last_refresh_box := self.wait_feature(feature=fL.refresh_order_list, vertical_variance=0.05, horizontal_variance=0.02):
                    now = self.active_time()
                    last = getattr(self, "_last_refresh_ts", 0.0)
                    wait = max(0.0, 5.4 - (now - last))
                    if wait > 0:
                        self.sleep(wait)
                    self.click(last_refresh_box)
                    self._last_refresh_ts = self.active_time()
                    self.wait_ui_stable(refresh_interval=1)  # 刷新后界面稳定的时间可能会比平常长一些，尤其是网络较慢的时候
                    break
                if self.active_time() - refresh_btn_start > 10:
                    self.log_info("长时间未定位到刷新按钮，无法刷新，结束任务")
                    return False
                self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                self.sleep(1.0)

    def _find_zip_line_board_button(self, direct_wait=5.0, total_time_out=60.0):
        """传送落地后确保处于主界面，再寻找「登上滑索架」交互按钮。

        分三个阶段：
        1. 直接原地查找约 ``direct_wait`` 秒；
        2. 未找到则切换步行模式做 WASD 小幅度踱步搜索，最多约 10 秒；
        3. 仍未找到则改为仅 W/S 前后移动继续搜索，直到总超时。
        移动全程保持步行而非奔跑（ctrl 切换），结束后恢复奔跑模式。

        Args:
            direct_wait: 直接原地查找的时长（秒）。
            total_time_out: 含直接查找在内的总超时（秒）。

        Returns:
            Box | None: 找到的「登上滑索架」按钮位置；超时返回 None。
        """
        self.ensure_main()  # 传送后先确保回到主界面再尝试登滑索架
        match = self.lang.DeliveryTask.k_b0e3a2da  # 「登上滑索架」按钮 OCR 文本
        box = self.box.bottom_right  # 交互按钮固定出现在右下角提示区
        start = self.active_time()
        deadline = start + max(0.0, total_time_out)  # 统一总截止时间

        def check():
            frame = self.next_frame()  # 移动或等待后必须取新帧再识别
            results = self.ocr(match=match, box=box, frame=frame, log=True)
            return results[0] if results else None  # 命中返回按钮位置

        # 阶段一：直接原地查找一小段时间（正常情况下按钮很快出现），
        # 但同样受总截止时间约束（direct_wait 大于剩余预算时提前截断）
        while self.active_time() < min(start + direct_wait, deadline):
            if found := check():
                return found
            self.sleep(0.1)

        remaining = deadline - self.active_time()
        if remaining <= 0:  # 严格遵守总截止时间：剩余不足时不再进入移动搜索
            self.log_info("总等待时间已耗尽，仍未找到登上滑索架")
            return None

        # 切换步行模式：踱步与前后移动都用走路，避免奔跑错过交互按钮
        self.press_key("ctrl")  # 确认使用send_key：ctrl为奔跑切换键，不属于游戏可配置热键
        try:
            # 阶段二：WASD 踱步寻找（参考 nav 目标不稳定时的做法），最多持续约 10 秒
            self.log_info("短时间内未找到登上滑索架，可能被其他设备遮挡，切换步行开始踱步寻找（最长 10 秒）")
            found = self.strafe_search(
                check,
                passes=None,  # 不限轮数，持续踱步直到找到或阶段时限
                duration=0.2,  # 每个方向轻移 0.2 秒，避免走远
                keys=("s", "w", "a", "d"),  # 后退优先：落点常越过滑索架，后退最容易重新看到
                time_out=min(10.0, remaining),
            )
            if found:
                self.log_info("踱步寻找过程中找到登上滑索架")
                return found

            # 阶段三：改为仅前后移动继续找，直到总超时
            remaining = deadline - self.active_time()
            if remaining <= 0:
                self.log_info("踱步超时，仍未找到登上滑索架")
                return None
            self.log_info("踱步仍未找到登上滑索架，改为仅 W/S 前后移动继续寻找")
            found = self.strafe_search(
                check,
                passes=None,
                duration=0.2,
                keys=("s", "w"),  # 仅前后移动，同样后退优先
                time_out=remaining,
            )
            if found:
                self.log_info("前后移动寻找过程中找到登上滑索架")
                return found
            self.log_info("前后移动超时，仍未找到登上滑索架")
            return None
        finally:
            self.press_key("ctrl", after_sleep=0.01)  # 结束后恢复奔跑模式（与导航结束时处理一致）
            self.log_info("恢复奔跑模式")

    def to_storage_point_and_back_zip_line(self, only_zip_line=False):
        """从仓储点出发，乘坐滑索到送货点

        Args:
            only_zip_line: True时仅乘坐出发滑索，False时乘至仓储点

        Returns:
            bool: 成功返回True，失败返回False
        """
        result = self._find_zip_line_board_button(total_time_out=120)  # 主界面确认 + 找登滑索架按钮（含踱步兜底），总超时沿用 120s
        if result:
            if self.wait_ocr(match=self.lang.DeliveryTask.k_96b876e3, box=self.box.top_left, time_out=2, log=True):
                self.press_key("tab")
                self.wait_until(
                    lambda: not self.ocr(
                        match=self.lang.DeliveryTask.k_96b876e3,
                        box=self.box.top_left,
                    ),
                    time_out=2,
                    raise_if_not_found=False,
                )
            self.click_with_alt(result, after_sleep=2)  # result 已是单个按钮 Box（踱步搜索返回值）
            to_delivery_point_key = self._resolve_to_delivery_point_config_key()
            if not to_delivery_point_key:
                return False
            zip_line_list = parse_int_sequence(self.get_zip_line_config_value(to_delivery_point_key))
            if not zip_line_list:
                self.log_info(f"送货点滑索配置为空: {to_delivery_point_key}")
                return False
            self.zip_line_list_go(zip_line_list,
                                  need_scroll=self.zip_line_scroll_enabled(),
                                  target=(secondary_objective_direction_dot, "feature"),
                                  need_v=True)  # 需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            if only_zip_line:
                return True
            self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=secondary_objective_direction_dot,
                threshold=0.8,
                only_x=True,
                ocr=False,
                raise_if_fail=False,
            )
            self.send_key("v", after_sleep=0.5)  # 确认使用send_key：v为追踪键，在导航循环中用于重置视野，属于高频重复操作避免经过KeyConfigManager
            if not self.navigate_until_target(
                target=fL.receive_good,
                target_is_ocr=False,
                target_vertical_variance=0.06,
            ):
                self.log_info("未能到达送货点，取货失败")
                return False
            if not self.wait_click_feature(feature=fL.receive_good, time_out=10, raise_if_not_found=False, alt=True):
                self.log_info("未能识别到取货界面，取货失败")
                return False
            self.strafe_search(
                lambda: self.wait_ocr(
                    match=self.lang.DeliveryTask.k_b0e3a2da,
                    box=self.box.bottom_right,
                    time_out=2,
                    log=True,
                ),
                keys=("s",),
                duration=0.5,
                passes=None,
            )
            return True
        return False

    def to_end_and_submit(self, end_pattern):
        """从仓储点出发到目标点并提交委托

        Args:
            end_pattern: 目标点的正则匹配模式
        """
        if end_pattern == self.lang.DeliveryTask.k_6536f6f1:
            end_pattern = self.lang.DeliveryTask.k_0c1ef9f5
        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=secondary_objective_direction_dot,
            threshold=0.8,
            only_x=True,
            ocr=False,
            raise_if_fail=False,
        )
        self.send_key("v", after_sleep=0.5)  # 确认使用send_key：v为追踪键，在导航循环中用于重置视野，属于高频重复操作避免经过KeyConfigManager
        self.navigate_until_target(
            target=end_pattern,
            target_is_ocr=True,
            nav=secondary_objective_direction_dot,
            box=self.box_of_screen(0.676, 0.576, 0.752, 0.746),
            need_v=True,
        )
        if self.wait_click_ocr(
            match=end_pattern,
            box=self.box.bottom_right,
            settle_time=1,
            time_out=2,
            log=True,
            alt=True,
        ):
            self.skip_dialog(time_out=5)
            self.ensure_main()


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
            delivery_locations = get_delivery_locations(self.delivery_area, self.lang)
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
        if getattr(self, "_daily_delivery_mode", False) or self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE:
            ends_list_pattern_dict = {}
            for end in self.ends:
                pattern = get_delivery_target_ocr_pattern(self.delivery_area, end, self.lang)
                ends_list_pattern_dict[pattern] = end
            for _ in range(3):
                self._accepted_delivery_location = None
                self.location = None
                if not self._logged_in:
                    self.ensure_main(time_out=600)
                else:
                    self.ensure_main()
                self.back()
                self.ensure_main()
                if self.config.get(self.CFG_ONLY_ACCEPT):
                    self.accept_order()
                    break
                else:
                    if not self.config.get(self.CFG_ONLY_DELIVER):
                        if not self.accept_order():
                            return
                    success = None
                    for attempt in range(3):
                        success = self.task_to_transfer_point(
                            need_location_list=get_delivery_locations(self.delivery_area, self.lang),
                        )
                        if success:
                            break
                    if not success:
                        self.log_info("传送失败（未找到传送按钮），终止本轮送货")
                        return
                    # 缓存为空时用地图上记录的地区回填（覆盖仅送货模式等未接取委托的场景）
                    if not self._accepted_delivery_location and self.location:
                        self._accepted_delivery_location = extract_delivery_location(
                            self.location, self.delivery_area, self.lang
                        )
                        if self._accepted_delivery_location:
                            self.log_info(f"通过地图自动回填送货地点: {self._accepted_delivery_location}")
                    if not self.to_storage_point_and_back_zip_line():
                        return
                    results = self.wait_ocr(
                        match=list(ends_list_pattern_dict.keys()), box=self.box.left, time_out=10, log=True
                    )
                    self.wait_click_ocr(
                        match=self.lang.DeliveryTask.k_b0e3a2da,
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
                                     need_scroll=self.zip_line_scroll_enabled(),
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
                self.task_to_transfer_point()
                self.to_storage_point_and_back_zip_line(only_zip_line=True)
                if self.wait_click_ocr(
                        match=self.lang.DeliveryTask.k_b0e3a2da,
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
                        need_scroll=self.zip_line_scroll_enabled()
                    )
                    self.sleep(2)
        else:
            zip_line_list_str = self.get_zip_line_config_value(self.config.get(self.CFG_TEST_TARGET))
            if zip_line_list_str:
                zip_line_list = parse_int_sequence(zip_line_list_str)
                self.zip_line_list_go(
                    zip_line_list,
                    need_scroll=self.zip_line_scroll_enabled(),
                )

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


class DeliveryFeature(DeliveryTask):
    """日常任务使用的自动送货功能。"""

    DAILY_ENABLE_KEY = "⭐自动送货"

    def __init__(self, task):
        self._task = task
        self._daily_delivery_mode = False
        self._accepted_delivery_location = None
        self._last_refresh_ts = 0
        self.try_time = 0
        self._configure_delivery_area(DEFAULT_DELIVERY_AREA)

        task.default_config.update({
            self.DAILY_ENABLE_KEY: False,
            self.CFG_TARGET_TICKET_NUM: ["119000"],
            self.CFG_DELIVERY_AREA: DEFAULT_DELIVERY_AREA,
        })
        task.config_description.update({
            self.DAILY_ENABLE_KEY: "是否执行自动接取并完成运输委托。",
            self.CFG_TARGET_TICKET_NUM: (
                "目标券数优先级序列，用逗号分隔多个券数。"
            ),
            self.CFG_DELIVERY_AREA: "通过下拉框切换送货地区配置。",
        })
        task.config_type[self.CFG_DELIVERY_AREA] = {
            "type": "drop_down",
            "options": list(DELIVERY_AREA_CONFIG.keys()),
        }
        task.config_type[self.CFG_TARGET_TICKET_NUM] = {
            "options_available": DELIVERY_TARGET_TICKET_NUM_OPTIONS,
            "allow_duplication": False,
        }
        task.default_config_group.update({
            self.DAILY_ENABLE_KEY: [self.CFG_TARGET_TICKET_NUM, self.CFG_DELIVERY_AREA],
        })

    def __getattr__(self, name):
        return getattr(self._task, name)

    def run_daily(self):
        """执行一轮日常自动送货，由 DailyTask 负责多账号循环。"""
        current_area = self.config.get(self.CFG_DELIVERY_AREA, DEFAULT_DELIVERY_AREA)
        if current_area not in DELIVERY_AREA_CONFIG:
            current_area = DEFAULT_DELIVERY_AREA
        if current_area != self.delivery_area:
            self._configure_delivery_area(current_area)

        self._daily_delivery_mode = True
        try:
            self._run_single_delivery_cycle()
            return True
        finally:
            self._daily_delivery_mode = False
