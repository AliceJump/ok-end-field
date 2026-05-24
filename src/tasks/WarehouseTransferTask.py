import re
import win32api
import win32con
from qfluentwidgets import FluentIcon

from src.data.lang import (
    compile_any_pattern,
    get_warehouse_current_location_rules,
    get_warehouse_location_labels,
    get_warehouse_ocr_pattern_tokens,
)
from src.data.world_map import item_to_warehouse_dict
from src.data.zh_en import get_item_translation_dict, get_item_warehouse_category_map
from src.tasks.BaseEfTask import BaseEfTask


class WarehouseTransferTask(BaseEfTask):
    """
    背包物品跨仓库转移（发货仓库 -> 收货仓库 -> 一键存放 -> 切回发货仓库）。

    依赖：
    - OCR 用于识别：仓库标题/仓库切换按钮/确认/已连接/一键存放
    - template 用于识别：物品图标（来自 assets/items/images）
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "仓库物品转移"
        self.description = "从发货仓库取出指定物品，切到收货仓库后一键存放 （目前只支持中文版）"
        self.icon = FluentIcon.SYNC
        self.default_config.update(
            {
                "发货仓库": "valley4",
                "收货仓库": "wuling",
                "物品": "蓝铁矿",
                "转移轮次": 10,
                # "最小保留数量": 1000,
            }
        )
        self.config_description.update(
            {
                "发货仓库": "从这个仓库拿货",
                "收货仓库": "转运到这个仓库",
                "物品": "选择要转移的物品",
                "转移轮次": "倒货的轮次",
                # "最小保留数量": "当识别到当前数量小于该值时停止任务并通知",
            }
        )
        warehouse_keys = list(self._warehouse_locations.keys())
        self.config_type["发货仓库"] = {"type": "drop_down", "options": warehouse_keys}
        self.config_type["收货仓库"] = {"type": "drop_down", "options": warehouse_keys}
        # self.config_type["物品"] = {"type": "drop_down", "options": self._load_item_keys_for_dropdown()}
        self.config_type["物品"] = {
            "type": "drop_down",
            "options": list(item_to_warehouse_dict.keys()),
        }
        self._template_cache: dict[str, object] = {}
        self._item_name_cache: dict[str, str] | None = None

    @property
    def _warehouse_locations(self) -> dict[str, str]:
        return get_warehouse_location_labels(context=self)

    @property
    def _warehouse_location_rules(self) -> dict[str, list[list[str]]]:
        return get_warehouse_current_location_rules(context=self)

    @property
    def _warehouse_ocr_patterns(self) -> dict[str, re.Pattern]:
        return {
            key: compile_any_pattern(value)
            for key, value in get_warehouse_ocr_pattern_tokens(context=self).items()
        }

    def _to_one_type_page(self, item_name: str):
        category_map = get_item_warehouse_category_map(context=self)
        category_en_name = category_map.get(item_to_warehouse_dict.get(item_name, ""), "")
        if not category_en_name:
            raise ValueError(f"物品 {item_name} 无法找到分类，无法定位图标")
        result = self.find_feature(feature_name=f"{category_en_name}_icon")
        if not result:
            self.log_info(f"物品 {item_name} 无法找到分类图标,可能已经进入该分类页")
        if result:
            self.click(result[0], move_back=True, after_sleep=2)

    def _detect_current_location(self) -> str | None:
        boxes = self.ocr(box=self.box_of_screen(0.15, 0.18, 0.26, 0.22, name="current_location_area"))
        rules = self._warehouse_location_rules
        for box in boxes or []:
            name = str(getattr(box, "name", "")).strip()
            for location_key, groups in rules.items():
                if any(all(token in name for token in token_group) for token_group in groups):
                    return location_key
        return None

    def _maybe_click_confirm(self) -> bool:
        confirm_pattern = self._warehouse_ocr_patterns.get("confirm")
        if confirm_pattern is None:
            raise RuntimeError("缺少仓库确认按钮 OCR 配置")
        hits = self.ocr(
            box=self.box_of_screen(0.79, 0.79, 0.84, 0.82, name="bottom_right"),
            match=confirm_pattern,
        )
        if hits:
            self.click(hits[0], move_back=True, after_sleep=0.3)
            return True
        return False

    def _switch_location(self, target_key: str):
        locations = self._warehouse_locations
        if target_key not in locations:
            raise ValueError(f"未知 location key: {target_key}")

        switch_pattern = self._warehouse_ocr_patterns.get("switch_button")
        if switch_pattern is None:
            raise RuntimeError("缺少仓库切换按钮 OCR 配置")
        btn = self.wait_ocr(
            box=self.box_of_screen(0.48, 0.18, 0.52, 0.215, name="switch_btn_area"),
            match=switch_pattern,
            time_out=5,
        )
        if not btn:
            raise RuntimeError("未找到“仓库切换”按钮")
        self.click(btn[0], move_back=True, after_sleep=0.5)

        target_text = locations[target_key]
        option = self.wait_ocr(
            box=self.box_of_screen(0.4, 0.35, 0.75, 0.65, name="switch_menu"),
            match=target_text,
            time_out=5,
        )
        if not option:
            raise RuntimeError(f"未找到仓库选项：{target_text}")
        self.click(option[0], move_back=True, after_sleep=0.2)

        self._maybe_click_confirm()
        connected_pattern = self._warehouse_ocr_patterns.get("connected")
        if connected_pattern is None:
            raise RuntimeError("缺少仓库连接状态 OCR 配置")
        for _ in range(50):
            self.next_frame()
            hits = self.ocr(
                box=self.box.bottom_right,
                match=connected_pattern,
            )
            if hits:
                self.sleep(0.3)
                self.send_key("esc", after_sleep=0.2)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
                self.log_info(f"仓库切换成功")
                return
            self.sleep(0.5)
        raise RuntimeError("切换仓库失败：5秒内未检测到“已连接”")

    def _ctrl_click(self, box):
        win32api.keybd_event(
            win32con.VK_CONTROL, 0, 0, 0
        )  # 确认使用send_key：ctrl为系统修饰键，用于ctrl+点击多选，非游戏可配置热键
        try:
            self.sleep(0.03)
            self.click(box, move_back=True, down_time=0.03, after_sleep=0, key="left")
            self.sleep(0.03)
        finally:
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        self.sleep(0.15)

    def run(self):
        self.ensure_main()

        from_key = str(self.config.get("发货仓库", "wuling")).strip()
        to_key = str(self.config.get("收货仓库", "valley4")).strip()
        if from_key == to_key:
            raise RuntimeError("发货仓库与收货仓库不能相同")

        item_key = str(self.config.get("物品", "")).strip()
        if not item_key:
            raise RuntimeError("未选择物品")
        max_times = int(self.config.get("转移轮次", 10))
        self.press_key("b", after_sleep=1)
        search_box = self.box_of_screen(0.12, 0.30, 0.55, 0.68)
        while True:
            current = self._detect_current_location()
            if current != from_key:
                self.log_info(f"当前仓库={current}，切换到发货仓库={from_key}")
                self._switch_location(from_key)
                current = self._detect_current_location()
                if current != from_key:
                    raise RuntimeError(f"切换到发货仓库失败，当前={current} 期望={from_key}")
            self._to_one_type_page(item_key)
            cx = int(self.width / 3)
            cy = int(self.height * 0.5)
            self.log_info(f"处理物品: {item_key}")

            ROUND = 5
            icon = None
            item_translation_map = get_item_translation_dict(context=self)
            item_key_en = item_translation_map.get(item_key, "")
            if not item_key_en:
                self.log_info(f"找不到的图标名 {item_key}")
            for round_idx in range(ROUND + 1):
                icon = self.find_one(feature_name=item_key_en, box=search_box, threshold=0.8)
                if icon:
                    break
                if round_idx == ROUND:
                    break
                self.move(cx, cy)
                self.scroll(cx, cy, -2)
                self.sleep(0.5)

            if not icon:
                raise RuntimeError(f"未找到物品图标（滚动{ROUND}轮后仍失败）：{item_key}")
            self._ctrl_click(icon)
            self.sleep(0.35)
            icon_after = self.find_feature(feature_name=item_key_en, box=search_box, threshold=0.8)
            if not icon_after:
                self.log_info(f"物品图标已消失（可能已倒完）：{item_key}")
                # count_after = self._read_count_near_icon(icon_after)
                # if count_before is not None and count_after is not None:
                #     self.log_debug(f"物品数量(后): {count_after}")
                #     if count_after >= count_before:
                #         raise RuntimeError(f"点击后数量未减少：{item_key} 前={count_before} 后={count_after}")

            self.log_info(f"切换到收货仓库={to_key}")
            self._switch_location(to_key)

            store_pattern = self._warehouse_ocr_patterns.get("store")
            if store_pattern is None:
                raise RuntimeError("缺少仓库存放按钮 OCR 配置")
            store_btn = self.wait_ocr(
                box=self.box_of_screen(0.64, 0.705, 0.69, 0.735, name="onekey_store_area"),
                match=store_pattern,
                time_out=5,
            )
            if not store_btn:
                raise RuntimeError("未找到“一键存放”按钮")
            self.click(store_btn[0], move_back=True, after_sleep=0.5)
            self._maybe_click_confirm()
            max_times -= 1
            if max_times <= 0:
                break
            self.log_info(f"切回发货仓库={from_key}")
            self._switch_location(from_key)
        self.log_info("仓库转移任务完成")
