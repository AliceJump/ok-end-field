import re
from src.image.hsv_config import HSVRange as hR
from src.core.sequence_parser import parse_int_sequence
from src.core.global_config_store import (
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_SCROLL_KEY,
    ZIP_LINE_DELIVERY_KEYS,
    ZIP_LINE_GATHER_KEYS,
    get_global_config,
)
from src.tasks.account.account_scope_store import get_account_task_overrides
from src.tasks.mixin.navigation_mixin import NavigationMixin


def _inst_line(text: str, color: str = "", *, bold: bool = False, indent: int = 0):
    content = f"{'&nbsp;' * (indent * 4)}{text}"
    if bold:
        content = f"<strong>{content}</strong>"
    return f'<span style="color:{color};">{content}</span>'


def _inst_gap():
    return '<span style="font-size:4px;">&nbsp;</span>'


class ZipLineMixin(NavigationMixin):
    # 延迟合并标记与基础说明缓存（类级默认，避免 __init__ 赋值顺序问题）
    _zip_line_inst_dirty = True
    _zip_line_inst_base = None

    @property
    def zip_line_config(self):
        return get_global_config(ZIP_LINE_CONFIG_NAME)

    @property
    def instructions(self):
        """滑索配置使用说明（延迟触发）。

        首次访问时把滑索说明追加到任务说明（追加而非覆盖）。
        说明文本通过 self.tr() 走 ok 的 gettext i18n（msgid 写入 ok.po，编译成 ok.mo 生效）。
        这样使用滑索的任务无需在 __init__ 里显式调用。
        """
        if self._zip_line_inst_dirty:
            self._zip_line_inst_dirty = False
            base = self._zip_line_inst_base
            zip_inst = self._build_zip_line_instructions()
            self._zip_line_inst_base = f"{base}<br><br>{zip_inst}" if base else zip_inst
        return self._zip_line_inst_base

    @instructions.setter
    def instructions(self, value):
        # ok 库 BaseTask.__init__ 会执行 self.instructions = None，这里仅缓存基础说明
        self._zip_line_inst_base = value
        self._zip_line_inst_dirty = True

    def _build_zip_line_instructions(self):
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
                _inst_line("📍 " + self.tr("滑索配置说明"), "#FF5555", bold=True),
                _inst_line("⚙️ " + self.tr("滑索距离序列在「全局配置 → 滑索配置」中设置"), "#FF5555", bold=True),
                _inst_gap(),
                _inst_line("⚠️ " + self.tr("填写规则"), "#FE821D", bold=True),
                _inst_line(f"└─ {self.tr('每个键对应一条滑索路线，值为距离序列，用英文逗号分隔')}", indent=1),
                _inst_line(f"└─ {self.tr('任务会按顺序依次对齐并滑行每段距离')}", indent=1),
                _inst_line(f"└─ {self.tr('例：「{key}」= {raw} → 依次滑行 {seq}').format(key=example_key, raw=example_raw, seq=example_seq)}", indent=1),
                _inst_line(f"└─ {self.tr('留空表示该路线不乘滑索')}", indent=1),
                _inst_gap(),
                _inst_line("📦 " + self.tr("送货相关键"), "#FE821D", bold=True),
                _inst_line(f"├─ {self.tr('{keys}：出发滑索距离').format(keys=' / '.join(start_keys))}", indent=1),
                _inst_line(f"└─ {self.tr('{keys}：各送货目标滑索序列').format(keys=' / '.join(target_keys))}", indent=1),
                _inst_gap(),
                _inst_line("🪫 " + self.tr("淤积点相关键"), "#FE821D", bold=True),
                _inst_line(f"└─ {self.tr('{keys}：能量淤积点滑索序列').format(keys=' / '.join(gather_keys))}", indent=1),
                _inst_gap(),
                _inst_line("🖱️ " + self.tr("是否启用滚动放大视角"), "#FE821D", bold=True),
                _inst_line(f"└─ {self.tr('对齐滑索时自动滚动放大视角，可能提高成功率，也可能明显降低')}", indent=1),
                _inst_line(f"└─ {self.tr('建议启用时不要使用非白发或有白帽角色')}", indent=1),
            ]
        )

    def get_zip_line_config_value(self, key, default=None):
        base_value = self.zip_line_config.get(key, default)
        if not getattr(self, "running", False):
            return base_value
        if hasattr(self, "_is_account_override_enabled") and not self._is_account_override_enabled():
            return base_value

        account_id = (getattr(self, "current_account_id", "") or "").strip()
        account_name = (getattr(self, "current_user", "") or "").strip()
        if not account_id and not account_name:
            return base_value

        override = get_account_task_overrides(
            account_id or account_name,
            ZIP_LINE_CONFIG_NAME,
            account_name=account_name,
        )
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

    def zip_line_list_go(self, zip_line_list, need_scroll=None, target=None, need_v=False):
        """按顺序对齐滑索并执行滑行

        Args:
            zip_line_list: 滑索距离列表
            need_scroll: 是否需要滚动
            target: 目标信息，包含名称和类型(例如：("登上滑索架", "ocr"))
            need_v: 是否需要按V键追踪

        """
        for zip_line in zip_line_list:
            self.align_ocr_or_find_target_to_center(
                re.compile(str(zip_line)),
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
        if self.wait_ocr(match=[
                self.lang.zip_line_mixin.k_2f4f4a2f,
                self.lang.zip_line_mixin.k_0b1e4f35,
            ], box=self.box_of_screen(0.351, 0.943, 0.657, 0.981), log=True, time_out=2):
            self.click(key="right", after_sleep=2)
        self.log_info("滑索结束")
        self.ensure_main()

    def ensure_click_on_zip_line(self, max_attempts=5):
        for _ in range(max_attempts):
            self.click(after_sleep=0.1)
            self.send_key("e")  # 确认使用send_key：滑索交互键为游戏固定不可改绑键
            if not self.ocr(match=[
                    self.lang.zip_line_mixin.k_2f4f4a2f,
                    self.lang.zip_line_mixin.k_0b1e4f35,
                ], frame=self.next_frame(), box=self.box_of_screen(0.351, 0.943, 0.657, 0.981)):
                return True
