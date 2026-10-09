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
from src.tasks.mixin.instructions_mixin import InstructionsMixin, inst_gap, inst_line
from src.tasks.mixin.navigation_mixin import NavigationMixin


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
                raise Exception("滑索超时，强制退出")
        zip_line_list_str = self.get_zip_line_config_value(delivery_to)
        zip_line_list = parse_int_sequence(zip_line_list_str)
        self.zip_line_list_go(zip_line_list, need_scroll, target, need_v=need_v)

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

        left = max(0, center_x - radius_x - template_width)
        right = min(width, center_x + radius_x + template_width)
        top = max(0, center_y - radius_y - template_height)
        bottom = min(height, center_y + radius_y + template_height)
        if right - left < template_width or bottom - top < template_height:
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
            locations = np.argwhere(scores >= self._ZIP_LINE_TEMPLATE_THRESHOLD)
            locations = sorted(locations, key=lambda pos: float(scores[pos[0], pos[1]]), reverse=True)
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

        # 相同距离同时存在时先检查白色，再检查黄色/金黄色；同色优先转动更少的目标。
        candidates.sort(key=lambda item: (0 if item["state"] == "white" else 1, item["radius"], -item["score"]))
        return candidates

    def _zip_line_distance_box(self, candidate):
        """按图标下方约 39px 的固定 HUD 关系构造距离数字 OCR 小区域。"""
        center_x = candidate["center_x"]
        center_y = candidate["center_y"]
        half_width = self.scale_distance(55)
        top_offset = self.scale_distance(28)
        bottom_offset = self.scale_distance(58)
        left = max(0, int(round(center_x - half_width)))
        right = min(self.width, int(round(center_x + half_width)))
        top = max(0, int(round(center_y + top_offset)))
        bottom = min(self.height, int(round(center_y + bottom_offset)))
        return self.box_of_screen(left / self.width, top / self.height, right / self.width, bottom / self.height)

    def _find_zip_line_template_target(self, zip_line, frame):
        """先定位合格图标，再只 OCR 对应图标正下方的目标距离。"""
        pattern = self._zip_line_distance_pattern(zip_line)
        candidates = self._detect_zip_line_icon_candidates(frame)
        for candidate in candidates:
            hsv_range = hR.WHITE if candidate["state"] == "white" else hR.GOLD_TEXT
            result = self.ocr(
                match=pattern,
                box=self._zip_line_distance_box(candidate),
                frame=frame,
                frame_processor=self.make_hsv_isolator(hsv_range),
                log=False,
            )
            if result:
                target = result[0] if isinstance(result, list) else result
                return candidate, target
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
    ):
        """保留原始全局 OCR 对中作为模板连续失败后的兜底。"""
        return self.align_ocr_or_find_target_to_center(
            self._zip_line_distance_pattern(zip_line),
            is_num=True,
            need_scroll=need_scroll,
            ocr_frame_processor_list=[
                self.make_hsv_isolator(hR.GOLD_TEXT),
                self.make_hsv_isolator(hR.WHITE),
            ],
            tolerance=tolerance,
            max_time=max_time,
            raise_if_fail=raise_if_fail,
        )

    def _align_zip_line_distance(
        self,
        zip_line,
        need_scroll=None,
        tolerance=50,
        max_time=100,
        raise_if_fail=True,
    ):
        """优先用滑索图标限定 OCR 候选；连续模板 miss 后退回原始 OCR。"""
        scaled_tolerance = self.scale_distance(tolerance)
        misses = 0
        for _ in range(max(1, max_time * 2)):
            if need_scroll:
                self.do_scroll(1, 400)
            frame = self.next_frame()
            matched = self._find_zip_line_template_target(zip_line, frame)
            if matched is None:
                misses += 1
                if misses >= self._ZIP_LINE_TEMPLATE_MISS_LIMIT:
                    self.log_info(f"滑索{zip_line}连续{misses}次未找到白色/黄色模板目标，回退原始OCR对中")
                    return self._align_zip_line_distance_ocr(
                        zip_line,
                        need_scroll=need_scroll,
                        tolerance=tolerance,
                        max_time=max_time,
                        raise_if_fail=raise_if_fail,
                    )
                self.sleep(0.03)
                continue

            misses = 0
            candidate, ocr_target = matched
            target_center = (int(round(candidate["center_x"])), int(round(candidate["center_y"])))
            screen_center_x, screen_center_y = self.screen_center()
            dx = target_center[0] - screen_center_x
            dy = target_center[1] - screen_center_y
            if abs(dx) <= scaled_tolerance and abs(dy) <= scaled_tolerance:
                return target_center

            move_target = copy(ocr_target)
            move_target.x = candidate["x"]
            move_target.y = candidate["y"]
            move_target.width = candidate["width"]
            move_target.height = candidate["height"]
            self.move_to_target_once(move_target, **self._zip_line_move_profile(candidate["radius"]))
            self.sleep(0.01)

        self.log_info(f"滑索{zip_line}模板对中未在限制内收敛，回退原始OCR对中")
        return self._align_zip_line_distance_ocr(
            zip_line,
            need_scroll=need_scroll,
            tolerance=tolerance,
            max_time=max_time,
            raise_if_fail=raise_if_fail,
        )

    def _zip_line_stop_state(self):
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

    def zip_line_list_go(self, zip_line_list, need_scroll=None, target=None, need_v=False):
        """按顺序对齐滑索并执行滑行

        Args:
            zip_line_list: 滑索距离列表
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪

        """
        for zip_line in zip_line_list:
            self._align_zip_line_distance(zip_line, need_scroll=need_scroll, tolerance=50)
            self.log_info(f"成功将滑索调整到{zip_line}的中心")

            gate_retry_start = self.active_time()
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
                if attempt >= 2 or self.active_time() - gate_retry_start >= 20:
                    break
                realigned = self._align_zip_line_distance(
                    zip_line,
                    need_scroll=need_scroll,
                    tolerance=50,
                    max_time=10,
                    raise_if_fail=False,
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
