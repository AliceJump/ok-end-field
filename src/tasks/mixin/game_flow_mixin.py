import re
import time

import cv2
import numpy as np

from src.data.world_map import areas_list
from src.essence.essence_recognizer import EssenceInfo, read_essence_info
from src.image.login_screenshot import capture_window_by_screen
from src.interaction.Mouse import run_at_window_pos


class GameFlowMixin:
    """登录弹窗、主界面状态与场景导航流程能力。"""

    def login_screenshot(self):
        self.active_and_send_mouse_delta(0, 0, activate=True, only_activate=True)
        self.sleep(0.1)
        return capture_window_by_screen(self.hwnd.hwnd)

    def login_ocr(self, x=0, y=0, to_x=1, to_y=1, match=None, width=0, height=0, box=None, name=None, threshold=0,
                  target_height=0, use_grayscale=False, log=False, frame_processor=None, lib='default'):
        img = self.login_screenshot()

        if not isinstance(img, np.ndarray):
            img = np.array(img)
        return super().ocr(x, y, to_x, to_y, match, width, height, box, name, threshold, img, target_height,
                           use_grayscale, log, frame_processor, lib)

    def login_find_feature(self, feature_name=None, horizontal_variance=0, vertical_variance=0, threshold=0,
                           use_gray_scale=False, x=-1, y=-1, to_x=-1, to_y=-1, width=-1, height=-1, box=None,
                           canny_lower=0, canny_higher=0, frame_processor=None, template=None,
                           match_method=cv2.TM_CCOEFF_NORMED, screenshot=False, mask_function=None, frame=None):
        img = self.login_screenshot()

        if not isinstance(img, np.ndarray):
            frame = np.array(img)
        return super().find_feature(feature_name, horizontal_variance, vertical_variance, threshold, use_gray_scale, x,
                                    y, to_x, to_y, width, height, box, canny_lower, canny_higher, frame_processor,
                                    template, match_method, screenshot, mask_function, frame)

    def skip_dialog(self):
        """跳过对话框，自动点击确认或跳过按钮。"""
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log_info("skip_dialog 超时退出")
                return False
            if self.find_one("skip_dialog_esc", horizontal_variance=0.05):
                self.send_key("esc", after_sleep=0.1)
                start = time.time()
                clicked_confirm = False
                while time.time() - start < 3:
                    confirm = self.find_confirm()
                    if confirm:
                        self.click(confirm, after_sleep=0.4)
                        clicked_confirm = True
                    elif clicked_confirm:
                        self.log_debug("AutoSkipDialogTask no confirm break")
                        return True
                    self.next_frame()
            self.sleep(0.5)

    def find_confirm(self):
        """寻找对话框中的确认按钮。"""
        return self.find_one(
            "skip_dialog_confirm", horizontal_variance=0.05, vertical_variance=0.05
        )

    def click_confirm(self, after_sleep=0.5, time_out=5, recheck_time=0):
        """点击对话框中的确认按钮。"""
        start_time = time.time()
        while True:
            if time.time() - start_time > time_out:
                self.log_info("点击确认超时")
                return False
            confirm = self.find_confirm()
            if confirm:
                self.click(confirm, after_sleep=after_sleep)
                if recheck_time > 0:
                    self.sleep(recheck_time)
                    if confirm := self.find_confirm():
                        self.click(confirm, after_sleep=after_sleep)
                        return True
                return True
            self.sleep(0.1)
            self.next_frame()

    def wait_pop_up(self, time_out=15, after_sleep=0):
        """等待奖励弹窗出现并点击 OK 按钮。"""
        count = 0
        start_time = time.time()
        while True:
            if time.time() - start_time > time_out:
                return False
            if count > 30:
                return False
            result = self.find_one(
                feature_name="reward_ok", box=self.box.bottom, threshold=0.8
            )
            if not result:
                result = self.wait_ocr(match=re.compile("空白"), time_out=1, box=self.box.bottom)
            if result:
                self.click(result, after_sleep=after_sleep)
                return True
            count += 1

    def wait_login(self):
        """处理登录界面的各种弹窗（月卡、签到、奖励等）。"""
        close = None
        if not self._logged_in:
            if self.in_world():
                self._logged_in = True
                return True
            elif self.find_one("monthly_card") or self.find_one("logout"):
                run_at_window_pos(self.hwnd.hwnd, super().click, self.width // 2, self.height // 2, 1, 0.5, 0.5)
                return False
            elif close := (
                self.find_one(
                    "reward_ok",
                    horizontal_variance=0.1,
                    vertical_variance=0.1,
                )
                or self.find_one("one_click_claim", horizontal_variance=0.1, vertical_variance=0.1)
                or self.find_one(
                    "check_in_close",
                    horizontal_variance=0.1,
                    vertical_variance=0.1,
                    threshold=0.75,
                )
            ):
                self.click(close, after_sleep=1)
                return False
        return False

    def find_reward_ok(self):
        """寻找奖励对话框中的确定按钮。"""
        return self.find_one("reward_ok", vertical_variance=0.05,
                             box=self.box_of_screen(1760 / 3840, 1760 / 2160, 2100 / 3840, 2100 / 2160))

    def find_f(self):
        """寻找 F 键提示（拾取物品）。"""
        return self.find_one("pick_f", vertical_variance=0.05)

    def read_essence_info(self) -> EssenceInfo | None:
        """读取当前屏幕中的精华信息（用于装备识别）。"""
        return read_essence_info(self)

    def in_bg(self):
        """判断游戏窗口是否在后台。"""
        return not self.hwnd.is_foreground()

    def in_combat_world(self):
        """判断是否在战斗场景中。"""
        in_combat_world = self.find_one("top_left_tab")
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def ensure_main(self, esc=True, time_out=60, after_sleep=2, need_active=True):
        """确保回到主界面（游戏世界），超时会抛出异常。"""
        self.info_set("current task", f"wait main esc={esc}")
        if not self.wait_until(
                lambda: self.is_main(esc=esc, need_active=need_active), time_out=time_out, raise_if_not_found=False
        ):
            raise Exception("Please start in game world and in team!")
        self.sleep(after_sleep)
        self.info_set("current task", f"in main esc={esc}")

    def in_world(self):
        """判断是否在游戏世界中（非菜单/对话状态）。"""
        main_world_features = ["esc"]

        in_world = all(self.find_one(f, vertical_variance=0.01, horizontal_variance=0.02) for f in main_world_features)

        if in_world:
            self._logged_in = True

        return in_world

    def is_main(self, esc=False, need_active=True):
        """判断是否处于可执行任务的主界面状态。"""
        self.next_frame()
        if not self._logged_in:
            if need_active:
                self.active_and_send_mouse_delta(activate=True, only_activate=True)
        if self.wait_until(self.in_world, time_out=1):
            self._logged_in = True
            return True
        if self.wait_login():
            return True
        if self.click_confirm(time_out=1):
            return False
        rules = [
            [None, None, [re.compile("空白"), re.compile("结束拜访")], self.box.bottom]
        ]
        if not self.run_ocr_rules(rules):
            return False
        if esc:
            self.back(after_sleep=self.once_sleep_time)
            return False
        return False

    def run_ocr_rules(self, rules: list[list]) -> bool:
        for need, need_box, match, box in rules:
            if need is not None:
                if not self.ocr(match=need, box=need_box, log=True):
                    continue

            if result := self.ocr(match=match, box=box, log=True):
                self.click_with_alt(result, after_sleep=self.once_sleep_time)
                return False

        return True

    def enter_home_room_list(self, timeout=6):
        """进入基地房间列表页面（i 面板）。"""
        self.log_info("进入基地房间列表页面")

        self.transfer_to_home_point(should_check_out_boat=True)
        self.press_key("i")

        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        room_keywords = [re.compile("会客室"), re.compile("制造")]

        results = self.wait_ocr(match=room_keywords, time_out=timeout, box=exchange_help_box)

        if results:
            self.log_info(f"已进入房间列表: {[r.name for r in results]}")
            return True

        self.log_info("未识别到房间列表")
        return False

    def to_model_area(self, area, model):
        """导航到指定区域的特定模块。"""
        need_change = True
        success = False

        for _ in range(3):
            self.press_key("y")
            check = self.wait_ocr(match=re.compile("建设"), box=self.box.top_left, time_out=5)
            if check:
                success = True
            else:
                self.log_info("未识别到区域且未检测到建设，重新尝试打开界面")
                continue
            result = self.wait_ocr(match=[re.compile(area) for area in areas_list], box=self.box.left, time_out=1)
            if result:
                success = True
                break
            else:
                self.log_info("未识别到区域且未检测到建设，重新尝试打开界面")

        if not success:
            self.log_error("未能识别到区域列表")
            return False
        for i in result:
            if area in i.name:
                need_change = False
                break
        if need_change:
            if not self.wait_click_ocr(
                match=re.compile("更换"), box=self.box.left, time_out=2, log=True
            ):
                return False
            if not self.wait_click_ocr(
                match=re.compile(area),
                box=self.box_of_screen(
                    648 / 1920, 196 / 1080, 648 / 1920 + 628 / 1920, 196 / 1080 + 192 / 1080
                ),
                time_out=4,
            ):
                return False
            if not self.wait_click_ocr(
                match=re.compile("确认"),
                box=self.box.bottom_right,
                time_out=2,
            ):
                return False
        box = self.wait_ocr(
            match=re.compile(f"{model}"), box=self.box.right, time_out=5
        )
        if box:
            self.click(box[0], move_back=True)
            self.wait_ocr(match=re.compile(f"{model[:2]}"), box=self.box.top_left)
            self.sleep(0.5)
            return True
        else:
            self.log_error(f"未找到‘{model}’按钮，任务中止。")
            return False

    def ensure_map(self, addtional_match=None, time_out=30):
        """确保进入地图界面。"""
        start_time = time.time()
        if addtional_match:
            match = [re.compile("事务")] + addtional_match if isinstance(addtional_match, list) else [
                re.compile("事务"), re.compile(addtional_match)]
        else:
            match = [re.compile("事务")]
        while not self.wait_ocr(match=match, time_out=2, box=self.box.top_left):
            if time.time() - start_time > time_out:
                raise Exception("进入地图失败")
            self.press_key("m")

    def in_friend_boat(self):
        """判断是否在好友的帝江号舰船中。"""
        return self.wait_ocr(match=re.compile("离开"), box=self.box.top_left)

    def ensure_in_friend_boat(self):
        """确保进入好友帝江号舰船。"""
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log_info("进入好友帝江号超时")
                return False
            if self.in_friend_boat():
                return True
