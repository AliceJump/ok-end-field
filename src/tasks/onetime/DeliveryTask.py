import webbrowser
from enum import Enum, auto

from qfluentwidgets import FluentIcon

from src.core.sequence_parser import parse_int_sequence
from src.data.delivery_area import (
    DEFAULT_DELIVERY_AREA,
    DELIVERY_AREA_CONFIG,
    DELIVERY_TARGET_TICKET_NUM_OPTIONS,
)
from src.data.delivery_area_service import (
    extract_delivery_location,
    get_accept_feature_labels,
    get_delivery_location_coordinate,
    get_delivery_locations,
    get_delivery_target_coordinate,
    get_delivery_target_ocr_pattern,
    get_delivery_targets,
    get_full_cycle_targets,
)
from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.account.account_mixin import AccountMixin
from src.tasks.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

secondary_objective_direction_dot = [
    fL.secondary_objective_direction_dot,
    fL.secondary_objective_direction_dot_light,
    fL.secondary_objective_direction_dot_light_two,
    fL.secondary_objective_direction_dot_light_three,
    fL.secondary_objective_direction_dot_light_fourth,
]


class DeliveryNavigationMode(Enum):
    """取货和送达阶段的到达方式。"""

    LEGACY = auto()
    GRID = auto()


class DeliveryPhase(Enum):
    """网格导航送货状态机的阶段。"""

    NAVIGATE_TO_PICKUP = auto()
    PICKUP = auto()
    RECOGNIZE_DESTINATION = auto()
    NAVIGATE_TO_DESTINATION = auto()
    SUBMIT = auto()
    DONE = auto()
    FAILED = auto()


