"""Inn opening and setup adapted from MaaEnd/MaaEnd#6676 (AGPL-3.0)."""

import hashlib
import re

from src.image.restaurant_detector import menu_card_rows, menu_selected, reference_frame


class RestaurantSetupMixin:
    def _restaurant_text(self, key, roi, *, timeout=0, click=False):
        match = getattr(self.lang.RestaurantTask, key)
        kwargs = {"box": self._roi(*roi), "match": match}
        if timeout:
            kwargs.update(time_out=timeout, raise_if_not_found=True)
            return self.wait_click_ocr(**kwargs) if click else self.wait_ocr(**kwargs)
        return self.ocr(**kwargs)

    def _prepare_restaurant(self):
        """Resume a kitchen, or open the nearby counter through project inputs."""
        if not self.get_game_hwnd():
            self.ensure_main(time_out=400)
        reference_frame(self.frame)
        if self._panel_ready():
            self._restaurant_icons = {}
            self._restaurant_dishes = []
            return "resume"
        theme = self._restaurant_text("daily_theme", (525, 95, 180, 35))
        setup = self._restaurant_text("setup", (24, 10, 170, 32))
        if not theme and not setup:
            # ensure_main owns game launch/login/window capture; no separate launcher.
            self.ensure_main(time_out=400)
            if not self._restaurant_text("counter", (800, 385, 285, 110)):
                raise RuntimeError("请在缘缘庄柜台的开始经营交互点运行任务")
            self.press_key("f")
            self._restaurant_text("daily_theme", (525, 95, 180, 35), timeout=15)
            theme = True
        if theme:
            boxes = self.wait_ocr(
                box=self._roi(1083, 15, 111, 29),
                match=re.compile(r"(\d+)\s*/\s*\d+"),
                time_out=5,
                raise_if_not_found=True,
            )
            quantity = re.search(r"(\d+)\s*/", boxes[0].name)
            if quantity is None:
                raise RuntimeError("未能确认客源数量")
            if int(quantity[1]) < 60:
                self.log_info("缘缘庄客源不足60，停止经营", notify=True)
                return "insufficient"
            self._restaurant_text("formal_start", (1005, 640, 205, 45), timeout=5, click=True)
        self._restaurant_text("setup", (24, 10, 170, 32), timeout=10)
        # Resetting fills all unlocked Si duties with true in the game client.
        self._restaurant_text("reset_setup", (690, 630, 325, 65), timeout=5)
        self.click_relative(975 / 1280, 660 / 720, after_sleep=0.3)
        for index, left in enumerate((552, 775, 1001)):
            self._restaurant_text("assign", (left, 362, 200, 50), timeout=5, click=True)
            self._restaurant_text("picker", (20, 630, 180, 55), timeout=5)
            for count, x in ((0, 75), (1, 165)):
                self.wait_ocr(
                    box=self._roi(205, 630, 75, 55),
                    match=re.compile(rf"{count}\s*/\s*2"),
                    time_out=5,
                    raise_if_not_found=True,
                )
                self.click_relative(x / 1280, 182 / 720, after_sleep=0.2)
            self.wait_ocr(
                box=self._roi(205, 630, 75, 55), match=re.compile(r"2\s*/\s*2"), time_out=5, raise_if_not_found=True
            )
            self._restaurant_text("confirm", (1125, 630, 145, 60), timeout=5, click=True)
            self._restaurant_text("setup", (24, 10, 170, 32), timeout=5)
            self.info_set("restaurant_kitchens", index + 1)
        self._restaurant_text("set_menu", (1010, 635, 215, 55), timeout=5, click=True)
        self._restaurant_text("menu_requirements", (48, 105, 470, 32), timeout=5)
        requirements = self.ocr(box=self._roi(121, 171, 367, 58))
        text = "".join(box.name for box in requirements or []).replace(" ", "")
        match = self.lang.RestaurantTask.requirement_counts.search(text)
        if not match or not (1 <= int(match[1]) <= 6 and 1 <= int(match[2]) <= 8):
            raise RuntimeError("未能识别日常经营的菜品与饮品数量要求")
        self._restaurant_icons = {}
        self._restaurant_dishes = []
        self._select_restaurant_menu("dish", int(match[1]))
        self._select_restaurant_menu("drink", int(match[2]))
        self._restaurant_text("start", (1005, 635, 210, 55), timeout=5, click=True)
        self.wait_until(self._open_restaurant_kitchen, time_out=20, raise_if_not_found=True)
        return "fresh"

    def _open_restaurant_kitchen(self):
        if self._panel_ready():
            return True
        if self.ocr(box=self._roi(65, 5, 110, 40), match=self.lang.RestaurantTask.countdown):
            self.click_with_alt(int(self.width * 867 / 1280), int(self.height * 630 / 720), after_sleep=0.2)
        return False

    def _select_restaurant_menu(self, kind, required):
        tab = (528, 84, 113, 34) if kind == "dish" else (1098, 84, 105, 34)
        self._restaurant_text("dish_tab" if kind == "dish" else "drink_tab", tab, timeout=5, click=True)
        self.scroll_relative(0.76, 0.45, 30)
        self.sleep(0.3)
        selected, previous_page = set(), None
        for _ in range(20):
            frame = reference_frame(self.frame)
            page = hashlib.sha256(frame[134:537, 552:1192].tobytes()).digest()
            if page == previous_page:
                break
            previous_page = page
            boxes = self.ocr(box=self._roi(552, 133, 640, 404)) or []
            pattern = self.lang.RestaurantTask.craftable if kind == "dish" else self.lang.RestaurantTask.stock
            cards = menu_card_rows(boxes, pattern, width=self.width, height=self.height)
            for left, top in cards:
                title = self.ocr(box=self._roi(left + 103, top + 5, 178, 35)) or []
                name = "".join(box.name for box in title).replace(" ", "").strip()
                if not name:
                    continue
                item = f"{kind}:{name}"
                if item in selected:
                    continue
                if not menu_selected(reference_frame(self.frame), left, top):
                    self.click_relative((left + 150) / 1280, (top + 45) / 720, after_sleep=0.2)
                    self.wait_until(
                        lambda left=left, top=top: menu_selected(reference_frame(self.frame), left, top),
                        time_out=3,
                        raise_if_not_found=True,
                    )
                selected.add(item)
                current = reference_frame(self.frame)
                self._restaurant_icons[item] = current[top + 14 : top + 78, left + 34 : left + 98].copy()
                if kind == "dish":
                    self._restaurant_dishes.append(item)
                if len(selected) >= required:
                    return
            self.scroll_relative(0.76, 0.45, -3)
            self.sleep(0.3)
        raise RuntimeError("可用菜品或饮品不足，停止开局")
