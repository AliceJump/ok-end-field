import random
import re
import time
from typing import List

import cv2
import imagehash
import numpy as np
import pyautogui
from PIL import Image
from ok import BaseTask, Box
from skimage.metrics import structural_similarity as ssim

from src.essence.essence_recognizer import EssenceInfo, read_essence_info
from src.interaction.Key import move_keys
from src.interaction.Mouse import active_and_send_mouse_delta, move_to_target_once, run_at_window_pos
from src.interaction.ScreenPosition import ScreenPosition as sP

TOLERANCE = 50


class BaseEfTask(BaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False

    def move_keys(self, keys, duration):
        move_keys(self.hwnd.hwnd, keys, duration)

    def move_to_target_once(self, hwnd, ocr_obj, max_step=100, min_step=20, slow_radius=200):
        return move_to_target_once(hwnd, ocr_obj, self.screen_center,max_step=max_step,min_step=min_step,slow_radius=slow_radius)
    def active_and_send_mouse_delta(self, hwnd, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
        return active_and_send_mouse_delta(hwnd, dx, dy, activate, only_activate, delay, steps)

    def isolate_white_yellow_text(self,frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # ===== 白色 =====
        lower_white = np.array([0, 0, 200], dtype=np.uint8)
        upper_white = np.array([180, 50, 255], dtype=np.uint8)

        # ===== 金色（双段）=====
        lower_gold_strong = np.array([18, 120, 170], dtype=np.uint8)
        upper_gold_strong = np.array([40, 255, 255], dtype=np.uint8)

        lower_gold_soft = np.array([18, 60, 140], dtype=np.uint8)
        upper_gold_soft = np.array([45, 200, 255], dtype=np.uint8)

        mask_white = cv2.inRange(hsv, lower_white, upper_white)
        mask_gold_strong = cv2.inRange(hsv, lower_gold_strong, upper_gold_strong)
        mask_gold_soft = cv2.inRange(hsv, lower_gold_soft, upper_gold_soft)

        mask_gold = cv2.bitwise_or(mask_gold_strong, mask_gold_soft)
        mask = cv2.bitwise_or(mask_gold, mask_white)

        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        mask = cv2.bitwise_not(mask)

        return cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    def click_with_alt(self, x: float | Box | List[Box] = -1, y: float = -1, move_back: bool = False, name: str | None = None, interval: int = -1, move: bool = True, down_time: float = 0.01, after_sleep: float = 0, key: str = 'left'):
        self.send_key_down("alt")
        self.sleep(0.5)
        self.click(x=x,y=y,move_back=move_back,name=name,interval=interval,move=move,down_time=down_time,after_sleep=after_sleep,key=key)
        self.send_key_up("alt")

    def scroll(self, x:int, y:int, count:int)->None:
        run_at_window_pos(self.hwnd.hwnd,super().scroll,x,y,0.5,x,y,count)

    def scroll_relative(self, x: float, y: float, count: int) -> None:
        run_at_window_pos(self.hwnd.hwnd,super().scroll_relative,int(x*self.width),int(y*self.height),0.5,x,y,count)

    def screen_center(self):
        return int(self.width / 2), int(self.height / 2)

    # def turn_direction(self, direction):
    #     if direction != "w":
    #         self.send_key(direction, down_time=0.05, after_sleep=0.5)
    #     self.center_camera()

    def wait_ui_stable(
        self,
        method="phash",
        threshold=5,
        stable_time=0.5,
        max_wait=5,
        refresh_interval=0.2,
    ):
        """
        等待界面稳定（UI 停止变化）再执行操作

        参数：
            method: "phash" / "dhash" / "pixel" / "ssim"，比较两帧相似度方法
            threshold: 方法对应的阈值
                - phash/dhash: 汉明距离，默认5
                - pixel: 平均像素差，默认5
                - ssim: 相似度，0~1，默认0.98
            stable_time: 连续稳定时间（秒），默认0.5秒
            max_wait: 最大等待时间（秒），默认5秒
            refresh_interval: 每次获取新帧的间隔（秒），默认0.2秒

        返回：
            True → UI 已稳定
            False → 超时仍未稳定
        """
        start_time = time.time()
        last_frame = self.next_frame()  # 获取初始帧
        stable_start = None

        while True:
            current_frame = self.next_frame()  # 获取最新帧

            # 1️⃣ phash / dhash 相似度
            if method in ("phash", "dhash"):
                img1 = Image.fromarray(last_frame)
                img2 = Image.fromarray(current_frame)
                h1 = (
                    imagehash.phash(img1)
                    if method == "phash"
                    else imagehash.dhash(img1)
                )
                h2 = (
                    imagehash.phash(img2)
                    if method == "phash"
                    else imagehash.dhash(img2)
                )
                distance = h1 - h2
                is_stable = distance <= threshold

            # 2️⃣ 像素差法
            elif method == "pixel":
                if last_frame.shape != current_frame.shape:
                    is_stable = False
                else:
                    diff = cv2.absdiff(last_frame, current_frame)
                    is_stable = np.mean(diff) <= threshold

            # 3️⃣ 结构相似度 SSIM
            elif method == "ssim":
                last_gray = cv2.cvtColor(last_frame, cv2.COLOR_BGR2GRAY)
                current_gray = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
                if last_gray.shape != current_gray.shape:
                    is_stable = False
                else:
                    score, _ = ssim(last_gray, current_gray, full=True)
                    is_stable = score >= threshold

            else:
                raise ValueError(f"Unknown method {method}")

            # 处理连续稳定时间
            if is_stable:
                if stable_start is None:
                    stable_start = time.time()
                elif time.time() - stable_start >= stable_time:
                    return True  # 连续稳定达到要求
            else:
                stable_start = None  # 重置计时

            # 超时退出
            if time.time() - start_time > max_wait:
                return False

            last_frame = current_frame
            time.sleep(refresh_interval)

    def align_ocr_or_find_target_to_center(
        self,
        ocr_match_or_feature_name_list,
        only_x=False,
        only_y=False,
        box=None,
        threshold=0.8,
        max_time=50,
        ocr=True,
        raise_if_fail=True,
        is_num=False,
        need_scroll=False,
        max_step=100,
        min_step=20,
        slow_radius=200,
        once_time=1,
        tolerance=TOLERANCE,
        ocr_frame_processor=None
    ):
        """
        Aligns a target detected by OCR or image feature to the center of the screen.
        将OCR识别的目标或图像特征目标对准屏幕中心。

        Parameters 参数:
        ocr_match_or_feature_name – str or Feature. OCR匹配模式或特征名称。
        only_x – bool. If True, only align the X axis. 是否仅对齐X轴。
        only_y – bool. If True, only align the Y axis. 是否仅对齐Y轴。
        box – Box or None. Screen area to search. 查找区域框，None表示全屏。
        threshold – float. Feature matching threshold. 特征匹配阈值。
        max_time – int. Maximum number of attempts. 最大尝试次数。
        ocr – bool. Whether to use OCR mode. 是否使用OCR模式。
        raise_if_fail – bool. Raise exception if alignment fails. 对中失败时是否抛出异常。
        is_num – bool. Adjust Y for numeric targets. 数字型目标Y坐标微调。

        Returns 返回值:
        bool. True if successfully aligned, False if failed and raise_if_fail is False.
        成功对中返回True，失败返回False（当raise_if_fail为False时）。"""
        if box:
            feature_box = box
        else:
            feature_box = self.box_of_screen(
                (1920 - 1550) / 1920,
                150 / 1080,
                1550 / 1920,
                (1080 - 150) / 1080,
            )
        last_target = None
        last_target_fail_count = 0
        success = False
        random_move_count = 0
        move_count = 0
        scroll_bool= False
        sum_dx=0
        sum_dy=0
        for i in range(max_time):
            start_action_time = time.time()
            if ocr:
                # 使用OCR模式识别目标，设置超时时间为2秒，并启用日志记录
                start_time = time.time()
                result=None
                while time.time() - start_time < 2:
                    frame = self.next_frame()
                    result = self.ocr(
                        match=ocr_match_or_feature_name_list,
                        box=box,
                        frame=frame,
                        log=True,
                        frame_processor=ocr_frame_processor,
                    )
                    if result:
                        break
                    time.sleep(0.1)
            else:
                if isinstance(ocr_match_or_feature_name_list, str):
                    ocr_match_or_feature_name_list = [ocr_match_or_feature_name_list]
                start_time = time.time()
                result = None
                while True:
                    if time.time() - start_time >= 2:
                        break
                    self.next_frame()
                    for feature_name in ocr_match_or_feature_name_list:
                        if time.time() - start_time >= 2:
                            break
                        
                        result = self.find_feature(
                            feature_name=feature_name,
                            threshold=threshold,
                            box=feature_box,
                        )
                        if result:
                            break
                    if result:
                        break
                    self.sleep(0.1)
            if result:
                success = True
                random_move_count = 0
                move_count = 0
                # OCR 成功
                if isinstance(result, list):
                    result = result[0]
                if is_num:
                    result.y = result.y - int(self.height * ((525 - 486) / 1080))
                if only_y:
                    result.x = self.width // 2 - result.width // 2
                if only_x:
                    result.y = self.height // 2 - result.height // 2
                target_center = (
                    result.x + result.width // 2,
                    result.y + result.height // 2,
                )
                screen_center_pos = self.screen_center()
                last_target = result
                last_target_fail_count = 0
                # 计算偏移量

                dx = target_center[0] - screen_center_pos[0]

                dy = target_center[1] - screen_center_pos[1]

                # 如果目标在容忍范围内
                if abs(dx) <= tolerance and abs(dy) <= tolerance:
                    return True
                else:
                    dx, dy = self.move_to_target_once(self.hwnd.hwnd, result,max_step=max_step,min_step=min_step,slow_radius=slow_radius)
                    sum_dx += dx
                    sum_dy += dy

            else:
                # 每次 OCR 失败，直接随机移动
                max_offset = 60  # 最大随机偏移
                if last_target:
                    decay = 0.9 ** last_target_fail_count
                    # 计算目标中心到屏幕中心的偏移

                    screen_center_x, screen_center_y = self.screen_center()
                    offset_x = int((screen_center_x - last_target.x) * decay)
                    offset_y = int((screen_center_y - last_target.y) * decay)
                    offset_width = int(last_target.width / 2 * decay)
                    offset_height = int(last_target.height / 2 * decay)
                    # 直接修改 last_target 坐标
                    last_target.x = screen_center_x - offset_x
                    last_target.y = screen_center_y - offset_y
                    last_target.width = offset_width
                    last_target.height = offset_height
                    dx, dy = self.move_to_target_once(self.hwnd.hwnd, last_target)
                    sum_dx += dx
                    sum_dy += dy
                    last_target_fail_count += 1
                    random_move_count = 0
                    move_count += 1
                    if move_count >= 10:
                        last_target = None
                        move_count = 0
                else:
                    if not success:
                        max_offset = self.width // 4
                    last_target = None
                    last_target_fail_count = 0
                    dx = random.randint(-max_offset, max_offset)
                    if not success:
                        dy = 0
                    else:
                        dy = random.randint(-max_offset, max_offset)

                    # 移动鼠标
                    active_and_send_mouse_delta(
                        self.hwnd.hwnd,
                        dx,
                        dy,
                        activate=True,
                        delay=0.1,
                    )
                    sum_dx += dx
                    sum_dy += dy
                    move_count = 0
                    random_move_count += 1
                    if random_move_count >= 10:
                        success = False
                        random_move_count = 0

            if time.time() - start_action_time < once_time:
                self.sleep(once_time - (time.time() - start_action_time))# OCR 成功后不需要处理，下一次失败仍然随机
            if not scroll_bool and need_scroll:
                scroll_bool=True
                # cx = int(self.width * 0.5)
                # cy = int(self.height * 0.5)
                for _ in range(6):
                    # self.scroll(cx, cy, 8)
                    pyautogui.scroll(80)
                    self.sleep(1)
        if raise_if_fail:
            raise Exception("对中失败")
        else:
            return False

    def to_model_area(self, area, model):
        self.send_key("y", after_sleep=2)
        if not self.wait_click_ocr(
                match="更换", box=sP.LEFT.value, time_out=2, after_sleep=2
        ):
            return
        if not self.wait_click_ocr(
                match=re.compile(area),
                box=self.box_of_screen(
                    648 / 1920, 196 / 1080, 648 / 1920 + 628 / 1920, 196 / 1080 + 192 / 1080
                ),
                time_out=2,
                after_sleep=2,
        ):
            return
        if not self.wait_click_ocr(
                match="确认",
                box=sP.BOTTOM_RIGHT.value,
                time_out=2,
                after_sleep=2,
        ):
            return
        box = self.wait_ocr(
            match=re.compile(f"{model}"), box=sP.RIGHT.value, time_out=5
        )
        if box:
            self.click(box[0], move_back=True, after_sleep=0.5)
        else:
            self.log_error(f"未找到‘{model}’按钮，任务中止。")
            return

    def skip_dialog(self, end_list=re.compile("确认"), end_box=sP.BOTTOM_RIGHT.value):
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log_info("skip_dialog 超时退出")
                return False
            if self.wait_ocr(match=["工业", "探索"], box="top_left", time_out=1.5):
                return True
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
            if end_list and self.wait_click_ocr(match=end_list, box=end_box, time_out=0.5):
                return True

    def in_bg(self):
        return not self.hwnd.is_foreground()

    def find_confirm(self):
        return self.find_one(
            "skip_dialog_confirm", horizontal_variance=0.05, vertical_variance=0.05
        )

    def in_combat_world(self):
        in_combat_world = self.find_one("top_left_tab")
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def find_f(self):
        return self.find_one("pick_f", vertical_variance=0.05)

    def ensure_main(self, esc=True, time_out=30,after_sleep=2):
        self.info_set("current task", f"wait main esc={esc}")
        if not self.wait_until(
                lambda: self.is_main(esc=esc), time_out=time_out, raise_if_not_found=False
        ):
            raise Exception("Please start in game world and in team!")
        self.sleep(after_sleep)
        self.info_set("current task", f"in main esc={esc}")

    def in_world(self):
        in_world = self.find_one("esc") and self.find_one("b") and self.find_one("c")
        if in_world:
            self._logged_in = True
        return in_world

    def is_main(self, esc=False):
        self.next_frame()  # 确保拿到最新的截图
        if self.in_world():
            self._logged_in = True
            return True
        if self.wait_login():
            return True
        if result:=self.ocr(match=re.compile("结束拜访"), box=sP.BOTTOM_RIGHT.value):
            self.click(result, after_sleep=1.5)
            return False
        if result:=self.ocr(match=[re.compile("确认"), re.compile("确定")], box=sP.BOTTOM_RIGHT.value):
            self.click(result, after_sleep=1.5)
            return False
        if esc:
            self.back(after_sleep=1.5)
            return False
        return False

    def wait_pop_up(self,after_sleep=0):
        count = 0
        while True:
            if count > 30:
                return False
            result = self.find_one(
                feature_name="reward_ok", box="bottom", threshold=0.8
            )
            if result:
                self.click(result, after_sleep=after_sleep)
                return True
            self.sleep(1)
            count += 1

    def wait_login(self):
        close=None
        if not self._logged_in:
            if self.in_world():
                self._logged_in = True
                return True
            elif self.find_one("monthly_card") or self.find_one("logout"):
                self.click(after_sleep=1)
                return False
            elif close := (
                    self.find_one(
                        "reward_ok",
                        horizontal_variance=0.1,
                        vertical_variance=0.1,
                    )
                    or self.find_one(
                "one_click_claim", horizontal_variance=0.1, vertical_variance=0.1
            )
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

    def read_essence_info(self) -> EssenceInfo | None:
        return read_essence_info(self)
