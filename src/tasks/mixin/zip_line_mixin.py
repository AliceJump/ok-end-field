"""滑索交互、对准和场景状态判定。

本模块同时服务自动送货与网格导航：

- 自动送货按配置中的距离序列使用 OCR 对中；
- 网格导航优先使用世界方位直接对准，误差收敛到 ±3° 后执行；
- 两者共用「登上滑索架」模板匹配、错误落点判定和 WS 状态检查。

滑索架状态只以 ``move_to_the_target`` / ``move_to_the_target_2`` 模板为准；
融合坐标不参与上下索判断。
"""

import math
import re

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
from src.core.sequence_parser import parse_int_sequence
from src.data.FeatureList import FeatureList as fL
from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.mixin.navigation_mixin import NavigationMixin

ZIP_LINE_TEMPLATE_THRESHOLD = 0.8
ZIP_LINE_STATUS_BOX = (0.351, 0.943, 0.657, 0.981)
ZIP_LINE_DIRECT_TOLERANCE_DEG = 3.0
ZIP_LINE_DIRECT_PITCH_STEPS = (0, 24, -24, 48, -48, 0, 24, -24, 48, -48)
ZIP_LINE_MOTION_POLL_SECONDS = 0.2
ZIP_LINE_MOTION_CHANGE_METERS = 0.5


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
        if key not in override:
            return base_value
        return self._coerce_override_value(base_value, override[key])

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
        service_getter = getattr(self, "_get_minimap_position_service", None)
        if not callable(service_getter):
            return None
        try:
            service = service_getter()
            if service is None:
                return None
            sample_frame = frame if frame is not None else self.next_frame()
            state = service.minimap_position(
                frame=sample_frame,
                now=self.active_time(),
            )
            position = state.get("ws")
            if position is None:
                return None
            return float(position[0]), float(position[1])
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
    ):
        """按目标模板和原始 WS 坐标判断滑索状态。

        - 模板不存在：正在滑索间移动，继续等待。
        - 模板存在且 WS 到达目标附近：进入下一段。
        - 模板存在且 WS 仍在起点附近：返回失败，交给外层重试。
        - 停稳后既不在起点也不在目标附近：到达错误滑索架，重新规划。

        调用方负责点击并发送一次 E；本方法只轮询状态，不重复发送按键。
        """
        if target_position is None:
            return False
        start = self.active_time()
        moving_logged = False
        last_position: tuple[float, float] | None = None
        last_position_changed_at = start
        threshold = max(0.1, float(near_distance))
        stable_seconds = max(0.1, float(stable_seconds))
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

            if on_rack and start_distance is not None and start_distance <= threshold:
                self.log_info(f"到达模板存在且 WS 仍在起点附近：距离={start_distance:.2f}m，准备重试")
                return False

            if on_rack and self.active_time() - last_position_changed_at >= stable_seconds:
                raise ZipLineReplanRequired(
                    "到达了非目标滑索架："
                    f"WS=({current_position[0]:.2f}, {current_position[1]:.2f})，"
                    f"距起点={start_distance if start_distance is None else round(start_distance, 2)}m，"
                    f"距目标={target_distance:.2f}m",
                    current_position=current_position,
                )

            if self.active_time() - last_position_changed_at >= stable_seconds:
                if target_distance <= threshold:
                    self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
                    continue
                if start_distance is not None and start_distance <= threshold:
                    self.log_warning("WS 已在起点附近停稳但未触发滑索，准备重试")
                    return False
                raise ZipLineReplanRequired(
                    "滑索结束在非目标滑索架："
                    f"WS=({current_position[0]:.2f}, {current_position[1]:.2f})，"
                    f"距起点={start_distance if start_distance is None else round(start_distance, 2)}m，"
                    f"距目标={target_distance:.2f}m",
                    current_position=current_position,
                )

            self.sleep(ZIP_LINE_MOTION_POLL_SECONDS)
        return False

    def _direct_zip_line_go(self, target_bearing, target_position=None):
        """按世界方位直接对准并点击目标，失败时调整俯仰角重试。"""
        aim = getattr(self, "aim_view_to_bearing", None)
        if not callable(aim):
            self.log_warning("当前任务不支持读取小地图朝向，无法直接对准滑索目标")
            return False

        last_start_position = None
        for index, pitch in enumerate(ZIP_LINE_DIRECT_PITCH_STEPS):
            if pitch:
                self.log_info(f"直接对准未触发滑索，调整俯仰角 {pitch:+d}px 后重试")
                self.active_and_send_mouse_delta(dx=0, dy=pitch, steps=1, delay=0)
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
            self.send_key("e")
            if self._wait_zip_line_motion(
                target_position=target_position,
                start_position=last_start_position,
            ):
                return True
            self.log_warning(f"直接对准第 {index + 1} 次点击未触发滑索")
        raise ZipLineReplanRequired(
            "连续 10 次未移动到下一滑索，判定两滑索间存在阻挡，需要重新规划",
            current_position=last_start_position,
            failed_target_position=target_position,
        )

    def _find_zip_line_board_button(self, direct_wait=5.0, total_time_out=60.0):
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

        def check():
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

        while self.active_time() < min(start + direct_wait, deadline):
            if found := check():
                return found
            self.sleep(0.1)

        remaining = deadline - self.active_time()
        if remaining <= 0:
            self.log_info("总等待时间已耗尽，仍未找到登上滑索架")
            return None

        self.press_key("ctrl")
        try:
            self.log_info("短时间内未找到登上滑索架，可能被其他设备遮挡，切换步行开始踱步寻找（最长 10 秒）")
            found = self.strafe_search(
                check,
                passes=None,
                duration=0.2,
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
                duration=0.2,
                keys=("s", "w"),
                time_out=remaining,
            )
            if found:
                self.log_info("前后移动寻找过程中找到登上滑索架")
                return found
            self.log_info("前后移动超时，仍未找到登上滑索架")
            return None
        finally:
            self.press_key("ctrl", after_sleep=0.01)
            self.log_info("恢复奔跑模式")

    def board_zip_line(self, direct_wait=5.0, total_time_out=60.0):
        """点击当前滑索架的「登上滑索架」按钮。"""
        result = self._find_zip_line_board_button(
            direct_wait=direct_wait,
            total_time_out=total_time_out,
        )
        if not result:
            return False
        self.click_with_alt(result, after_sleep=2)
        self.log_info("已点击登上滑索架")
        return True

    def on_zip_line_start(self, delivery_to, need_scroll=None, target=None, need_v=True):
        """进入滑索后，根据配置对齐并滑行至送货点

        Args:
            delivery_to: 送货目标名称（用于获取配置中的滑索距离序列）
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪
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
        self.zip_line_list_go(zip_line_list, need_scroll, target, need_v=need_v)

    @staticmethod
    def _zip_line_distance_matcher(zip_line, distance_tolerance=None):
        """构造距离匹配模式；容差只用于地图坐标与游戏显示距离存在偏差的场景。"""
        if distance_tolerance is None:
            return re.compile(str(zip_line))
        center = round(float(zip_line))
        tolerance = max(0, round(float(distance_tolerance)))
        if tolerance <= 0:
            return re.compile(str(center))
        return [
            re.compile(rf"(?<!\d){number}(?:\s*m)?", re.IGNORECASE)
            for number in range(max(1, center - tolerance), center + tolerance + 1)
        ]

    def _ride_zip_line_by_ocr(self, zip_line, tolerance, *, need_scroll=None):
        """用距离 OCR 对中并点击滑行；保留自动送货的兼容路径。"""
        self.align_ocr_or_find_target_to_center(
            self._zip_line_distance_matcher(zip_line, tolerance),
            is_num=True,
            need_scroll=need_scroll,
            ocr_frame_processor_list=[
                self.make_hsv_isolator(hR.GOLD_TEXT),
                self.make_hsv_isolator(hR.WHITE),
            ],
            max_time=100,
        )
        self.log_info(f"成功将滑索调整到{zip_line}的中心")
        self.ensure_click_on_zip_line()

        start = self.active_time()
        while True:
            self.next_frame()
            self.send_key("e")
            self.sleep(0.1)
            if self.ocr(
                match=[
                    self.lang.zip_line_mixin.k_2f4f4a2f,
                    self.lang.zip_line_mixin.k_0b1e4f35,
                ],
                box=self._zip_line_status_box(),
                log=True,
            ):
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
    ):
        """按顺序对齐滑索并执行滑行

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

        """
        bearings = self._normalize_values(target_bearing, scalar=True)
        positions = self._normalize_values(target_positions, scalar=True)
        tolerances = self._normalize_values(distance_tolerance, scalar=True)
        target_mode = self.zip_line_target_select_mode()
        for index, zip_line in enumerate(zip_line_list):
            tolerance = self._value_for_index(
                tolerances,
                index,
                default=distance_tolerance,
            )
            bearing = self._value_for_index(bearings, index)
            target_position = self._value_for_index(positions, index)
            if target_mode == ZIP_LINE_TARGET_SELECT_DIRECT and bearing is not None:
                if not self._direct_zip_line_go(bearing, target_position):
                    raise RuntimeError(f"直接对准滑索目标失败: {float(bearing):.1f}°")
                continue
            self._ride_zip_line_by_ocr(
                zip_line,
                tolerance,
                need_scroll=need_scroll,
            )
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
                result = self.strafe_search(
                    lambda: self.wait_ocr(
                        match=self.lang.zip_line_mixin.k_b0e3a2da,
                        box=self.box.bottom_right,
                        settle_time=1,
                        time_out=4,
                        log=True,
                    ),
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
                self.align_ocr_or_find_target_to_center(
                    ocr_match_or_feature_name_list=result_name,
                    threshold=ZIP_LINE_TEMPLATE_THRESHOLD,
                    ocr=ocr_bool,
                    use_yolo=yolo_bool,
                    raise_if_fail=False,
                )
                self.click(key="right")
        if self.wait_ocr(
            match=[
                self.lang.zip_line_mixin.k_2f4f4a2f,
                self.lang.zip_line_mixin.k_0b1e4f35,
            ],
            box=self._zip_line_status_box(),
            log=True,
            time_out=2,
        ):
            self.click(key="right", after_sleep=2)
        self.log_info("滑索结束")
        self.ensure_main()

    def ensure_click_on_zip_line(self, max_attempts=5):
        for _ in range(max_attempts):
            self.click(after_sleep=0.1)
            self.send_key("e")  # 确认使用send_key：滑索交互键为游戏固定不可改绑键
            if not self.ocr(
                match=[
                    self.lang.zip_line_mixin.k_2f4f4a2f,
                    self.lang.zip_line_mixin.k_0b1e4f35,
                ],
                frame=self.next_frame(),
                box=self._zip_line_status_box(),
            ):
                return True