class DeliveryTask(AccountMixin, ZipLineMixin, GridNavigationMixin, MapMixin):
    """运输委托自动化任务类 - 处理游戏中的送货操作"""

    # 配置键名常量
    CFG_TARGET_TICKET_NUM = "目标券数"
    CFG_TEST_TARGET = "选择测试对象"
    CFG_ARRIVAL_MODE = "到达方式"
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
    ARRIVAL_MODE_LEGACY = "原流程"
    ARRIVAL_MODE_GRID = "网格导航"

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
        self._init_grid_navigation_mixin()
        self.default_config.update({"_enabled": True})
        self.name = "自动送货"
        self.icon = Icons.Deliver
        self.group_name = "运输委托"
        self.description = "根据地区配置自动送货，教程视频 BV1LLc7zFEF9"

        self._configure_delivery_area(DEFAULT_DELIVERY_AREA)
        self.support_schedule_task = True
        self.support_multi_account = True
        self.config_description.update(
            {
                self.CFG_DELIVERY_AREA: "通过下拉框切换送货地区配置",
                self.CFG_TEST_TARGET: "默认是无，表示正常执行相关任务\n也可以选择特定的滑索分叉序列来测试滑索功能\n选择完整循环测试则会依次测试每个送货目标的完整流程\n(需要锁定次要任务在送货任务上或附近)",
                self.CFG_ARRIVAL_MODE: (
                    "选择取货和送达阶段的到达方式。\n"
                    "原流程：使用原有滑索距离序列和蓝色标记搜索。\n"
                    "网格导航：按取货点/终点坐标调用小地图网格导航，自动组合滑索和寻路。"
                ),
                self.CFG_ONLY_ACCEPT: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n仅接取当前地区委托，不送货',
                self.CFG_ONLY_DELIVER: f'前置是选择测试对象部分选择"{self.TEST_NONE}"\n接取当前地区委托后启动自动识别送货',
                self.CFG_TARGET_TICKET_NUM: "目标券数优先级序列，用逗号分隔多个券数。按列表中顺序优先抢前面的券数。\n默认：119000。可选：73100、79800、119000、159000、163000",
                self.CFG_FULL_CYCLE_LOCATION: "仅在“完整循环测试”时生效，用于限定测试的小区域（当前地区可选地点）",
                self.CFG_TUTORIAL: self.TUTORIAL_TIPS,
                "发生异常时终止游戏": "勾选这个选项：如果「完成后退出」被选定，那么抛出异常也会退出游戏和App。",
            }
        )
        self.default_config.update(
            {
                self.CFG_TARGET_TICKET_NUM: ["119000"],
                self.CFG_DELIVERY_AREA: self.delivery_area,
                self.CFG_ONLY_ACCEPT: False,
                self.CFG_ONLY_DELIVER: False,
                self.CFG_TEST_TARGET: self.TEST_NONE,
                self.CFG_ARRIVAL_MODE: self.ARRIVAL_MODE_LEGACY,
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
        self.config_type[self.CFG_ARRIVAL_MODE] = {
            "type": "drop_down",
            "options": [self.ARRIVAL_MODE_LEGACY, self.ARRIVAL_MODE_GRID],
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
        self.active_and_send_mouse_delta(0, self.height // 2 - 20, activate=False)
        start_time = self.active_time()
        while True:
            if self.active_time() - start_time > 600:
                self.log_info("接单尝试时间过长，退出")
                return False
            target_nums = parse_int_sequence(self.config.get(self.CFG_TARGET_TICKET_NUM, ["119000"]))
            box = self.box_of_screen(0.654, 0.230, 0.699, 0.900)
            results, has_configured_feature = self._find_accept_order_results(target_nums, box)
            if not has_configured_feature:
                self.log_info(f"当前地区({self.delivery_area})未配置目标券数({target_nums})对应接单特征")
                return False
            if results:
                for result in results:
                    self.click(
                        (result.x + result.width) / self.width + (0.873 - 0.691),
                        (result.y + result.height) / self.height,
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
                    ) or (self.wait_feature(feature=fL.one_task_to_map))
                    if accepted_successfully:
                        self.log_info("接取成功")
                        return True
                    else:
                        self.log_info("接取失败，可能委托被抢了，继续寻找")
            self.log_info("未找到符合条件(金额+类型)的委托，准备刷新重试")
            refresh_btn_start = self.active_time()
            while True:
                if last_refresh_box := self.wait_feature(
                    feature=fL.refresh_order_list, vertical_variance=0.05, horizontal_variance=0.02
                ):
                    now = self.active_time()
                    last = getattr(self, "_last_refresh_ts", 0.0)
                    wait = max(0.0, 5.4 - (now - last))
                    if wait > 0:
                        self.sleep(wait)
                    self.click(last_refresh_box)
                    self._last_refresh_ts = self.active_time()
                    self.wait_ui_stable(
                        refresh_interval=1
                    )  # 刷新后界面稳定的时间可能会比平常长一些，尤其是网络较慢的时候
                    break
                if self.active_time() - refresh_btn_start > 10:
                    self.log_info("长时间未定位到刷新按钮，无法刷新，结束任务")
                    return False
                self.log_info("警告: 尚未定位到刷新按钮位置，无法刷新，重试...")
                self.sleep(1.0)

    def to_storage_point_and_back_zip_line(self, only_zip_line=False):
        """从仓储点出发，乘坐滑索到送货点

        Args:
            only_zip_line: True时仅乘坐出发滑索，False时乘至仓储点

        Returns:
            bool: 成功返回True，失败返回False
        """
        result = self._find_zip_line_board_button(
            total_time_out=120
        )  # 主界面确认 + 找登滑索架按钮（含踱步兜底），总超时沿用 120s
        if result:
            if self.wait_ocr(match=self.lang.DeliveryTask.k_96b876e3, box=self.box.top_left, time_out=2, log=True):
                self.press_key("tab")
                self.wait_until(
                    lambda: (
                        not self.ocr(
                            match=self.lang.DeliveryTask.k_96b876e3,
                            box=self.box.top_left,
                        )
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
            self.zip_line_list_go(
                zip_line_list,
                need_scroll=self.zip_line_scroll_enabled(),
                target=(secondary_objective_direction_dot, "feature"),
                need_v=True,
            )  # 需要在配置里指定出发点的滑索距离,这里默认是36m的滑索
            if only_zip_line:
                return True
            return self._legacy_pickup_search()
        return False

    def to_end_and_submit(self, end_pattern):
        """从仓储点出发到目标点并提交委托。

        Args:
            end_pattern: 目标点的正则匹配模式

        Returns:
            bool: 成功点击提交并回到主界面时返回 True。
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
        self.send_key(
            "v", after_sleep=0.5
        )  # 确认使用send_key：v为追踪键，在导航循环中用于重置视野，属于高频重复操作避免经过KeyConfigManager
        if not self.navigate_until_target(
            target=end_pattern,
            target_is_ocr=True,
            nav=secondary_objective_direction_dot,
            box=self.box_of_screen(0.676, 0.576, 0.752, 0.746),
            need_v=True,
        ):
            self.log_warning("未能到达送货目标")
            return False
        if not self.wait_click_ocr(
            match=end_pattern,
            box=self.box.bottom_right,
            settle_time=1,
            time_out=2,
            log=True,
            alt=True,
        ):
            self.log_warning("未能识别到提交按钮")
            return False
        self.skip_dialog(time_out=5)
        self.ensure_main()
        return True

    def _arrival_mode(self) -> DeliveryNavigationMode:
        value = str(
            self.config.get(
                self.CFG_ARRIVAL_MODE,
                self.ARRIVAL_MODE_LEGACY,
            )
            or ""
        ).strip()
        return DeliveryNavigationMode.GRID if value == self.ARRIVAL_MODE_GRID else DeliveryNavigationMode.LEGACY

    def _delivery_end_patterns(self) -> dict:
        return {get_delivery_target_ocr_pattern(self.delivery_area, end, self.lang): end for end in self.ends}

    def _navigate_delivery_coordinate(self, coordinate, label: str) -> bool:
        if coordinate is None:
            self.log_warning(f"缺少{label}坐标，无法使用网格导航")
            return False
        goal = (float(coordinate[0]), float(coordinate[2]))
        self.log_info(f"网格导航前往{label}: ({goal[0]:.2f}, {goal[1]:.2f})")
        if not self.navigate_grid_to(goal):
            self.log_warning(f"网格导航未能到达{label}")
            return False
        return True

    def _legacy_pickup_search(self) -> bool:
        """保留原蓝色标记对齐和前向搜索逻辑，作为取货直判失败后的回退。"""
        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=secondary_objective_direction_dot,
            threshold=0.8,
            only_x=True,
            ocr=False,
            raise_if_fail=False,
        )
        self.send_key(
            "v", after_sleep=0.5
        )  # 确认使用send_key：v为追踪键，在导航循环中用于重置视野，属于高频重复操作避免经过KeyConfigManager
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

    def _pickup_receive_good_with_fallback(self) -> bool:
        direct = self.find_feature(
            feature=fL.receive_good,
            threshold=0.7,
            vertical_variance=0.06,
        )
        if direct:
            result = direct[0] if isinstance(direct, list) else direct
            self.click_with_alt(result, after_sleep=2)
            self.log_info("已直接点击取货")
            return True
        self.log_info("未直接找到取货模板，回退原取货搜索")
        return self._legacy_pickup_search()

    def _recognize_delivery_end(self, ends_pattern_dict: dict):
        results = self.wait_ocr(
            match=list(ends_pattern_dict.keys()),
            box=self.box.left,
            time_out=10,
            log=True,
        )
        if not results:
            self.log_warning("未识别到送货目标")
            return None, None
        for result in results:
            for pattern, end in ends_pattern_dict.items():
                if pattern.search(result.name):
                    return end, pattern
        self.log_warning("左侧目标文本未匹配配置的送货终点")
        return None, None

    def _submit_at_destination(self, end_pattern) -> bool:
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
            return True
        self.log_info("网格导航到达后未直接找到提交按钮，回退原送达搜索")
        return self.to_end_and_submit(end_pattern)

    def _run_legacy_delivery_leg(self, ends_pattern_dict: dict) -> bool:
        """执行原滑索距离、蓝色标记和模板搜索组成的取货送达流程。"""
        if not self.to_storage_point_and_back_zip_line():
            return False
        end, end_pattern = self._recognize_delivery_end(ends_pattern_dict)
        if end is None:
            return False
        self.wait_click_ocr(
            match=self.lang.DeliveryTask.k_b0e3a2da,
            box=self.box.bottom_right,
            time_out=2,
            log=True,
            after_sleep=2,
            alt=True,
        )
        self.on_zip_line_start(
            end,
            need_scroll=self.zip_line_scroll_enabled(),
            target=(secondary_objective_direction_dot, "feature"),
        )
        return self.to_end_and_submit(end_pattern)

    def _run_grid_delivery_state_machine(self, ends_pattern_dict: dict) -> bool:
        """按坐标状态机执行取货和送达，移动由网格导航统一负责。"""
        pickup_coordinate = get_delivery_location_coordinate(
            self.delivery_area,
            self._accepted_delivery_location or "",
        )
        if pickup_coordinate is None:
            self.log_warning(f"取货点 {self._accepted_delivery_location!r} 缺少坐标，回退原取货送达流程")
            return self._run_legacy_delivery_leg(ends_pattern_dict)

        phase = DeliveryPhase.NAVIGATE_TO_PICKUP
        end_pattern = None
        while phase not in (DeliveryPhase.DONE, DeliveryPhase.FAILED):
            if phase == DeliveryPhase.NAVIGATE_TO_PICKUP:
                phase = (
                    DeliveryPhase.PICKUP
                    if self._navigate_delivery_coordinate(pickup_coordinate, "取货点")
                    else DeliveryPhase.FAILED
                )
            elif phase == DeliveryPhase.PICKUP:
                phase = (
                    DeliveryPhase.RECOGNIZE_DESTINATION
                    if self._pickup_receive_good_with_fallback()
                    else DeliveryPhase.FAILED
                )
            elif phase == DeliveryPhase.RECOGNIZE_DESTINATION:
                end, end_pattern = self._recognize_delivery_end(ends_pattern_dict)
                if end is None:
                    phase = DeliveryPhase.FAILED
                    continue
                target_coordinate = get_delivery_target_coordinate(
                    self.delivery_area,
                    end,
                    self._accepted_delivery_location,
                )
                if target_coordinate is None:
                    self.log_warning(f"送货终点 {end!r} 缺少坐标，回退原送达流程")
                    self.on_zip_line_start(
                        end,
                        need_scroll=self.zip_line_scroll_enabled(),
                        target=(secondary_objective_direction_dot, "feature"),
                    )
                    return self.to_end_and_submit(end_pattern)
                pickup_coordinate = target_coordinate
                phase = DeliveryPhase.NAVIGATE_TO_DESTINATION
            elif phase == DeliveryPhase.NAVIGATE_TO_DESTINATION:
                phase = (
                    DeliveryPhase.SUBMIT
                    if self._navigate_delivery_coordinate(pickup_coordinate, "送货点")
                    else DeliveryPhase.FAILED
                )
            elif phase == DeliveryPhase.SUBMIT:
                phase = DeliveryPhase.DONE if self._submit_at_destination(end_pattern) else DeliveryPhase.FAILED
        return phase == DeliveryPhase.DONE

    def _run_single_delivery_cycle(self) -> bool:
        if getattr(self, "_daily_delivery_mode", False) or self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE:
            ends_list_pattern_dict = self._delivery_end_patterns()
            completed = False
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
                    return self.accept_order()
                else:
                    if not self.config.get(self.CFG_ONLY_DELIVER) and not self.accept_order():
                        return False
                    success = None
                    for _attempt in range(3):
                        success = self.task_to_transfer_point(
                            need_location_list=get_delivery_locations(self.delivery_area, self.lang),
                        )
                        if success:
                            break
                    if not success:
                        self.log_info("传送失败（未找到传送按钮），终止本轮送货")
                        return False
                    # 缓存为空时用地图上记录的地区回填（覆盖仅送货模式等未接取委托的场景）
                    if not self._accepted_delivery_location and self.location:
                        self._accepted_delivery_location = extract_delivery_location(
                            self.location, self.delivery_area, self.lang
                        )
                        if self._accepted_delivery_location:
                            self.log_info(f"通过地图自动回填送货地点: {self._accepted_delivery_location}")
                    if self._arrival_mode() == DeliveryNavigationMode.GRID:
                        if not self._run_grid_delivery_state_machine(ends_list_pattern_dict):
                            return False
                    else:
                        if not self._run_legacy_delivery_leg(ends_list_pattern_dict):
                            return False
                    completed = True
                    if self.config.get(self.CFG_ONLY_DELIVER):
                        break
            return completed
        elif self.config.get(self.CFG_TEST_TARGET) == self.TEST_FULL_CYCLE:
            test_location = self.config.get(self.CFG_FULL_CYCLE_LOCATION)
            full_cycle_targets = get_full_cycle_targets(self.delivery_area, test_location)
            if not full_cycle_targets:
                self.log_info(f"未配置测试区域({test_location})的送货目标")
                return False
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
                    self.log_info("已找到并登上滑索架，继续测试")
                    self.on_zip_line_start(end, need_v=False, need_scroll=self.zip_line_scroll_enabled())
                    self.sleep(2)
            return True
        else:
            zip_line_list_str = self.get_zip_line_config_value(self.config.get(self.CFG_TEST_TARGET))
            if zip_line_list_str:
                zip_line_list = parse_int_sequence(zip_line_list_str)
                self.zip_line_list_go(
                    zip_line_list,
                    need_scroll=self.zip_line_scroll_enabled(),
                )
            return True

    def _ensure_delivery_area_config(self):
        """校验并应用配置中的送货地区，必要时同步相关下拉选项。"""
        current_area = self.config.get(self.CFG_DELIVERY_AREA, DEFAULT_DELIVERY_AREA)
        if current_area not in DELIVERY_AREA_CONFIG:
            self.log_info(f"配置的地区({current_area})无效，回退为默认地区({DEFAULT_DELIVERY_AREA})")
            current_area = DEFAULT_DELIVERY_AREA
        if current_area != self.delivery_area:
            self._configure_delivery_area(current_area)
        # 地区未变时也要修正无效的完整循环测试区域（如地区数据更新后旧地点失效）
        if self.CFG_FULL_CYCLE_LOCATION in self.config:
            if self.config.get(self.CFG_FULL_CYCLE_LOCATION) not in self.full_cycle_locations:
                self.config[self.CFG_FULL_CYCLE_LOCATION] = self.full_cycle_locations[0]
        # 日常模式（DeliveryFeature）未注册这两个配置项，仅独立任务模式同步下拉选项
        if self.CFG_FULL_CYCLE_LOCATION in self.config_type and self.CFG_TEST_TARGET in self.config_type:
            self.config_type[self.CFG_TEST_TARGET]["options"] = (
                [self.TEST_NONE] + self.to_delivery_point_config_keys + self.ends + [self.TEST_FULL_CYCLE]
            )
            self.config_type[self.CFG_FULL_CYCLE_LOCATION]["options"] = self.full_cycle_locations

    def run(self):
        """运输委托任务的主入口，支持与日常任务一致的多账号执行逻辑。"""
        try:
            self._ensure_delivery_area_config()
            allow_multi = (
                self.config.get(self.CFG_TEST_TARGET) == self.TEST_NONE
                and not self.config.get(self.CFG_ONLY_ACCEPT)
                and not self.config.get(self.CFG_ONLY_DELIVER)
            )
            for repeat_idx, repeat_times in self.iter_multi_account_context(
                repeat_times=1,
                empty_accounts_message="多账户模式已开启，但账号列表为空，自动送货任务结束",
                account_log_suffix=self.tr("自动送货"),
                allow_multi_account=allow_multi,
            ):
                self._run_single_delivery_cycle()

        except Exception as e:
            self.handle_task_exception(e, "DeliveryTask_Exception")


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
        self._init_grid_navigation_mixin()

        task.default_config.update(
            {
                self.DAILY_ENABLE_KEY: False,
                self.CFG_TARGET_TICKET_NUM: ["119000"],
                self.CFG_DELIVERY_AREA: DEFAULT_DELIVERY_AREA,
                self.CFG_ARRIVAL_MODE: self.ARRIVAL_MODE_LEGACY,
            }
        )
        task.config_description.update(
            {
                self.DAILY_ENABLE_KEY: "是否执行自动接取并完成运输委托。",
                self.CFG_TARGET_TICKET_NUM: ("目标券数优先级序列，用逗号分隔多个券数。"),
                self.CFG_DELIVERY_AREA: "通过下拉框切换送货地区配置。",
                self.CFG_ARRIVAL_MODE: (
                    "选择取货和送达阶段的到达方式：原流程使用原有滑索距离序列，"
                    "网格导航按取货点和终点坐标自动组合滑索与寻路。"
                ),
            }
        )
        task.config_type[self.CFG_DELIVERY_AREA] = {
            "type": "drop_down",
            "options": list(DELIVERY_AREA_CONFIG.keys()),
        }
        task.config_type[self.CFG_TARGET_TICKET_NUM] = {
            "options_available": DELIVERY_TARGET_TICKET_NUM_OPTIONS,
            "allow_duplication": False,
        }
        task.config_type[self.CFG_ARRIVAL_MODE] = {
            "type": "drop_down",
            "options": [self.ARRIVAL_MODE_LEGACY, self.ARRIVAL_MODE_GRID],
        }
        task.default_config_group.update(
            {
                self.DAILY_ENABLE_KEY: [
                    self.CFG_TARGET_TICKET_NUM,
                    self.CFG_DELIVERY_AREA,
                    self.CFG_ARRIVAL_MODE,
                ],
            }
        )

    def __getattr__(self, name):
        return getattr(self._task, name)

    def run_daily(self):
        """执行一轮日常自动送货，由 DailyTask 负责多账号循环。"""
        self._ensure_delivery_area_config()

        self._daily_delivery_mode = True
        try:
            return bool(self._run_single_delivery_cycle())
        finally:
            self._daily_delivery_mode = False
