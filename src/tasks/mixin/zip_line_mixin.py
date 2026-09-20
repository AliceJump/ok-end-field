import re

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
                raise Exception("滑索超时，强制退出")
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

    def zip_line_list_go(
        self,
        zip_line_list,
        need_scroll=None,
        target=None,
        need_v=False,
        distance_tolerance=None,
        target_bearing=None,
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

        """
        bearings = (
            list(target_bearing)
            if isinstance(target_bearing, (list, tuple))
            else None
        )
        if bearings:
            aim = getattr(self, "aim_view_to_bearing", None)
            if callable(aim):
                first_bearing = bearings[0]
                result = aim(first_bearing, tolerance=8.0, max_rounds=2)
                if result.get("ok"):
                    self.log_info(
                        f"已按滑索世界方位对准视角：目标={float(first_bearing):.1f}°，"
                        f"实测={result.get('heading')}"
                    )
                else:
                    self.log_warning(
                        f"滑索世界方位粗对准未到位：目标={float(first_bearing):.1f}°，"
                        f"实测={result.get('heading')}，继续使用距离 OCR 对中"
                    )
        elif target_bearing is not None:
            bearings = [float(target_bearing)]

        tolerances = (
            list(distance_tolerance)
            if isinstance(distance_tolerance, (list, tuple))
            else None
        )
        for index, zip_line in enumerate(zip_line_list):
            tolerance = (
                tolerances[min(index, len(tolerances) - 1)]
                if tolerances
                else distance_tolerance
            )
            if bearings and index > 0:
                aim = getattr(self, "aim_view_to_bearing", None)
                if callable(aim):
                    bearing = bearings[min(index, len(bearings) - 1)]
                    aim(bearing, tolerance=8.0, max_rounds=2)
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
                box=self.box_of_screen(0.351, 0.943, 0.657, 0.981),
            ):
                return True
