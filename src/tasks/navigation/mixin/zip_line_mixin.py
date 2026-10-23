"""滑索交互、对准和场景状态判定。

本模块同时服务自动送货与网格导航：

- 自动送货先按滑索图标模板定位候选，再做局部分色 OCR 对中；模板连续漏检时回退全屏 OCR；
- 网格导航优先使用世界方位直接对准，误差收敛到 ±3° 后执行；
- 两者共用「登上滑索架」模板匹配、错误落点判定和 WS 状态检查。

滑索架状态只以 ``move_to_the_target`` / ``move_to_the_target_2`` 模板为准；
融合坐标不参与上下索判断。
"""

import math
import re
from contextlib import nullcontext
from copy import copy
from dataclasses import replace

import cv2
import numpy as np

from src.core.global_config_store import (
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_DELIVERY_KEYS,
    ZIP_LINE_GATHER_KEYS,
    ZIP_LINE_SCROLL_KEY,
    ZIP_LINE_TARGET_SELECT_DIRECT,
    ZIP_LINE_TARGET_SELECT_KEY,
    ZIP_LINE_TARGET_SELECT_OCR,
    get_global_config,
)
from src.core.learning.models import yaw_bucket
from src.core.learning.policy import learning_allowed, learning_epoch
from src.core.sequence_parser import parse_int_sequence
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.interaction.target_movement import ellipse_geometry, ellipse_move_profile, ellipse_radius, hud_direction_step
from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.mixin.navigation_learning import align_initial_target
from src.tasks.mixin.route_learning import route_context_for, zipline_initial_alignment
from src.tasks.mixin.zip_line_motion_cache import build_zip_line_route_fingerprint
from src.tasks.mixin.zip_line_yaw_cache import (
    load_arrow_turn_motion,
    record_arrow_turn_motion,
    yaw_environment,
)
from src.tasks.navigation.mixin.navigation_mixin import NavigationMixin

ZIP_LINE_TEMPLATE_THRESHOLD = 0.8
ZIP_LINE_STATUS_BOX = (0.351, 0.943, 0.657, 0.981)
ZIP_LINE_DIRECT_TOLERANCE_DEG = 3.0
ZIP_LINE_DIRECT_PITCH_STEPS = (0, 24, -24, 48, -48)
ZIP_LINE_MOTION_POLL_SECONDS = 0.2
ZIP_LINE_MOTION_CHANGE_METERS = 0.5
ZIP_LINE_BOARD_MOVE_DURATION_S = 0.12
ZIP_LINE_BOARD_RECHECK_SETTLE_S = 0.25

#: 滑索图标二值模板（1080p 基准），按当前分辨率缩放后用于模板匹配。
_ZIP_LINE_ICON_TEMPLATE_ROWS = (
    "....................",
    "...############.....",
    ".##.......##........",
    ".##.......##........",
    "...#################",
    "...#################",
    "..............##....",
    "..............##....",
    "..............##....",
    "..............##....",
    "..............##....",
    "..............##....",
    ".............###....",
    "..........#..##.....",
    "............##......",
    "....................",
    "....................",
    "....................",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    "........####........",
    ".....#########......",
    "..............######",
    "...................#",
)

_ZIP_LINE_YELLOW_ICON_RANGES = (((15, 80, 160), (50, 255, 255)),)
_ZIP_LINE_GOLD_RING_RANGES = (((13, 35, 160), (50, 255, 255)),)


class ZipLineReplanRequired(Exception):
    """滑索实际落点与规划目标不符，需要从当前位置重新规划。"""

    def __init__(
        self,
        message,
        *,
        current_position=None,
        failed_target_position=None,
    ):
        super().__init__(message)
        self.current_position = current_position
        self.failed_target_position = failed_target_position


