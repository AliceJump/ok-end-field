import math
import re
from copy import copy

import cv2
import numpy as np

from src.core.global_config_store import (
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_DELIVERY_KEYS,
    ZIP_LINE_GATHER_KEYS,
    ZIP_LINE_SCROLL_KEY,
    get_global_config,
)
from src.core.sequence_parser import parse_int_sequence
from src.image.hsv_config import HSVRange as hR
from src.interaction.Mouse import calc_direction_step
from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.mixin.zip_line_motion_cache import (
    build_zip_line_route_fingerprint,
    load_nearest_zip_line_segment_motion,
    load_zip_line_segment_motion,
    record_zip_line_segment_motion,
)
from src.tasks.mixin.zip_line_motion_tracking import track_zip_line_mouse_motion
from src.tasks.mixin.zip_line_yaw_cache import (
    build_zip_line_motion_route_fingerprint,
    load_arrow_turn_motion,
    record_arrow_turn_motion,
)

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

    def on_zip_line_start(self, delivery_to, need_scroll=None, target=None, need_v=True, stay_on_zip_line=False):
        """进入滑索后，根据配置对齐并滑行至送货点

        Args:
            delivery_to: 送货目标名称（用于获取配置中的滑索距离序列）
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪
            stay_on_zip_line: 路线结束后留在滑索上，供下一段路线继续测试
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
                raise Exception("滑索超时，强制退出")
        zip_line_list_str = self.get_zip_line_config_value(delivery_to)
        zip_line_list = parse_int_sequence(zip_line_list_str)
        self.zip_line_list_go(
            zip_line_list,
            need_scroll,
            target,
            need_v=need_v,
            stay_on_zip_line=stay_on_zip_line,
            config_key=delivery_to,
        )

    @staticmethod
    def _zip_line_distance_pattern(zip_line):
        """匹配完整距离数字，避免 108 错配到 1080m 等更长距离。"""
        return re.compile(rf"(?<!\d){re.escape(str(zip_line))}(?!\d)")

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
        width = max(1, int(round(template.shape[1] * scale)))
        height = max(1, int(round(template.shape[0] * scale)))
        return cv2.resize(template, (width, height), interpolation=cv2.INTER_NEAREST)

    def _zip_line_ellipse_geometry(self):
        """返回屏幕中心与滑索边缘提示所在椭圆的半径。"""
        center_x, center_y = self.screen_center()
        scale = self.resolution_scale()
        radius_x = max(1, int(round(self._ZIP_LINE_ELLIPSE_RX * scale)))
        radius_y = max(1, int(round(self._ZIP_LINE_ELLIPSE_RY * scale)))
        return center_x, center_y, radius_x, radius_y

    def _zip_line_ellipse_radius(self, x, y):
        """返回点在滑索椭圆中的归一化半径，1.0 为椭圆边缘。"""
        center_x, center_y, radius_x, radius_y = self._zip_line_ellipse_geometry()
        return math.hypot((x - center_x) / radius_x, (y - center_y) / radius_y)

    def _zip_line_has_gold_ring(self, gold_mask, center_x, center_y):
        """用图标四周均有金黄色像素来识别当前锁定态，避免环境黄色误判。"""
        scale = self.resolution_scale()
        radius = max(1, int(round(22 * scale)))
        x = int(round(center_x))
        y = int(round(center_y))
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
        minimum = max(4, int(round(8 * scale * scale)))
        return all(count >= minimum for count in counts)

    def _detect_zip_line_icon_candidates(self, frame):
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
        merge_distance = max(1, int(round(12 * scale)))
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
        left = max(0, int(round(center_x - half_width)))
        right = min(self.width, int(round(center_x + half_width)))
        top = max(0, int(round(center_y + top_offset)))
        bottom = min(self.height, int(round(center_y + bottom_offset)))
        return self.box_of_screen(left / self.width, top / self.height, right / self.width, bottom / self.height)

    def _find_zip_line_template_target(self, zip_line, frame):
        """保持白色候选优先，并在局部分色 OCR 漏检时逐级放宽。"""
        pattern = self._zip_line_distance_pattern(zip_line)
        candidates = self._detect_zip_line_icon_candidates(frame)
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
        profile = {
            "max_step": 120,
            "min_step": 20,
            "slow_radius": 350,
            "deadzone": 8,
        }
        if ellipse_radius <= self._ZIP_LINE_INNER_RADIUS:
            return profile
        progress = min(1.0, (ellipse_radius - self._ZIP_LINE_INNER_RADIUS) / (1.0 - self._ZIP_LINE_INNER_RADIUS))
        boost = 1.0 + (self._ZIP_LINE_OUTER_MAX_BOOST - 1.0) * progress
        profile["max_step"] = int(round(profile["max_step"] * boost))
        profile["min_step"] = int(round(profile["min_step"] * boost))
        return profile

    @staticmethod
    def _zip_line_alignment_failed(raise_if_fail):
        """保持通用对中 helper 的失败语义。"""
        if raise_if_fail:
            raise Exception("对中失败")
        return False

    def _move_to_zip_line_target(self, target):
        """在椭圆坐标中计算方向与距离，以屏幕宽度统一缩放横纵步长。"""
        cx, cy, rx, ry = self._zip_line_ellipse_geometry()
        nx = (target.x + target.width // 2 - cx) / rx
        ny = (target.y + target.height // 2 - cy) / ry
        profile = self._zip_line_move_profile(math.hypot(nx, ny))
        width_scale = self.width / 1920
        width_radius = self._ZIP_LINE_ELLIPSE_RX * width_scale
        dx, dy = calc_direction_step(
            (0, 0),
            (nx * width_radius, ny * width_radius),
            **{key: max(1, round(value * width_scale)) for key, value in profile.items()},
        )
        if (dx or dy) and not self.active_and_send_mouse_delta(dx, dy, steps=5, delay=0.005):
            return 0, 0
        return dx, dy

    def _zip_line_target_is_gold_and_centered(self, zip_line, frame=None, tolerance=50):
        """判断目标距离是否处于黄色锁定态且位于屏幕中心附近。"""
        result = self.ocr(
            match=self._zip_line_distance_pattern(zip_line),
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
        initial_move=None,
    ):
        """先应用历史位移，再让模板与 OCR fallback 共用同一个可观测移动闭环。"""
        if max_time <= 0 or (deadline is not None and self.active_time() >= deadline):
            return self._zip_line_alignment_failed(raise_if_fail)

        prefeed_move = initial_move

        misses = 0
        pattern = self._zip_line_distance_pattern(zip_line)
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

            matched = self._find_zip_line_template_target(zip_line, frame)
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

        result = self._align_zip_line_distance_ocr(
            zip_line,
            need_scroll=need_scroll,
            tolerance=tolerance,
            max_time=max_time,
            raise_if_fail=raise_if_fail,
            deadline=deadline,
            max_iterations=max_time * 2,
            ocr_target_detector=detect_target,
        )
        return result

    def _zip_line_stop_state(self):
        """Return the OCR patterns and screen box used to detect the riding state."""
        return (
            [
                self.lang.zip_line_mixin.k_2f4f4a2f,
                self.lang.zip_line_mixin.k_0b1e4f35,
            ],
            self.box_of_screen(0.351, 0.943, 0.657, 0.981),
        )

    def _try_click_on_zip_line(self, zip_line, max_attempts=5, lock_timeout=2):
        """执行一次黄色门控 + 点击 + E，返回 ``(成功, 失败原因)``。"""
        stop_match, stop_box = self._zip_line_stop_state()
        lock_start = self.active_time()
        while not self._zip_line_target_is_gold_and_centered(zip_line, frame=self.next_frame(), tolerance=50):
            if self.active_time() - lock_start >= lock_timeout:
                return False, "gate"
            self.sleep(0.05)

        self.click(after_sleep=0.1)
        for _ in range(max_attempts):
            self.send_key("e")  # 确认使用send_key：滑索交互键为游戏固定不可改绑键
            if not self.ocr(match=stop_match, frame=self.next_frame(), box=stop_box):
                return True, None
        return False, "interaction"

    def _legacy_click_on_zip_line(self, max_attempts=5):
        """黄色门控耗尽后的 v1.1.11 式直接点击 + E 回退。"""
        stop_match, stop_box = self._zip_line_stop_state()
        for _ in range(max_attempts):
            self.click(after_sleep=0.1)
            self.send_key("e")  # 确认使用send_key：滑索交互键为游戏固定不可改绑键
            if not self.ocr(match=stop_match, frame=self.next_frame(), box=stop_box):
                return True
        return False

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
        expected_yaw_delta = abs(float(prefeed_dx) * self._ZIP_LINE_ARROW_YAW_PER_MOUSE)
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
        hint = load_arrow_turn_motion(width, height, current_yaw, target_yaw)
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
            move_dx = round(step_degrees / self._ZIP_LINE_ARROW_YAW_PER_MOUSE)
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
            record_arrow_turn_motion(width, height, initial_yaw, target_yaw, total_dx)
        self.log_info(
            f"[zipline-yaw] normalized {initial_yaw:.1f}->{current_yaw:.1f} target={target_yaw:.1f} dx={total_dx}"
        )
        return current_yaw, True

    def _align_and_activate_zip_line(self, zip_line, need_scroll=None, initial_move=None):
        """Align and activate one segment, replaying history only on the first attempt."""
        self._align_zip_line_distance(
            zip_line,
            need_scroll=need_scroll,
            tolerance=50,
            initial_move=initial_move,
        )
        self.log_info(f"成功将滑索调整到{zip_line}的中心")

        gate_retry_start = self.active_time()
        gate_retry_deadline = gate_retry_start + 20
        activated = False
        last_reason = None
        interaction_failed = False
        for attempt in range(3):
            activated, last_reason = self._try_click_on_zip_line(zip_line)
            if activated:
                break
            if last_reason == "gate":
                self.log_info(f"滑索{zip_line}金色门未通过，重新对中后重试（{attempt + 1}/3）")
            else:
                interaction_failed = True
                self.log_info(f"滑索{zip_line}已点击但 E 未生效，重新对中后重试（{attempt + 1}/3）")
            if attempt >= 2 or self.active_time() >= gate_retry_deadline:
                break
            realigned = self._align_zip_line_distance(
                zip_line,
                need_scroll=need_scroll,
                tolerance=50,
                max_time=10,
                raise_if_fail=False,
                deadline=gate_retry_deadline,
            )
            if not realigned:
                last_reason = "gate"
                self.log_info(f"滑索{zip_line}重新对中失败，进入失败处理")
                break

        if not activated:
            if interaction_failed:
                self.log_info(f"滑索{zip_line}已点击但 E 未生效，不执行无门控回退", notify=True)
                raise RuntimeError(f"滑索{zip_line}点击后 E 连续未生效")
            if last_reason == "gate":
                self.log_info(f"滑索{zip_line}金色门重试耗尽，回退直接点击 + E")
                if not self._legacy_click_on_zip_line():
                    self.log_info(f"滑索{zip_line}直接点击后 E 未生效", notify=True)
                    raise RuntimeError(f"滑索{zip_line}直接点击并按 E 后仍未生效")
            else:
                self.log_info(f"滑索{zip_line}交互失败", notify=True)
                raise RuntimeError(f"滑索{zip_line}交互失败")

    def zip_line_list_go(
        self,
        zip_line_list,
        need_scroll=None,
        target=None,
        need_v=False,
        stay_on_zip_line=False,
        config_key=None,
    ):
        """Reuse a matching/nearest yaw history and learn the full action under its original yaw."""
        route_fingerprint = self._zip_line_motion_route_fingerprint(zip_line_list, config_key=config_key)
        for segment_index, zip_line in enumerate(zip_line_list):
            reading = self._read_zip_line_settled_arrow_angle() if route_fingerprint else None
            start_yaw = reading[0] if reading is not None and reading[1] >= self._ZIP_LINE_ARROW_MIN_SCORE else None
            motion_key = (
                build_zip_line_motion_route_fingerprint(route_fingerprint, self.width, self.height, start_yaw)
                if start_yaw is not None
                else None
            )
            hint = load_zip_line_segment_motion(motion_key, segment_index) if motion_key else None
            nearest = (
                load_nearest_zip_line_segment_motion(
                    route_fingerprint, segment_index, self.width, self.height, start_yaw
                )
                if motion_key and hint is None
                else None
            )

            with track_zip_line_mouse_motion(self) as motion:
                if nearest is not None:
                    matched_yaw, _dx, _dy, _samples = nearest
                    actual_yaw, ready = self._normalize_zip_line_segment_yaw(matched_yaw, initial_reading=reading)
                    if ready:
                        # Re-read the cache using the confirmed yaw, never the requested yaw alone.
                        matched_key = build_zip_line_motion_route_fingerprint(
                            route_fingerprint, self.width, self.height, actual_yaw
                        )
                        hint = load_zip_line_segment_motion(matched_key, segment_index)
                initial_move = hint[:2] if hint is not None else None
                if hint is not None:
                    self.log_info(
                        f"[zipline-motion-cache] segment={segment_index + 1}/{len(zip_line_list)} "
                        f"start_yaw={start_yaw:.1f} samples={hint[2]} prefeed={initial_move}"
                    )
                self._align_and_activate_zip_line(zip_line, need_scroll=need_scroll, initial_move=initial_move)

            start = self.active_time()
            while True:
                self.next_frame()
                self.send_key("e")  # 游戏内无法修改此按键，故使用底层按键函数
                self.sleep(0.1)
                result = self.ocr(
                    match=[
                        self.lang.zip_line_mixin.k_2f4f4a2f,
                        self.lang.zip_line_mixin.k_0b1e4f35,
                    ],
                    box=self.box_of_screen(0.351, 0.943, 0.657, 0.981),
                    log=True,
                )
                if result:
                    break
                if self.active_time() - start > 240:
                    raise Exception("滑索超时，强制退出")
            if (
                motion_key
                and motion.complete
                and record_zip_line_segment_motion(motion_key, segment_index, motion.dx, motion.dy)
            ):
                self.log_info(
                    f"[zipline-motion-cache] segment={segment_index + 1}/{len(zip_line_list)} "
                    f"start_yaw={start_yaw:.1f} learned=({motion.dx},{motion.dy})"
                )
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
                    threshold=0.8,
                    ocr=ocr_bool,
                    use_yolo=yolo_bool,
                    raise_if_fail=False,
                    ocr_target_mover=self._move_to_zip_line_target if ocr_bool else None,
                )
                self.click(key="right")
        if self.wait_ocr(
            match=[
                self.lang.zip_line_mixin.k_2f4f4a2f,
                self.lang.zip_line_mixin.k_0b1e4f35,
            ],
            box=self.box_of_screen(0.351, 0.943, 0.657, 0.981),
            log=True,
            time_out=2,
        ):
            self.click(key="right", after_sleep=2)
        self.log_info("滑索结束")
        self.ensure_main()

    def ensure_click_on_zip_line(self, zip_line, max_attempts=5, lock_timeout=2):
        """兼容入口：一次黄色门控 + 点击 + E，失败由调用方决定是否重新对中。"""
        success, _ = self._try_click_on_zip_line(
            zip_line,
            max_attempts=max_attempts,
            lock_timeout=lock_timeout,
        )
        return success
