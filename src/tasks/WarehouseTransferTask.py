import json
import re
from pathlib import Path

import cv2
import numpy as np
from ok import Box
from qfluentwidgets import FluentIcon

from src.tasks.BaseEfTask import BaseEfTask


_LOCATIONS = {
    "valley4": "四号谷地",
    "wuling": "武陵",
}

_ASSETS_ITEM_JSON = Path("assets") / "items" / "item.json"
_ASSETS_ITEM_IMAGES_DIR = Path("assets") / "items" / "images"

def _parse_count(text: str) -> int | None:
    if not text:
        return None
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("O", "0")
        .replace("o", "0")
        .replace("０", "0")
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*万|(\d{1,4})", cleaned)
    if not match:
        return None
    if match.group(1):
        try:
            return int(float(match.group(1)) * 10000)
        except Exception:
            return None
    if match.group(2):
        try:
            return int(match.group(2))
        except Exception:
            return None
    return None


def split_template_and_mask(template: np.ndarray):
    if template.ndim == 2:
        return cv2.cvtColor(template, cv2.COLOR_GRAY2BGR), None
    if template.shape[2] == 4:
        bgr = template[:, :, :3]
        alpha = template[:, :, 3]
        mask = (alpha > 0).astype(np.uint8) * 255
        return bgr, mask
    return template[:, :, :3], None