class ZipLineMixin(InstructionsMixin, NavigationMixin):
    _ZIP_LINE_TEMPLATE_THRESHOLD = 0.55
    _ZIP_LINE_TEMPLATE_MISS_LIMIT = 3
    _ZIP_LINE_ELLIPSE_RX = 720
    _ZIP_LINE_ELLIPSE_RY = 405
    _ZIP_LINE_INNER_RADIUS = 0.5
    _ZIP_LINE_OUTER_MAX_BOOST = 1.75
    _ZIP_LINE_ARROW_MIN_SCORE = 0.60
    _ZIP_LINE_ARROW_TOLERANCE = 3.0
    _ZIP_LINE_ARROW_MAX_STEP_DEGREES = 30.0
    _ZIP_LINE_ARROW_MAX_STEPS = 6
    _ZIP_LINE_ARROW_YAW_PER_MOUSE = -0.083
    _ZIP_LINE_ARROW_SETTLE_DELAY = 0.08
    _ZIP_LINE_ARROW_STABLE_SAMPLES = 3
    _ZIP_LINE_ARROW_STABLE_TOLERANCE = 0.5
    _ZIP_LINE_ARROW_SETTLE_TIMEOUT = 1.5

    @property
    def zip_line_config(self):
        return get_global_config(ZIP_LINE_CONFIG_NAME)

    def build_instructions(self):
        """滑索配置使用说明。

        说明文本通过 self.tr() 走 ok 的 gettext i18n（msgid 写入 ok.po，编译成 ok.mo 生效）。
        由 InstructionsMixin 延迟构建并追加到任务原有说明之后，使用滑索的任务无需在 __init__ 里显式调用。
        """
        # 键名从滑索配置数据动态读取，不硬编码；显示时经 self.tr() 跟随 UI 语言翻译
        start_keys_raw = [k for k in ZIP_LINE_DELIVERY_KEYS if k.startswith("通向")]
        target_keys_raw = [k for k in ZIP_LINE_DELIVERY_KEYS if not k.startswith("通向")]
        gather_keys_raw = ZIP_LINE_GATHER_KEYS

        # 例子动态取第一个送货目标及其配置值（查配置用原始键名，显示用翻译后键名）
        example_key_raw = target_keys_raw[0] if target_keys_raw else (start_keys_raw[0] if start_keys_raw else "")
        example_raw = str(self.zip_line_config.get(example_key_raw, "") or "").strip()
        example_seq = " → ".join(f"{n}m" for n in example_raw.split(",") if str(n).strip())
        example_key = self.tr(example_key_raw) if example_key_raw else ""

        start_keys = [self.tr(k) for k in start_keys_raw]
        target_keys = [self.tr(k) for k in target_keys_raw]
        gather_keys = [self.tr(k) for k in gather_keys_raw]

        return "<br>".join(
            [
                inst_line("📍 " + self.tr("滑索配置说明"), "#FF5555", bold=True),
                inst_line("⚙️ " + self.tr("滑索距离序列在「全局配置 → 滑索配置」中设置"), "#FF5555", bold=True),
                inst_gap(),
                inst_line("⚠️ " + self.tr("填写规则"), "#FE821D", bold=True),
                inst_line(f"└─ {self.tr('每个键对应一条滑索路线，值为距离序列，用英文逗号分隔')}", indent=1),
                inst_line(f"└─ {self.tr('任务会按顺序依次对齐并滑行每段距离')}", indent=1),
                inst_line(
                    f"└─ {self.tr('例：「{key}」= {raw} → 依次滑行 {seq}').format(key=example_key, raw=example_raw, seq=example_seq)}",
                    indent=1,
                ),
                inst_line(f"└─ {self.tr('留空表示该路线不乘滑索')}", indent=1),
                inst_gap(),
                inst_line("📦 " + self.tr("送货相关键"), "#FE821D", bold=True),
                inst_line(f"├─ {self.tr('{keys}：出发滑索距离').format(keys=' / '.join(start_keys))}", indent=1),
                inst_line(f"└─ {self.tr('{keys}：各送货目标滑索序列').format(keys=' / '.join(target_keys))}", indent=1),
                inst_gap(),
                inst_line("🪫 " + self.tr("淤积点相关键"), "#FE821D", bold=True),
                inst_line(f"└─ {self.tr('{keys}：能量淤积点滑索序列').format(keys=' / '.join(gather_keys))}", indent=1),
                inst_gap(),
                inst_line("🖱️ " + self.tr("是否启用滚动放大视角"), "#FE821D", bold=True),
                inst_line(f"└─ {self.tr('对齐滑索时自动滚动放大视角，可能提高成功率，也可能明显降低')}", indent=1),
                inst_line(f"└─ {self.tr('建议启用时不要使用非白发或有白帽角色')}", indent=1),
            ]
        )

    def get_zip_line_config_value(self, key, default=None):
        base_value = self.zip_line_config.get(key, default)
        override = self._account_override_for(ZIP_LINE_CONFIG_NAME)
        value = base_value if key not in override else self._coerce_override_value(base_value, override[key])
        if key != ZIP_LINE_SCROLL_KEY:
            try:
                route_values = tuple(parse_int_sequence(value))
            except Exception:
                route_values = ()
            self._pending_zip_line_motion_route = (str(key), route_values)
        return value

    def zip_line_scroll_enabled(self):
        return self.get_zip_line_config_value(ZIP_LINE_SCROLL_KEY, False)

    def zip_line_target_select_mode(self):
        """返回滑索目标选择方式：``直接对准`` 或 ``OCR``。"""
        value = str(
            self.get_zip_line_config_value(
                ZIP_LINE_TARGET_SELECT_KEY,
                ZIP_LINE_TARGET_SELECT_DIRECT,
            )
            or ""
        ).strip()
        if value == ZIP_LINE_TARGET_SELECT_OCR:
            return ZIP_LINE_TARGET_SELECT_OCR
        return ZIP_LINE_TARGET_SELECT_DIRECT

    @staticmethod
    def _normalize_values(value, *, scalar=False):
        """把标量或序列统一成列表；``None`` 保持为 ``None``。"""
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value] if scalar else value

    @staticmethod
    def _value_for_index(values, index, default=None):
        """按滑索段下标取值；序列短于段数时重复最后一项。"""
        if not values:
            return default
        return values[min(index, len(values) - 1)]

    def _zip_line_status_box(self):
        return self.box_of_screen(*ZIP_LINE_STATUS_BOX)

    def _zip_line_on_rack_visible(self, frame):
        """当前画面是否显示任一「移动鼠标选择前进目标」模板。"""
        find_feature = getattr(self, "find_feature", None)
        if not callable(find_feature):
            return False
        box = self.box_of_screen(0.35, 0.90, 0.65, 1.0)
        for feature_name in (
            fL.move_to_the_target,
            fL.move_to_the_target_2,
        ):
            try:
                if find_feature(
                    feature_name=feature_name,
                    frame=frame,
                    box=box,
                    threshold=ZIP_LINE_TEMPLATE_THRESHOLD,
                ):
                    return True
            except Exception:
                continue
        return False

    def _zip_line_ws_position(self, frame=None):
        """读取滑索场景最新 WS 坐标；融合坐标不作为滑索到达依据。"""
        world_pose_getter = getattr(self, "world_pose", None)
        if not callable(world_pose_getter):
            return None
        try:
            sample_frame = frame if frame is not None else self.next_frame()
            state = world_pose_getter(
                frame=sample_frame,
                max_age=2.0,
            )
            if state is None:
                return None
            position = state.get("ws")
            if position is None:
                return None
            return float(position[0]), float(position[1])
        except Exception:
            return None

    def _zip_line_ws_position_xyz(self, frame=None):
        """读取最新 WS 三维坐标，用于区分同层投影但不同高度的滑索节点。"""
        world_pose_getter = getattr(self, "world_pose", None)
        if not callable(world_pose_getter):
            return None
        try:
            sample_frame = frame if frame is not None else self.next_frame()
            state = world_pose_getter(
                frame=sample_frame,
                max_age=2.0,
            )
            if state is None:
                return None
            position = state.get("ws_xyz")
            if position is None or len(position) < 3:
                return None
            return float(position[0]), float(position[1]), float(position[2])
        except Exception:
            return None

    def _wait_zip_line_motion(
        self,
        target_position,
        start_position=None,
        *,
        timeout=60.0,
        near_distance=4.0,
        stable_seconds=6.0,
        start_grace_seconds=None,
    ):
        """按目标模板和原始 WS 坐标判断滑索状态。

        一次轮询的判定结果：

        - ``MOVING``：模板不存在，正在滑索间移动，继续等待；
        - ``ARRIVED``：模板存在且 WS 到达目标附近，进入下一段；
        - ``WAITING_TEMPLATE``：已停稳在目标附近但到达模板还没出现，继续等模板；
        - ``NOT_TRIGGERED``：已停稳且 WS 仍在起点附近，没触发滑索，返回失败交给外层重试；
        - ``WRONG_RACK``：停稳后既不在起点也不在目标附近，落到了别的滑索架，重新规划。

        调用方负责点击鼠标左键；本方法只轮询状态，不重复发送按键。
        """
        if target_position is None:
            return False
        start = self.active_time()
        moving_logged = False
        last_position: tuple[float, float] | None = None
        last_position_changed_at = start
        threshold = max(0.1, float(near_distance))
        stable_seconds = max(0.1, float(stable_seconds))
        start_grace_seconds = (
            max(stable_seconds, 6.0) if start_grace_seconds is None else max(0.0, float(start_grace_seconds))
        )
        while self.active_time() - start < max(0.1, float(timeout)):
            frame = self.next_frame()
            on_rack = self._zip_line_on_rack_visible(frame)
            if not on_rack and not moving_logged:
                self.log_info("move_to_the_target / move_to_the_target_2 模板均不存在，滑索间移动中，继续等待")
                moving_logged = True

            current_position = self._zip_line_ws_position(frame=frame)
            if current_position is None:
                self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
                continue

            if last_position is None or math.dist(current_position, last_position) > ZIP_LINE_MOTION_CHANGE_METERS:
                last_position = current_position
                last_position_changed_at = self.active_time()

            target_distance = math.dist(current_position, target_position)
            start_distance = None
            if start_position is not None:
                start_distance = math.dist(current_position, start_position)

            if on_rack and target_distance <= threshold:
                self.log_info(f"已到达下一滑索附近且到达模板存在：WS 距离={target_distance:.2f}m")
                return True

            if self.active_time() - last_position_changed_at >= stable_seconds:
                # 已经停稳，按"停在哪、模板在不在"区分。这里原来写成 on_rack 的两个分支，
                # 两段"停稳后判定"几乎逐字重复，只有模板在时的日志级别与措辞不同。
                if not on_rack and target_distance <= threshold:
                    # 停在目标附近但到达模板还没出现：继续等模板，既不算到达也不算失败。
                    self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
                    continue
                if start_distance is not None and start_distance <= threshold:
                    # 仍停在起点附近：没触发滑索。刚上索的一小段时间先继续等，避免 WS
                    # 还没走完一个推送周期就判定失败。
                    if self.active_time() - start < start_grace_seconds:
                        self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
                        continue
                    if on_rack:
                        self.log_info(f"到达模板存在且 WS 仍在起点附近：距离={start_distance:.2f}m，准备重试")
                    else:
                        self.log_warning("WS 已在起点附近停稳但未触发滑索，准备重试")
                    return False
                # 停稳后既不在起点也不在目标附近：落到了别的滑索架，必须重新规划。
                raise ZipLineReplanRequired(
                    ("到达了非目标滑索架：" if on_rack else "滑索结束在非目标滑索架：")
                    + f"WS=({current_position[0]:.2f}, {current_position[1]:.2f})，"
                    f"距起点={start_distance if start_distance is None else round(start_distance, 2)}m，"
                    f"距目标={target_distance:.2f}m",
                    current_position=current_position,
                )

            self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
        return False

    def _direct_zip_line_go(self, target_bearing, target_position=None):
        """按世界方位直接对准并点击目标，失败时调整俯仰角重试。"""
        aim = getattr(self, "pose_aim_view_to_bearing", None)
        if not callable(aim):
            self.log_warning("当前任务不支持读取小地图朝向，无法直接对准滑索目标")
            return False

        last_start_position = None
        applied_pitch = 0
        try:
            for index, pitch in enumerate(ZIP_LINE_DIRECT_PITCH_STEPS):
                delta = pitch - applied_pitch
                if delta:
                    self.log_info(f"直接对准未触发滑索，调整俯仰角 {pitch:+d}px 后重试")
                    self.active_and_send_mouse_delta(dx=0, dy=delta, steps=1, delay=0)
                    applied_pitch = pitch
                    self.sleep(0.2)
                result = aim(
                    target_bearing,
                    tolerance=ZIP_LINE_DIRECT_TOLERANCE_DEG,
                    max_rounds=2,
                )
                if not result.get("ok"):
                    self.log_warning(
                        f"滑索方位误差未进入 ±{ZIP_LINE_DIRECT_TOLERANCE_DEG:g}°："
                        f"目标={float(target_bearing):.1f}°，"
                        f"实测={result.get('heading')}，误差={result.get('error')}"
                    )
                    continue
                self.log_info(
                    f"滑索方位已对准：目标={float(target_bearing):.1f}°，"
                    f"实测={result.get('heading')}，误差={result.get('error')}"
                )
                last_start_position = self._zip_line_ws_position()
                self.click(after_sleep=0.1)
                if self._wait_zip_line_motion(
                    target_position=target_position,
                    start_position=last_start_position,
                ):
                    return True
                self.log_warning(f"直接对准第 {index + 1} 次点击未触发滑索")
        finally:
            if applied_pitch:
                self.active_and_send_mouse_delta(dx=0, dy=-applied_pitch, steps=1, delay=0)
                self.sleep(0.2)
        raise ZipLineReplanRequired(
            f"连续 {len(ZIP_LINE_DIRECT_PITCH_STEPS)} 次未移动到下一滑索，判定两滑索间存在阻挡，需要重新规划",
            current_position=last_start_position,
            failed_target_position=target_position,
        )

    def _find_zip_line_board_button(
        self,
        direct_wait=5.0,
        total_time_out=60.0,
        *,
        switch_walk_mode=True,
    ):
        """确保处于主界面，并寻找「登上滑索架」交互按钮。

        复用自动送货已验证的三阶段策略：原地查找、WASD 踱步、W/S 前后移动。
        """
        self.ensure_main()
        language = getattr(self.lang, "zip_line_mixin", None)
        match = getattr(language, "k_b0e3a2da", None)
        if match is None:
            match = self.lang.DeliveryTask.k_b0e3a2da
        box = self.box.bottom_right
        start = self.active_time()
        deadline = start + max(0.0, total_time_out)

        def find_once():
            frame = self.next_frame()
            find_feature = getattr(self, "find_feature", None)
            if callable(find_feature):
                template_results = find_feature(
                    feature_name=fL.climb_the_zip_line,
                    frame=frame,
                    box=box,
                    threshold=ZIP_LINE_TEMPLATE_THRESHOLD,
                )
                if template_results:
                    return template_results[0]
            results = self.ocr(match=match, box=box, frame=frame, log=True)
            return results[0] if results else None

        def check():
            candidate = find_once()
            if not candidate:
                return None
            # 移动后先停稳，再用新画面复核；防止沿旧框点击时角色/镜头已经移动。
            self.sleep(ZIP_LINE_BOARD_RECHECK_SETTLE_S)
            stable = find_once()
            if stable is None:
                self.log_info("停稳后登索提示消失，继续移动搜索")
            return stable

        while self.active_time() < min(start + direct_wait, deadline):
            if found := check():
                return found
            self.sleep(0.1)

        remaining = deadline - self.active_time()
        if remaining <= 0:
            self.log_info("总等待时间已耗尽，仍未找到登上滑索架")
            return None

        if switch_walk_mode:
            self.press_key("ctrl")
        try:
            self.log_info("短时间内未找到登上滑索架，可能被其他设备遮挡，切换步行开始踱步寻找（最长 10 秒）")
            found = self.strafe_search(
                check,
                passes=None,
                duration=ZIP_LINE_BOARD_MOVE_DURATION_S,
                keys=("s", "w", "a", "d"),
                time_out=min(10.0, remaining),
            )
            if found:
                self.log_info("踱步寻找过程中找到登上滑索架")
                return found

            remaining = deadline - self.active_time()
            if remaining <= 0:
                self.log_info("踱步超时，仍未找到登上滑索架")
                return None
            self.log_info("踱步仍未找到登上滑索架，改为仅 W/S 前后移动继续寻找")
            found = self.strafe_search(
                check,
                passes=None,
                duration=ZIP_LINE_BOARD_MOVE_DURATION_S,
                keys=("s", "w"),
                time_out=remaining,
            )
            if found:
                self.log_info("前后移动寻找过程中找到登上滑索架")
                return found
            self.log_info("前后移动超时，仍未找到登上滑索架")
            return None
        finally:
            if switch_walk_mode:
                self.press_key("ctrl", after_sleep=0.01)
                self.log_info("恢复奔跑模式")

    def board_zip_line(
        self,
        direct_wait=5.0,
        total_time_out=60.0,
        *,
        switch_walk_mode=True,
    ):
        """点击当前滑索架的「登上滑索架」按钮。"""
        result = self._find_zip_line_board_button(
            direct_wait=direct_wait,
            total_time_out=total_time_out,
            switch_walk_mode=switch_walk_mode,
        )
        if not result:
            return False
        self.press_key("f", after_sleep=0.2)
        self.log_info("已按 F 登上滑索架")
        return True

    def on_zip_line_start(
        self,
        delivery_to,
        need_scroll=None,
        target=None,
        need_v=True,
        stay_on_zip_line=False,
        route_family=(),
        target_learning_context=None,
    ):
        """进入滑索后，根据配置对齐并滑行至送货点

        Args:
            delivery_to: 送货目标名称（用于获取配置中的滑索距离序列）
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪
            stay_on_zip_line: 路线结束后留在滑索上，供下一段路线继续测试
            route_family: 调用者已确认的物理路线族；未知入口保持私有。
            target_learning_context: 末端重新登架后的首次目标对中上下文。
        Raises:
            Exception: 滑索超时时抛出异常
        """
        start = self.active_time()
        self.sleep(1)
        self.next_frame()
        on_zip_line_stop = [
            self.lang.zip_line_mixin.k_2f4f4a2f,
            self.lang.zip_line_mixin.k_0b1e4f35,
        ]
        while not self.ocr(match=on_zip_line_stop, frame=self.next_frame(), box="bottom", log=True):
            self.sleep(0.1)
            if self.active_time() - start > 60:
                raise RuntimeError("滑索超时，强制退出")
        zip_line_list_str = self.get_zip_line_config_value(delivery_to)
        zip_line_list = parse_int_sequence(zip_line_list_str)
        self.zip_line_list_go(
            zip_line_list,
            need_scroll,
            target,
            need_v=need_v,
            stay_on_zip_line=stay_on_zip_line,
            config_key=delivery_to,
            **({"route_family": route_family} if route_family else {}),
            **({"target_learning_context": target_learning_context} if target_learning_context is not None else {}),
        )

    @staticmethod
    def _zip_line_distance_pattern(zip_line):
        """匹配完整距离数字，避免 108 错配到 1080m 等更长距离。"""
        return re.compile(rf"(?<!\d){re.escape(str(zip_line))}(?!\d)")

    @staticmethod
    def _zip_line_distance_matcher(zip_line, distance_tolerance=None):
        """构造距离匹配模式。

        无容差时就是精确匹配（自动送货用）；给了容差则返回窗口内的多个模式，用于网格导航
        ——那里的滑索距离来自地图标记的三维坐标，与游戏内显示值可能对不上。
        """
        tolerance = 0 if distance_tolerance is None else max(0, round(float(distance_tolerance)))
        if tolerance <= 0:
            return re.compile(rf"(?<!\d){re.escape(str(zip_line))}(?!\d)")
        center = round(float(zip_line))
        return [
            re.compile(rf"(?<!\d){number}(?!\d)(?:\s*m)?", re.IGNORECASE)
            for number in range(max(1, center - tolerance), center + tolerance + 1)
        ]

    @staticmethod
    def _zip_line_hsv_mask(hsv, ranges):
        """把一组 HSV 范围合并成单通道掩码。"""
        if hasattr(ranges, "value"):
            ranges = ranges.value
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in ranges:
            current = cv2.inRange(
                hsv,
                np.asarray(lower, dtype=np.uint8),
                np.asarray(upper, dtype=np.uint8),
            )
            mask = cv2.bitwise_or(mask, current)
        return mask

    def _zip_line_icon_template(self):
        """返回按当前分辨率缩放的 1080p 滑索图标二值模板。"""
        template = np.asarray(
            [[255 if pixel == "#" else 0 for pixel in row] for row in _ZIP_LINE_ICON_TEMPLATE_ROWS],
            dtype=np.uint8,
        )
        scale = self.resolution_scale()
        if abs(scale - 1.0) < 1e-6:
            return template
        width = max(1, round(template.shape[1] * scale))
        height = max(1, round(template.shape[0] * scale))
        return cv2.resize(template, (width, height), interpolation=cv2.INTER_NEAREST)

    def _zip_line_ellipse_geometry(self):
        """返回屏幕中心与滑索边缘提示所在椭圆的半径。"""
        return ellipse_geometry(
            self.screen_center(), self.resolution_scale(), self._ZIP_LINE_ELLIPSE_RX, self._ZIP_LINE_ELLIPSE_RY
        )

    def _zip_line_ellipse_radius(self, x, y):
        """返回点在滑索椭圆中的归一化半径，1.0 为椭圆边缘。"""
        return ellipse_radius(x, y, self._zip_line_ellipse_geometry())

    def _zip_line_has_gold_ring(self, gold_mask, center_x, center_y):
        """用图标四周均有金黄色像素来识别当前锁定态，避免环境黄色误判。"""
        scale = self.resolution_scale()
        radius = max(1, round(22 * scale))
        x = round(center_x)
        y = round(center_y)
        left = max(0, x - radius)
        right = min(gold_mask.shape[1], x + radius)
        top = max(0, y - radius)
        bottom = min(gold_mask.shape[0], y + radius)
        counts = (
            np.count_nonzero(gold_mask[top:y, left:x]),
            np.count_nonzero(gold_mask[top:y, x:right]),
            np.count_nonzero(gold_mask[y:bottom, left:x]),
            np.count_nonzero(gold_mask[y:bottom, x:right]),
        )
        minimum = max(4, round(8 * scale * scale))
        return all(count >= minimum for count in counts)

    def _detect_zip_line_icon_candidates(self, frame, search_box=None):
        """仅在滑索椭圆内检测白色与黄色/金黄色图标，蓝色共享滑索不会进入候选。"""
        if frame is None or getattr(frame, "ndim", 0) < 2:
            return []

        height, width = frame.shape[:2]
        template = self._zip_line_icon_template()
        template_height, template_width = template.shape[:2]
        center_x, center_y, radius_x, radius_y = self._zip_line_ellipse_geometry()
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        white_mask = self._zip_line_hsv_mask(hsv, hR.WHITE)
        yellow_mask = self._zip_line_hsv_mask(hsv, _ZIP_LINE_YELLOW_ICON_RANGES)
        gold_mask = self._zip_line_hsv_mask(hsv, _ZIP_LINE_GOLD_RING_RANGES)
        log_info = getattr(self, "log_info", None)

        left = max(0, center_x - radius_x - template_width)
        right = min(width, center_x + radius_x + template_width)
        top = max(0, center_y - radius_y - template_height)
        bottom = min(height, center_y + radius_y + template_height)
        if search_box is not None:
            left = max(left, int(search_box.x))
            top = max(top, int(search_box.y))
            right = min(right, int(search_box.x + search_box.width))
            bottom = min(bottom, int(search_box.y + search_box.height))
        if right - left < template_width or bottom - top < template_height:
            if callable(log_info):
                log_info(
                    f"[zipline-template] invalid-search-area frame={width}x{height} "
                    f"roi=({left},{top})-({right},{bottom}) template={template_width}x{template_height}"
                )
            return []

        ellipse_mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
        cv2.ellipse(
            ellipse_mask,
            (center_x - left, center_y - top),
            (radius_x + template_width // 2, radius_y + template_height // 2),
            0,
            0,
            360,
            255,
            -1,
        )

        candidates = []
        scale = self.resolution_scale()
        merge_distance = max(1, round(12 * scale))
        for source_mask, source_state in ((white_mask, "white"), (yellow_mask, "gold")):
            search_mask = cv2.bitwise_and(source_mask[top:bottom, left:right], ellipse_mask)
            scores = cv2.matchTemplate(search_mask, template, cv2.TM_CCOEFF_NORMED)
            best_score = float(scores.max()) if scores.size else 0.0
            locations = np.argwhere(scores >= self._ZIP_LINE_TEMPLATE_THRESHOLD)
            locations = sorted(locations, key=lambda pos: float(scores[pos[0], pos[1]]), reverse=True)
            raw_hit_count = len(locations)
            candidate_count_before = len(candidates)
            for local_y, local_x in locations:
                x = int(local_x + left)
                y = int(local_y + top)
                score = float(scores[local_y, local_x])
                icon_center_x = x + template_width / 2
                icon_center_y = y + template_height / 2
                ellipse_radius = self._zip_line_ellipse_radius(icon_center_x, icon_center_y)
                if ellipse_radius > 1.0:
                    continue

                state = source_state
                if state == "white" and self._zip_line_has_gold_ring(gold_mask, icon_center_x, icon_center_y):
                    state = "gold"

                existing = next(
                    (
                        item
                        for item in candidates
                        if math.hypot(item["center_x"] - icon_center_x, item["center_y"] - icon_center_y)
                        <= merge_distance
                    ),
                    None,
                )
                if existing is not None:
                    if state == "gold":
                        existing["state"] = "gold"
                    if score > existing["score"]:
                        existing.update(
                            x=x,
                            y=y,
                            width=template_width,
                            height=template_height,
                            center_x=icon_center_x,
                            center_y=icon_center_y,
                            radius=ellipse_radius,
                            score=score,
                        )
                    continue

                candidates.append(
                    {
                        "x": x,
                        "y": y,
                        "width": template_width,
                        "height": template_height,
                        "center_x": icon_center_x,
                        "center_y": icon_center_y,
                        "radius": ellipse_radius,
                        "state": state,
                        "score": score,
                    }
                )
                if len(candidates) >= 32:
                    break

            if callable(log_info):
                log_info(
                    f"[zipline-template] state={source_state} best={best_score:.3f} "
                    f"threshold={self._ZIP_LINE_TEMPLATE_THRESHOLD:.3f} raw_hits={raw_hit_count} "
                    f"accepted_delta={len(candidates) - candidate_count_before}"
                )

        # 白色候选优先于黄色/金黄色；同色候选优先转动更少、匹配更高的目标。
        candidates.sort(key=lambda item: (0 if item["state"] == "white" else 1, item["radius"], -item["score"]))
        if callable(log_info):
            summary = ", ".join(
                f"{item['state']}@({item['center_x']:.0f},{item['center_y']:.0f}) "
                f"r={item['radius']:.2f} s={item['score']:.3f}"
                for item in candidates[:12]
            )
            log_info(f"[zipline-template] candidates={len(candidates)} [{summary}]")
        return candidates

    def _zip_line_distance_box(self, candidate):
        """构造图标下方的距离 OCR 区域；1080p 基准为 70×30px，随分辨率缩放。"""
        center_x = candidate["center_x"]
        center_y = candidate["center_y"]
        half_width = self.scale_distance(35)
        top_offset = self.scale_distance(28)
        bottom_offset = self.scale_distance(58)
        left = max(0, round(center_x - half_width))
        right = min(self.width, round(center_x + half_width))
        top = max(0, round(center_y + top_offset))
        bottom = min(self.height, round(center_y + bottom_offset))
        return self.box_of_screen(left / self.width, top / self.height, right / self.width, bottom / self.height)

    def _find_zip_line_template_target(self, zip_line, frame, search_box=None, *, distance_tolerance=None):
        """保持白色候选优先，并在局部分色 OCR 漏检时逐级放宽。"""
        pattern = (
            self._zip_line_distance_pattern(zip_line)
            if distance_tolerance is None
            else self._zip_line_distance_matcher(zip_line, distance_tolerance)
        )
        candidates = self._detect_zip_line_icon_candidates(
            frame, **({"search_box": search_box} if search_box is not None else {})
        )
        log_info = getattr(self, "log_info", None)
        if callable(log_info):
            log_info(f"[zipline-template] target={zip_line} candidate_count={len(candidates)}")
        combined_range = tuple(hR.WHITE) + tuple(hR.GOLD_TEXT)
        for index, candidate in enumerate(candidates):
            hsv_range = hR.WHITE if candidate["state"] == "white" else hR.GOLD_TEXT
            distance_box = self._zip_line_distance_box(candidate)
            if callable(log_info):
                log_info(
                    f"[zipline-template] target={zip_line} try={index + 1}/{len(candidates)} "
                    f"state={candidate['state']} center=({candidate['center_x']:.0f},{candidate['center_y']:.0f}) "
                    f"r={candidate.get('radius', float('inf')):.2f} score={candidate.get('score', 0.0):.3f} "
                    f"distance_box=({distance_box.x},{distance_box.y},{distance_box.width},{distance_box.height}) "
                    f"processor={'WHITE' if candidate['state'] == 'white' else 'GOLD_TEXT'}"
                )
            result = self.ocr(
                match=pattern,
                box=distance_box,
                frame=frame,
                frame_processor=self.make_hsv_isolator(hsv_range),
                log=True,
            )
            if not result:
                if callable(log_info):
                    log_info(
                        f"[zipline-template] target={zip_line} local_ocr=miss "
                        f"state={candidate['state']} center=({candidate['center_x']:.0f},{candidate['center_y']:.0f}) "
                        "fallback=white+gold"
                    )
                result = self.ocr(
                    match=pattern,
                    box=distance_box,
                    frame=frame,
                    frame_processor=self.make_hsv_isolator(combined_range),
                    log=True,
                )
            if not result:
                if callable(log_info):
                    log_info(
                        f"[zipline-template] target={zip_line} local_ocr=miss "
                        f"state={candidate['state']} center=({candidate['center_x']:.0f},{candidate['center_y']:.0f}) "
                        "fallback=raw"
                    )
                result = self.ocr(
                    match=pattern,
                    box=distance_box,
                    frame=frame,
                    frame_processor=None,
                    log=True,
                )
            if result:
                target = result[0] if isinstance(result, list) else result
                if callable(log_info):
                    log_info(
                        f"[zipline-template] target={zip_line} local_ocr=hit "
                        f"state={candidate['state']} center=({candidate['center_x']:.0f},{candidate['center_y']:.0f})"
                    )
                return candidate, target
        if callable(log_info):
            log_info(f"[zipline-template] target={zip_line} no_candidate_with_matching_distance")
        return None

    def _zip_line_move_profile(self, ellipse_radius):
        """椭圆内半径正常对中，外半圈随离中心程度平滑加速。"""
        return ellipse_move_profile(
            ellipse_radius, inner_radius=self._ZIP_LINE_INNER_RADIUS, outer_boost=self._ZIP_LINE_OUTER_MAX_BOOST
        )

    @staticmethod
    def _zip_line_alignment_failed(raise_if_fail):
        """保持通用对中 helper 的失败语义。"""
        if raise_if_fail:
            raise Exception("对中失败")
        return False

    def _move_to_zip_line_target(self, target):
        """在椭圆坐标中计算方向与距离，以屏幕宽度统一缩放横纵步长。"""
        dx, dy = hud_direction_step(
            (target.x + target.width // 2, target.y + target.height // 2),
            self._zip_line_ellipse_geometry(),
            self.width,
            reference_radius_x=self._ZIP_LINE_ELLIPSE_RX,
            inner_radius=self._ZIP_LINE_INNER_RADIUS,
            outer_boost=self._ZIP_LINE_OUTER_MAX_BOOST,
        )
        if (dx or dy) and not self.active_and_send_mouse_delta(dx, dy, steps=5, delay=0.005):
            return 0, 0
        return dx, dy

    def _zip_line_target_is_gold_and_centered(self, zip_line, frame=None, tolerance=50, *, distance_tolerance=None):
        """判断目标距离是否处于黄色锁定态且位于屏幕中心附近。"""
        result = self.ocr(
            match=(
                self._zip_line_distance_pattern(zip_line)
                if distance_tolerance is None
                else self._zip_line_distance_matcher(zip_line, distance_tolerance)
            ),
            frame=frame if frame is not None else self.next_frame(),
            frame_processor=self.make_hsv_isolator(hR.GOLD_TEXT),
        )
        if not result:
            return False

        screen_center_x, screen_center_y = self.screen_center()
        scaled_tolerance = self.scale_distance(tolerance)
        numeric_y_offset = int(self.height * ((525 - 486) / 1080))
        for target in result:
            target_center_x = target.x + target.width // 2
            target_center_y = target.y - numeric_y_offset + target.height // 2
            if (
                abs(target_center_x - screen_center_x) <= scaled_tolerance
                and abs(target_center_y - screen_center_y) <= scaled_tolerance
            ):
                return True
        return False

    def _align_zip_line_distance_ocr(
        self,
        zip_line,
        need_scroll=None,
        tolerance=50,
        max_time=100,
        raise_if_fail=True,
        deadline=None,
        max_iterations=None,
        ocr_target_detector=None,
        ocr_target_mover=None,
        learning_session=None,
    ):
        """统一模板/全屏 OCR 对中入口，并允许调用方观察实际鼠标位移。"""

        def align_once(helper_max_time, helper_raise_if_fail, helper_deadline=None, helper_max_iterations=None):
            return self.align_ocr_or_find_target_to_center(
                self._zip_line_distance_pattern(zip_line),
                is_num=True,
                need_scroll=need_scroll,
                ocr_frame_processor_list=[
                    self.make_hsv_isolator(hR.GOLD_TEXT),
                    self.make_hsv_isolator(hR.WHITE),
                ],
                tolerance=tolerance,
                max_time=helper_max_time,
                raise_if_fail=helper_raise_if_fail,
                deadline=helper_deadline,
                max_iterations=helper_max_iterations,
                ocr_target_detector=ocr_target_detector,
                ocr_target_mover=ocr_target_mover or self._move_to_zip_line_target,
                **({"learning_session": learning_session} if learning_session is not None else {}),
            )

        if deadline is None and max_iterations is None:
            return align_once(max_time, raise_if_fail)

        remaining_iterations = max_time * 2 if max_iterations is None else max(0, int(max_iterations))
        if remaining_iterations <= 0 or (deadline is not None and self.active_time() >= deadline):
            return self._zip_line_alignment_failed(raise_if_fail)

        result = align_once(
            1,
            False,
            helper_deadline=deadline,
            helper_max_iterations=remaining_iterations,
        )
        if result:
            return result
        return self._zip_line_alignment_failed(raise_if_fail)

    def _align_zip_line_distance(
        self,
        zip_line,
        need_scroll=None,
        tolerance=50,
        max_time=100,
        raise_if_fail=True,
        deadline=None,
        *,
        distance_tolerance=None,
        initial_move=None,
        learning_session=None,
    ):
        """先用历史位移预送一次，再让模板与 OCR fallback 共用同一个可观测移动闭环。

        ``initial_move`` 只在本段第一次检测时用掉一次——之后的重新对中要靠当前画面判断，
        再送一次就等于把已经修正过的偏差又加上去。
        """
        if max_time <= 0 or (deadline is not None and self.active_time() >= deadline):
            return self._zip_line_alignment_failed(raise_if_fail)

        prefeed_move = initial_move if learning_allowed() else None

        misses = 0
        pattern = (
            self._zip_line_distance_pattern(zip_line)
            if distance_tolerance is None
            else self._zip_line_distance_matcher(zip_line, distance_tolerance)
        )
        processors = [self.make_hsv_isolator(hR.GOLD_TEXT), self.make_hsv_isolator(hR.WHITE)]

        def detect_target(frame):
            nonlocal misses, prefeed_move
            if prefeed_move is not None:
                try:
                    prefeed_dx = int(prefeed_move[0])
                    prefeed_dy = int(prefeed_move[1])
                except (TypeError, ValueError, IndexError, OverflowError):
                    prefeed_dx = 0
                    prefeed_dy = 0
                prefeed_move = None
                if prefeed_dx or prefeed_dy:
                    start_reading = self._read_zip_line_arrow_angle() if prefeed_dx else None
                    try:
                        sent = self.active_and_send_mouse_delta(
                            dx=prefeed_dx,
                            dy=prefeed_dy,
                            activate=True,
                            steps=5,
                        )
                    except Exception as e:
                        sent = False
                        self.log_info(f"[zipline-motion-cache] prefeed failed: {e}")
                    if sent:
                        self.log_info(
                            f"[zipline-motion-cache] target={zip_line} prefeed move=({prefeed_dx},{prefeed_dy})"
                        )
                        if prefeed_dx:
                            self._wait_zip_line_prefeed_settled(start_reading, prefeed_dx)
                        else:
                            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
                        frame = self.next_frame()

            target_kwargs = {}
            if distance_tolerance is not None:
                target_kwargs["distance_tolerance"] = distance_tolerance
            if learning_session is not None:
                matched = learning_session.detect_target(
                    lambda search_box: self._find_zip_line_template_target(
                        zip_line,
                        frame,
                        **({"search_box": search_box} if search_box is not None else {}),
                        **target_kwargs,
                    )
                )
            else:
                matched = self._find_zip_line_template_target(
                    zip_line,
                    frame,
                    **target_kwargs,
                )
            if matched is None:
                misses += 1
                if misses < self._ZIP_LINE_TEMPLATE_MISS_LIMIT:
                    return None
                if misses == self._ZIP_LINE_TEMPLATE_MISS_LIMIT:
                    self.log_info(
                        f"滑索{zip_line}连续{misses}次未找到白色/黄色模板目标，本轮回退全屏OCR，后续继续尝试模板"
                    )
                for processor in processors:
                    if deadline is not None and self.active_time() >= deadline:
                        return None
                    result = self.ocr(
                        match=pattern,
                        frame=frame,
                        log=True,
                        frame_processor=processor,
                    )
                    if result:
                        target = copy(result[0])
                        target.y -= int(self.height * ((525 - 486) / 1080))
                        return target
                return []

            if misses >= self._ZIP_LINE_TEMPLATE_MISS_LIMIT:
                self.log_info(f"滑索{zip_line}恢复模板OCR快速对中")
            misses = 0
            candidate, ocr_target = matched
            move_target = copy(ocr_target)
            move_target.x = candidate["x"]
            move_target.y = candidate["y"]
            move_target.width = candidate["width"]
            move_target.height = candidate["height"]
            return move_target

        return self._align_zip_line_distance_ocr(
            zip_line,
            need_scroll=need_scroll,
            tolerance=tolerance,
            max_time=max_time,
            raise_if_fail=raise_if_fail,
            deadline=deadline,
            max_iterations=max_time * 2,
            ocr_target_detector=detect_target,
            **({"learning_session": learning_session} if learning_session is not None else {}),
        )

    def _zip_line_stop_state(self):
        """滑索停稳提示的匹配项与状态栏区域。

        区域直接写 ``box_of_screen`` 的字面量而不是走 :meth:`_zip_line_status_box`：两者取值
        完全相同，但上游的测试替身只补了 ``box_of_screen``。
        """
        return (
            [
                self.lang.zip_line_mixin.k_2f4f4a2f,
                self.lang.zip_line_mixin.k_0b1e4f35,
            ],
            self.box_of_screen(0.351, 0.943, 0.657, 0.981),
        )

    def _try_click_on_zip_line(
        self, zip_line, max_attempts=5, lock_timeout=2, *, distance_tolerance=None, on_gate_confirmed=None
    ):
        """执行一次黄色门控 + 鼠标左键点击，返回 ``(成功, 失败原因)``。"""
        lock_start = self.active_time()
        while True:
            gate_frame = self.next_frame()
            if self._zip_line_target_is_gold_and_centered(
                zip_line, frame=gate_frame, tolerance=50, distance_tolerance=distance_tolerance
            ):
                # Observe the gate before clicking/E changes the scene or taints the learning session.
                if on_gate_confirmed is not None:
                    on_gate_confirmed(gate_frame)
                break
            if self.active_time() - lock_start >= lock_timeout:
                return False, "gate"
            self.sleep(0.05)

        self.click(after_sleep=0.1)
        return True, None

    def _legacy_click_on_zip_line(self, max_attempts=5):
        """黄色门控耗尽后只点击鼠标左键，由调用方等待实际移动结果。"""
        self.click(after_sleep=0.1)
        return True

    def _zip_line_motion_route_fingerprint(self, zip_line_list, config_key=None):
        """Resolve the exact config key + full list identity for motion reuse."""
        route_values = tuple(int(value) for value in zip_line_list)
        if config_key is None:
            pending = getattr(self, "_pending_zip_line_motion_route", None)
            if pending and pending[1] == route_values:
                config_key = pending[0]
        self._pending_zip_line_motion_route = None
        return build_zip_line_route_fingerprint(config_key, route_values)

    @staticmethod
    @staticmethod
    def _zip_line_angle_delta(target_yaw, current_yaw):
        """Return the shortest signed yaw delta in (-180, 180]."""
        return (float(target_yaw) - float(current_yaw) + 180.0) % 360.0 - 180.0

    def _read_zip_line_arrow_angle(self):
        """Read the minimap arrow without low-score smoothing so stale yaw cannot pass the gate."""
        try:
            angle, score = self.get_arrow_angle(
                two_stage=True,
                benchmark_width=2560,
                smoothing_threshold=None,
            )
            angle = float(angle)
            score = 0.0 if score is None else float(score)
        except Exception as e:
            self.log_info(f"[zipline-yaw] read failed: {e}")
            return None

        if not math.isfinite(angle) or not math.isfinite(score):
            return None
        return angle % 360.0, score

    def _read_zip_line_settled_arrow_angle(self):
        """Wait for the character arrow to finish following the camera's yaw.

        TravelPoleBrain.LateTick rotates the handrail toward the camera before
        copying its stand-point rotation to the character. A single fresh frame
        can therefore still contain an intermediate yaw after a large turn.
        """
        deadline = self.active_time() + self._ZIP_LINE_ARROW_SETTLE_TIMEOUT
        previous_yaw = None
        stable_samples = 0
        while self.active_time() < deadline:
            reading = self._read_zip_line_arrow_angle()
            if reading is None or reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
                return None
            if self.active_time() >= deadline:
                break
            yaw, _ = reading
            if (
                previous_yaw is not None
                and abs(self._zip_line_angle_delta(yaw, previous_yaw)) <= self._ZIP_LINE_ARROW_STABLE_TOLERANCE
            ):
                stable_samples += 1
            else:
                stable_samples = 1
            if stable_samples >= self._ZIP_LINE_ARROW_STABLE_SAMPLES:
                return reading
            previous_yaw = yaw
            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
        self.log_info("[zipline-yaw] arrow did not settle; history disabled")
        return None

    def _wait_zip_line_prefeed_settled(self, start_reading, prefeed_dx):
        """Wait until replayed horizontal motion starts and then the arrow settles again."""
        environment = yaw_environment(self.width, self.height, start_reading[0] if start_reading else 0)
        expected_yaw_delta = abs(
            float(prefeed_dx) * environment.horizontal_yaw_gain(self._ZIP_LINE_ARROW_YAW_PER_MOUSE)
        )
        if expected_yaw_delta < 1.0:
            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
            return True
        if start_reading is None or start_reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
            self.log_info("[zipline-motion-cache] prefeed settle skipped: unreliable starting yaw")
            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
            return False

        start_yaw = start_reading[0]
        deadline = self.active_time() + self._ZIP_LINE_ARROW_SETTLE_TIMEOUT
        movement_started = False
        previous_yaw = None
        stable_samples = 0
        while self.active_time() < deadline:
            reading = self._read_zip_line_arrow_angle()
            if reading is None or reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
                self.log_info("[zipline-motion-cache] prefeed settle lost reliable yaw; visual alignment continues")
                return False
            if self.active_time() >= deadline:
                break
            yaw, _score = reading
            if not movement_started:
                if abs(self._zip_line_angle_delta(yaw, start_yaw)) < 1.0:
                    self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
                    continue
                movement_started = True
                previous_yaw = yaw
                stable_samples = 1
            else:
                if abs(self._zip_line_angle_delta(yaw, previous_yaw)) <= self._ZIP_LINE_ARROW_STABLE_TOLERANCE:
                    stable_samples += 1
                else:
                    stable_samples = 1
                previous_yaw = yaw
                if stable_samples >= self._ZIP_LINE_ARROW_STABLE_SAMPLES:
                    self.log_info(
                        f"[zipline-motion-cache] prefeed settled start_yaw={start_yaw:.1f} final_yaw={yaw:.1f}"
                    )
                    return True
            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)

        self.log_info("[zipline-motion-cache] prefeed settle timeout; visual alignment continues")
        return False

    def _send_zip_line_yaw_delta(self, dx):
        """Send one horizontal turn and report rejected input without blocking alignment."""
        try:
            return bool(
                self.active_and_send_mouse_delta(
                    dx=int(dx),
                    dy=0,
                    activate=True,
                    steps=1,
                    delay=0,
                )
            )
        except Exception as e:
            self.log_info(f"[zipline-yaw] mouse move failed dx={dx}: {e}")
            return False

    def _normalize_zip_line_segment_yaw(self, target_yaw, initial_reading=None):
        """Turn to a reusable starting-yaw bucket, verifying fresh, settled frames.

        This is part of the complete segment motion; the caller observes all
        accepted input, including cached turns and rollback, under the original yaw.
        """
        width, height = self.width, self.height
        reading = initial_reading if initial_reading is not None else self._read_zip_line_settled_arrow_angle()
        if reading is None or reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
            return None, False
        current_yaw, _score = reading
        initial_yaw = current_yaw
        target_yaw = float(target_yaw) % 360.0
        # Stay inside the 5-degree history bucket rather than accepting its neighbor.
        tolerance = min(self._ZIP_LINE_ARROW_TOLERANCE, 2.0)
        error = abs(self._zip_line_angle_delta(target_yaw, current_yaw))
        if error <= tolerance:
            return current_yaw, True

        total_dx = 0
        scope = vars(self).get("_initial_alignment_scope")
        scroll = scope.session.environment.scroll if scope is not None else False
        environment = yaw_environment(width, height, initial_yaw, scroll=scroll)
        yaw_per_mouse = environment.horizontal_yaw_gain(self._ZIP_LINE_ARROW_YAW_PER_MOUSE)
        hint = (
            load_arrow_turn_motion(width, height, current_yaw, target_yaw, environment=environment)
            if learning_allowed()
            else None
        )
        if hint is not None:
            hint_dx, hint_samples = hint
            if self._send_zip_line_yaw_delta(hint_dx):
                total_dx += hint_dx
                self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
                reading = self._read_zip_line_settled_arrow_angle()
                if reading is None or reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
                    self._send_zip_line_yaw_delta(-total_dx)
                    self.log_info("[zipline-yaw] cached turn verification failed; rolled back")
                    return target_yaw, False
                next_yaw, _next_score = reading
                next_error = abs(self._zip_line_angle_delta(target_yaw, next_yaw))
                self.log_info(
                    f"[zipline-yaw] cached turn samples={hint_samples} "
                    f"dx={hint_dx} yaw={current_yaw:.1f}->{next_yaw:.1f} "
                    f"error={error:.1f}->{next_error:.1f}"
                )
                if next_error > tolerance and next_error >= error - 0.5:
                    self._send_zip_line_yaw_delta(-total_dx)
                    self.log_info("[zipline-yaw] cached turn did not improve; rolled back")
                    return target_yaw, False
                current_yaw = next_yaw
                error = next_error
            else:
                # A sender exception can follow partial input. Do not learn a
                # subsequent turn whose starting position is now uncertain.
                return target_yaw, False

        for _ in range(self._ZIP_LINE_ARROW_MAX_STEPS):
            if error <= tolerance:
                break
            desired_delta = self._zip_line_angle_delta(target_yaw, current_yaw)
            step_degrees = max(
                -self._ZIP_LINE_ARROW_MAX_STEP_DEGREES,
                min(self._ZIP_LINE_ARROW_MAX_STEP_DEGREES, desired_delta),
            )
            move_dx = round(step_degrees / yaw_per_mouse)
            if move_dx == 0 or not self._send_zip_line_yaw_delta(move_dx):
                break

            total_dx += move_dx
            previous_error = error
            self.sleep(self._ZIP_LINE_ARROW_SETTLE_DELAY)
            reading = self._read_zip_line_settled_arrow_angle()
            if reading is None or reading[1] < self._ZIP_LINE_ARROW_MIN_SCORE:
                break

            next_yaw, _score = reading
            error = abs(self._zip_line_angle_delta(target_yaw, next_yaw))
            self.log_info(
                f"[zipline-yaw] calibrated step dx={move_dx} "
                f"yaw={current_yaw:.1f}->{next_yaw:.1f} error={previous_error:.1f}->{error:.1f}"
            )
            current_yaw = next_yaw
            if error >= previous_error - 0.5:
                break

        if error > tolerance:
            if total_dx:
                self._send_zip_line_yaw_delta(-total_dx)
            self.log_info(
                f"[zipline-yaw] normalize failed target={target_yaw:.1f} error={error:.1f}; visual alignment continues"
            )
            return target_yaw, False

        if total_dx:
            if learning_allowed() and environment == yaw_environment(width, height, initial_yaw, scroll=scroll):
                record_arrow_turn_motion(width, height, initial_yaw, target_yaw, total_dx, environment=environment)
        self.log_info(
            f"[zipline-yaw] normalized {initial_yaw:.1f}->{current_yaw:.1f} target={target_yaw:.1f} dx={total_dx}"
        )
        return current_yaw, True

    def _align_and_activate_zip_line(
        self, zip_line, need_scroll=None, initial_move=None, learning_context=None, *, distance_tolerance=None
    ):
        """Commit a clean complete initial movement only when its gold gate is verified."""
        scope = (
            zipline_initial_alignment(
                self,
                learning_context,
                zip_line,
                need_scroll=need_scroll,
                **({"distance_tolerance": distance_tolerance} if distance_tolerance is not None else {}),
            )
            if learning_context is not None
            else nullcontext(None)
        )
        with scope as learning:
            if learning_context is None:
                self._align_zip_line_distance(
                    zip_line,
                    need_scroll=need_scroll,
                    tolerance=50,
                    initial_move=initial_move,
                    **({"distance_tolerance": distance_tolerance} if distance_tolerance is not None else {}),
                )
            self.log_info(f"成功将滑索调整到{zip_line}的中心")

            gate_retry_start = self.active_time()
            gate_retry_deadline = gate_retry_start + 20
            activated = False
            last_reason = None
            for attempt in range(3):
                on_gate = (
                    learning.confirm_zipline_gold_gate if learning is not None and not learning.session.closed else None
                )
                activated, last_reason = self._try_click_on_zip_line(
                    zip_line,
                    **({"distance_tolerance": distance_tolerance} if distance_tolerance is not None else {}),
                    **({"on_gate_confirmed": on_gate} if on_gate is not None else {}),
                )
                if activated:
                    break
                if last_reason == "gate":
                    self.log_info(f"滑索{zip_line}金色门未通过，重新对中后重试（{attempt + 1}/3）")
                else:
                    self.log_info(f"滑索{zip_line}未能触发，重新对中后重试（{attempt + 1}/3）")
                if attempt >= 2 or self.active_time() >= gate_retry_deadline:
                    break
                # Retry movement belongs to the same original-entry sample if the gate
                # has not passed. The entry zoom has already been applied once.
                retry_learning = learning if on_gate is not None else None
                realigned = self._align_zip_line_distance(
                    zip_line,
                    need_scroll=False if retry_learning is not None else need_scroll,
                    tolerance=50,
                    max_time=10,
                    raise_if_fail=False,
                    deadline=gate_retry_deadline,
                    **({"distance_tolerance": distance_tolerance} if distance_tolerance is not None else {}),
                    **({"learning_session": retry_learning} if retry_learning is not None else {}),
                )
                if not realigned:
                    last_reason = "gate"
                    self.log_info(f"滑索{zip_line}重新对中失败，进入失败处理")
                    break

            if not activated:
                # A direct-click fallback has no gold-gate evidence and must not train.
                if learning is not None and not learning.session.closed:
                    learning.fail()
                if last_reason == "gate":
                    self.log_info(f"滑索{zip_line}金色门重试耗尽，回退直接点击鼠标左键")
                    if not self._legacy_click_on_zip_line():
                        self.log_info(f"滑索{zip_line}直接点击鼠标左键失败", notify=True)
                        raise RuntimeError(f"滑索{zip_line}直接点击鼠标左键失败")
                else:
                    self.log_info(f"滑索{zip_line}交互失败", notify=True)
                    raise RuntimeError(f"滑索{zip_line}交互失败")

    def _ride_zip_line_by_ocr(self, zip_line, tolerance, *, need_scroll=None, initial_move=None):
        """用距离 OCR 对中并点击滑行；保留自动送货的兼容路径。

        对中走模板 OCR（``_align_zip_line_distance``），点击走黄色门控重试，两道都不过才回退
        直接点击鼠标左键。网格导航传入的距离容差会一路传给对中与门控，用于吸收地图坐标与游戏
        显示距离之间的偏差。``initial_move`` 是该段学到的历史鼠标位移，只在对中第一步预送。
        """
        self._align_and_activate_zip_line(
            zip_line,
            need_scroll=need_scroll,
            initial_move=initial_move,
            distance_tolerance=tolerance,
        )

        stop_match, stop_box = self._zip_line_stop_state()
        start = self.active_time()
        while True:
            self.next_frame()
            self.sleep(0.1)
            if self.ocr(match=stop_match, box=stop_box, log=True):
                return
            if self.active_time() - start > 240:
                raise RuntimeError("滑索超时，强制退出")

    def zip_line_list_go(
        self,
        zip_line_list,
        need_scroll=None,
        target=None,
        need_v=False,
        distance_tolerance=None,
        target_bearing=None,
        target_positions=None,
        source_positions_xyz=None,
        target_positions_xyz=None,
        stay_on_zip_line=False,
        config_key=None,
        route_family=(),
        target_learning_context=None,
        target_learning_epoch=None,
    ):
        """按顺序对齐滑索并执行滑行

        每段先选路：网格导航给了世界方位角就走**直接对准**（只转视角、不产生位移），否则走
        模板/OCR 对中。仅模板/OCR 初次对中使用主线的上下文学习；激活、骑乘和直接对准不记录样本。

        Args:
            zip_line_list: 滑索距离列表
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪
            distance_tolerance: 距离匹配容差（米）。地图坐标与游戏显示距离不一致时，
                会按该半径依次匹配可见距离，普通送货仍保持精确匹配。
            target_bearing: 当前滑索到下一滑索的世界方位角。提供后会先只转动视角
                做横向对准，不按 W，不会让滑索上的角色发生位移。
            target_positions: 每段滑索目标滑索架的世界坐标列表。
            source_positions_xyz: 每段滑索三维起点列表，仅用于失败时精确标记连接。
            target_positions_xyz: 每段滑索三维终点列表，仅用于失败时精确标记连接。
            stay_on_zip_line: 路线结束后留在滑索上，供下一段路线继续测试。
            config_key: 本段路线在滑索配置里的键名。历史位移按 (配置键, 距离序列) 归档；
                不传时回退到最近一次 get_zip_line_config_value 读到的键。
        """
        bearings = self._normalize_values(target_bearing, scalar=True)
        positions = self._normalize_values(target_positions, scalar=True)
        tolerances = self._normalize_values(distance_tolerance, scalar=True)
        source_positions_3d = self._normalize_values(source_positions_xyz)
        target_positions_3d = self._normalize_values(target_positions_xyz)
        target_mode = self.zip_line_target_select_mode()
        route_fingerprint = self._zip_line_motion_route_fingerprint(zip_line_list, config_key=config_key)
        stop_match, stop_box = self._zip_line_stop_state()
        if not learning_allowed():
            route_fingerprint = None
        for segment_index, zip_line in enumerate(zip_line_list):
            tolerance = self._value_for_index(
                tolerances,
                segment_index,
                default=distance_tolerance,
            )
            bearing = self._value_for_index(bearings, segment_index)
            target_position = self._value_for_index(positions, segment_index)
            source_xyz = self._value_for_index(source_positions_3d, segment_index)
            target_xyz = self._value_for_index(target_positions_3d, segment_index)
            if target_mode == ZIP_LINE_TARGET_SELECT_DIRECT and bearing is not None:
                try:
                    if not self._direct_zip_line_go(bearing, target_position):
                        raise RuntimeError(f"直接对准滑索目标失败: {float(bearing):.1f}°")
                except ZipLineReplanRequired as exc:
                    if source_xyz is not None:
                        exc.current_position = tuple(float(value) for value in source_xyz)
                    if target_xyz is not None:
                        exc.failed_target_position = tuple(float(value) for value in target_xyz)
                    raise
                continue
            if route_fingerprint and need_scroll:
                # Make zoom a reproducible entry before measuring the original yaw/input.
                self.do_scroll(1, 400)
                self.next_frame()
            reading = self._read_zip_line_settled_arrow_angle() if route_fingerprint else None
            start_yaw = reading[0] if reading is not None and reading[1] >= self._ZIP_LINE_ARROW_MIN_SCORE else None
            context = route_context_for(
                self,
                route_fingerprint,
                segment_index,
                start_yaw,
                need_scroll=need_scroll,
                family=route_family,
            )
            self._align_and_activate_zip_line(
                zip_line,
                need_scroll=need_scroll,
                initial_move=None,
                **({"learning_context": context} if context is not None else {}),
                **({"distance_tolerance": tolerance} if tolerance is not None else {}),
            )

            start = self.active_time()
            while True:
                self.next_frame()
                if bearing is None:
                    # 距离序列可能跨过中间连接点，沿用旧送货路线的 E 连按；网格导航逐点到达。
                    self.send_key("e")  # 滑索继续移动键无法在游戏内改绑。
                self.sleep(0.1)
                if self.ocr(match=stop_match, box=stop_box, log=True):
                    break
                if self.active_time() - start > 240:
                    raise RuntimeError("滑索超时，强制退出")
        if stay_on_zip_line:
            self.log_info("滑索路线结束，留在滑索上继续测试")
            return
        if need_v:
            self.click(key="right", after_sleep=2)
        if target:
            result_name = target[0]
            result_type = target[1]
            if result_type == "ocr":
                ocr_bool = True
                yolo_bool = False
            elif result_type == "yolo":
                ocr_bool = False
                yolo_bool = True
            else:
                ocr_bool = False
                yolo_bool = False
            if need_v:
                self.ensure_main()

                def check_board():
                    return self.wait_ocr(
                        match=self.lang.zip_line_mixin.k_b0e3a2da,
                        box=self.box.bottom_right,
                        settle_time=1,
                        time_out=4,
                        log=True,
                    )

                result = self.strafe_search(
                    check_board,
                    passes=1,
                    duration=0.1,
                    keys=("s", "w", "a", "d"),  # 后退优先：落点常越过滑索架，后退最容易重新看到
                )
                if result:
                    self.press_key("v", after_sleep=1)
                    self.click_with_alt(result[0], after_sleep=2)
            else:
                result = True
            if result:
                context = (
                    target_learning_context
                    if (
                        learning_allowed()
                        and (target_learning_epoch is None or target_learning_epoch == learning_epoch())
                        and target_learning_context is not None
                        and target_learning_context.entry_route_signature == tuple(zip_line_list)
                        and (
                            (need_v and target_learning_context.entry != "after_zipline_on_board")
                            or (
                                not need_v
                                and target_learning_context.task == "gather"
                                and target_learning_context.phase == "after_zipline"
                                and target_learning_context.entry == "after_zipline_on_board"
                                and bool(zip_line_list)
                            )
                        )
                    )
                    else None
                )
                confidence = "high"
                if context is not None:
                    reading = self._read_zip_line_settled_arrow_angle()
                    verified_yaw = reading is not None and reading[1] >= self._ZIP_LINE_ARROW_MIN_SCORE
                    if verified_yaw:
                        self._learning_start_yaw = float(reading[0]) % 360.0
                    context = replace(context, start_yaw_bucket=yaw_bucket(reading[0]) if verified_yaw else None)
                    confidence = "high" if verified_yaw else "medium"
                    if target_learning_epoch is not None and target_learning_epoch != learning_epoch():
                        context = None
                align_initial_target(
                    self,
                    context,
                    result_name,
                    confidence=confidence,
                    threshold=ZIP_LINE_TEMPLATE_THRESHOLD,
                    ocr=ocr_bool,
                    use_yolo=yolo_bool,
                    raise_if_fail=False,
                    ocr_target_mover=self._move_to_zip_line_target if ocr_bool else None,
                    move_profile="world_projection" if yolo_bool else "hud_direction",
                )
                self.click(key="right")
        stop_match, stop_box = self._zip_line_stop_state()
        if self.wait_ocr(
            match=stop_match,
            box=stop_box,
            log=True,
            time_out=2,
        ):
            self.click(key="right", after_sleep=2)
        self.log_info("滑索结束")
        self.ensure_main()

    def ensure_click_on_zip_line(self, zip_line, max_attempts=5, lock_timeout=2, *, distance_tolerance=None):
        """兼容入口：一次黄色门控 + 鼠标左键点击，失败由调用方决定是否重新对中。"""
        success, _ = self._try_click_on_zip_line(
            zip_line,
            max_attempts=max_attempts,
            lock_timeout=lock_timeout,
            distance_tolerance=distance_tolerance,
        )
        return success