def find_best_template_match(search_bgr: np.ndarray, template: np.ndarray, scales):
    best_score = -1.0
    best_scale = 1.0
    best_loc = None
    best_size = None
    for scale in scales:
        if scale == 1.0:
            scaled_template = template
        else:
            h, w = template.shape[:2]
            nw = int(w * scale)
            nh = int(h * scale)
            if nw < 8 or nh < 8:
                continue
            scaled_template = cv2.resize(template, (nw, nh), interpolation=cv2.INTER_LINEAR)

        template_bgr, template_mask = split_template_and_mask(scaled_template)
        if template_bgr.shape[0] > search_bgr.shape[0] or template_bgr.shape[1] > search_bgr.shape[1]:
            continue

        if template_mask is not None:
            result = cv2.matchTemplate(search_bgr, template_bgr, cv2.TM_CCORR_NORMED, mask=template_mask)
        else:
            result = cv2.matchTemplate(search_bgr, template_bgr, cv2.TM_CCORR_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if float(max_val) > best_score:
            best_score = float(max_val)
            best_scale = scale
            best_loc = max_loc
            best_size = (template_bgr.shape[1], template_bgr.shape[0])

    return best_score, best_scale, best_loc, best_size


def calc_count_ocr_rect(icon_x: int, icon_y: int, icon_w: int, icon_h: int, screen_w: int, screen_h: int):
    box_w = max(2, int(screen_w * 0.045))
    box_h = max(2, int(screen_h * 0.03))
    center_x = int(icon_x + icon_w / 2)
    center_y = int(icon_y + icon_h)
    x = max(0, min(screen_w - box_w, center_x - int(box_w / 2)))
    y = max(0, min(screen_h - box_h, center_y - int(box_h / 2)))
    return x, y, box_w, box_h


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
                "物品": "item_iron_ore",
                "最小保留数量": 1000,
            }
        )
        self.config_description.update(
            {
                "发货仓库": "从这个仓库拿货",
                "收货仓库": "转运到这个仓库",
                "物品": "选择要转移的物品",
                "最小保留数量": "当识别到当前数量小于该值时停止任务并通知",
            }
        )
        self.config_type["发货仓库"] = {"type": "drop_down", "options": list(_LOCATIONS.keys())}
        self.config_type["收货仓库"] = {"type": "drop_down", "options": list(_LOCATIONS.keys())}
        self.config_type["物品"] = {"type": "drop_down", "options": self._load_item_keys_for_dropdown()}

        self._template_cache: dict[str, object] = {}
        self._item_name_cache: dict[str, str] | None = None

    def _load_item_keys_for_dropdown(self) -> list[str]:
        try:
            if _ASSETS_ITEM_JSON.exists():
                data = json.loads(_ASSETS_ITEM_JSON.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return sorted([str(k) for k in data.keys()])
        except Exception:
            pass
        return ["item_bottled_food_1"]

    def _items_search_box(self):
        x1, y1, x2, y2 = (0.12, 0.30, 0.55, 0.68)
        return self.box_of_screen(x1, y1, x2, y2, name="items_search_area")

    def _load_item_names(self) -> dict[str, str]:
        if self._item_name_cache is not None:
            return self._item_name_cache
        if not _ASSETS_ITEM_JSON.exists():
            self._item_name_cache = {}
            return self._item_name_cache
        try:
            self._item_name_cache = json.loads(_ASSETS_ITEM_JSON.read_text(encoding="utf-8"))
        except Exception:
            self._item_name_cache = {}
        return self._item_name_cache

    def _get_item_name(self, key: str) -> str:
        names = self._load_item_names()
        return str(names.get(key, key))

    def _get_template(self, item_key: str):
        if item_key in self._template_cache:
            return self._template_cache[item_key]
        template_path = _ASSETS_ITEM_IMAGES_DIR / f"{item_key}.png"
        if not template_path.exists():
            raise FileNotFoundError(f"物品模板不存在: {template_path}")
        template = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
        if template is None:
            raise FileNotFoundError(f"无法读取物品模板: {template_path}")
        self._template_cache[item_key] = template
        return template

    def _detect_current_location(self) -> str | None:
        boxes = self.ocr(box=self.box_of_screen(0.15, 0.18, 0.26, 0.22, name="current_location_area"))
        for box in boxes or []:
            name = str(getattr(box, "name", "")).strip()
            if "武陵仓库" in name:
                return "wuling"
            if "谷地" in name and "仓库" in name:
                return "valley4"
        return None

    def _maybe_click_confirm(self) -> bool:
        hits = self.ocr(
            box=self.box_of_screen(0.79, 0.79, 0.84, 0.82, name="bottom_right"),
            match=re.compile(r"确认"),
        )
        if hits:
            self.click(hits[0], move_back=True, after_sleep=0.3)
            return True
        return False

    def _switch_location(self, target_key: str):
        if target_key not in _LOCATIONS:
            raise ValueError(f"未知 location key: {target_key}")

        btn = self.wait_ocr(
            box=self.box_of_screen(0.48, 0.18, 0.52, 0.215, name="switch_btn_area"),
            match="仓库切换",
            time_out=5,
        )
        if not btn:
            raise RuntimeError("未找到“仓库切换”按钮")
        self.click(btn[0], move_back=True, after_sleep=0.5)

        target_text = _LOCATIONS[target_key]
        option = self.wait_ocr(
            box=self.box_of_screen(0.4, 0.35, 0.75, 0.65, name="switch_menu"),
            match=target_text,
            time_out=5,
        )
        if not option:
            raise RuntimeError(f"未找到仓库选项：{target_text}")
        self.click(option[0], move_back=True, after_sleep=0.2)

        self._maybe_click_confirm()
        for _ in range(50):
            hits = self.ocr(
                box=self.box_of_screen(0.79, 0.79, 0.84, 0.82, name="bottom_right"),
                match=re.compile(r"已连接"),
            )
            if hits:
                self.sleep(0.3)
                self.send_key("esc", after_sleep=0.2)
                self.log_info(f"仓库切换成功")
                return
            self.sleep(0.1)
        raise RuntimeError("切换仓库失败：5秒内未检测到“已连接”")

    def _find_item_icon(self, item_key: str, search_box):
        template = self._get_template(item_key)
        search_frame = search_box.crop_frame(self.frame)
        if search_frame is None or search_frame.size == 0:
            return None

        raw_best_score, raw_best_scale, max_loc, max_size = find_best_template_match(
            search_frame,
            template,
            (1.10, 1.15, 1.20, 1.25, 1.30),
        )

        if raw_best_score >= 0.82 and max_loc is not None and max_size is not None:
            result = Box(
                int(search_box.x + max_loc[0]),
                int(search_box.y + max_loc[1]),
                int(max_size[0]),
                int(max_size[1]),
                float(raw_best_score),
                item_key,
            )
            self.log_debug(
                f"模板命中 item={item_key}, conf={raw_best_score:.3f}, scale={raw_best_scale:.2f}, box={result}"
            )
            return result

        self.log_debug(
            f"模板未命中 item={item_key}, best_raw_score={raw_best_score:.3f}, "
            f"best_scale={raw_best_scale:.2f}, threshold={0.82:.2f}"
        )
        return None

    def _read_count_near_icon(self, icon_box) -> int | None:
        x, y, w, h = calc_count_ocr_rect(
            int(icon_box.x),
            int(icon_box.y),
            int(icon_box.width),
            int(icon_box.height),
            int(self.width),
            int(self.height),
        )
        roi = Box(x, y, w, h, name="item_count_roi")
        texts = self.ocr(box=roi, match=re.compile(r"(\d+(?:\.\d+)?)\s*万|(\d{1,4})"))

        best = None
        for text_box in texts or []:
            val = _parse_count(str(getattr(text_box, "name", "")))
            if val is None:
                continue
            if best is None or val > best:
                best = val
        return best

    def _ctrl_click(self, box):
        self.send_key_down("LCONTROL")
        try:
            self.sleep(0.03)
            self.click(box, move_back=True, down_time=0.03, after_sleep=0, key="left")
            self.sleep(0.03)
        finally:
            self.send_key_up("LCONTROL")
        self.sleep(0.15)

    def run(self):
        from_key = str(self.config.get("发货仓库", "wuling")).strip()
        to_key = str(self.config.get("收货仓库", "valley4")).strip()
        if from_key == to_key:
            raise RuntimeError("发货仓库与收货仓库不能相同")

        item_key = str(self.config.get("物品", "")).strip()
        if not item_key:
            raise RuntimeError("未选择物品")
        
        self.log_info(f"5 秒后开始自动转移")
        self.sleep(5)

        self.send_key("b", after_sleep=1)

        while True:
            current = self._detect_current_location()
            if current != from_key:
                self.log_info(f"当前仓库={current}，切换到发货仓库={from_key}")
                self._switch_location(from_key)
                current = self._detect_current_location()
                if current != from_key:
                    raise RuntimeError(f"切换到发货仓库失败，当前={current} 期望={from_key}")

            search_box = self._items_search_box()
            cx = int(self.width / 3)
            cy = int(self.height * 0.5)

            self.move(cx, cy)
            self.scroll(cx, cy, 5) #滚回最上面

            self.sleep(0.5)

            item_name = self._get_item_name(item_key)
            self.log_info(f"处理物品: {item_key} ({item_name})")
            min_keep_count = int(self.config.get("最小保留数量", 1000))

            ROUND = 5
            icon = None
            for round_idx in range(ROUND + 1):
                icon = self._find_item_icon(item_key, search_box=search_box)
                if icon:
                    break
                if round_idx == ROUND:
                    break
                self.move(cx, cy)
                self.scroll(cx, cy, -2)
                self.sleep(0.5)

            if not icon:
                raise RuntimeError(f"未找到物品图标（滚动{ROUND}轮后仍失败）：{item_key}")

            count_before = self._read_count_near_icon(icon)
            if count_before is not None:
                self.log_debug(f"物品数量(前): {count_before}")
                if count_before < min_keep_count:
                    self.log_info(
                        f"检测到物品数量低于阈值，停止任务：{item_key} 当前={count_before} 阈值={min_keep_count}",
                        notify=True,
                    )
                    return

            self._ctrl_click(icon)
            self.sleep(0.35)

            icon_after = self._find_item_icon(
                item_key,
                search_box=icon.copy(
                    x_offset=-icon.width,
                    y_offset=-icon.height,
                    width_offset=icon.width * 2,
                    height_offset=icon.height * 2,
                    name="recheck_icon_area",
                ),
            )
            if not icon_after:
                self.log_info(f"物品图标已消失（可能已倒完）：{item_key}")
            else:
                count_after = self._read_count_near_icon(icon_after)
                if count_before is not None and count_after is not None:
                    self.log_debug(f"物品数量(后): {count_after}")
                    if count_after >= count_before:
                        raise RuntimeError(f"点击后数量未减少：{item_key} 前={count_before} 后={count_after}")

            self.log_info(f"切换到收货仓库={to_key}")
            self._switch_location(to_key)

            store_btn = self.wait_ocr(
                box=self.box_of_screen(0.64, 0.705, 0.69, 0.735, name="onekey_store_area"),
                match=re.compile(r"一键存放"),
                time_out=5,
            )
            if not store_btn:
                raise RuntimeError("未找到“一键存放”按钮")
            self.click(store_btn[0], move_back=True, after_sleep=0.5)
            self._maybe_click_confirm()

            self.log_info(f"切回发货仓库={from_key}")
            self._switch_location(from_key)
