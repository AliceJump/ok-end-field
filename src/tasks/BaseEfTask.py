import re
import threading
import time
from enum import Enum
from functools import partial
from typing import List

import cv2
import imagehash
import numpy as np
import win32process, win32gui, win32api, win32con
from PIL import Image
from ok import BaseTask, Box
from skimage.metrics import structural_similarity as ssim

from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
from src.config import config as app_config
from src.essence.essence_recognizer import EssenceInfo, read_essence_info
from src.image.frame_processes import isolate_by_hsv_ranges
from src.interaction.Key import move_keys
from src.interaction.KeyConfig import KeyConfigManager
from src.interaction.Mouse import active_and_send_mouse_delta, move_to_target_once, run_at_window_pos, run_in_window, click_down, click_up
from src.interaction.ScreenPosition import ScreenPosition
from src.data.world_map import areas_list


def back_window(prev):
    current = win32gui.GetForegroundWindow()

    if prev and win32gui.IsWindow(prev) and current != prev:
        try:
            win32gui.SetForegroundWindow(prev)
        except:
            pass


class BaseEfTask(BaseTask):
    """游戏自动化任务基类，提供通用的交互和识别功能"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False  # 记录是否已登录游戏
        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_config = self.get_global_config('Game Hotkey Config')  # 获取全局热键配置
        self.once_sleep_time = self.get_global_config('Ensure Main Once Action Sleep').get("SingleActionWithDelay",
                                                                                           1.5)  # 获取全局配置的单次动作睡眠时间
        self.key_manager = KeyConfigManager(self.key_config)  # 初始化热键管理器
        self._detector = None
        self._detector_loading = False
        self._detector_loaded_event = threading.Event()

        self._start_detector_loading()

    def safe_back(self, match, box=None, time_out: float = 1, after_sleep: float = 1):
        """
        超时版本的返回操作：在 time_out 内等待 match 出现，如果未出现则执行 back。

        Args:
            match: OCR 匹配条件，通常是正则
            box: OCR 搜索区域
            time_out: 最大等待时间（秒）
            after_sleep: 每次 back 后等待时间
        """
        while not self.wait_ocr(match=match, time_out=time_out, box=box):  # 每次短等待
            # 超时未找到，则执行 back
            self.back(after_sleep=after_sleep)

    def _start_detector_loading(self):

        def load_model():

            self._detector_loading = True

            try:
                yolo_config = app_config.get("yolo", {})
                model_path = yolo_config.get("model_path", "models/yolo/best.onnx")

                self._detector = OpenVinoYolo8Detect(weights=model_path)

            finally:
                self._detector_loading = False
                self._detector_loaded_event.set()

        threading.Thread(target=load_model, daemon=True).start()

    @property
    def detector(self):

        if self._detector:
            return self._detector

        if self._detector_loading:
            self._detector_loaded_event.wait()
            return self._detector

        # 极端情况：线程没启动
        self._start_detector_loading()
        self._detector_loaded_event.wait()

        return self._detector

    def press_game_key(
            self,
            key: str,
            key_type: str = "common",
            down_time: float = 0.02,
            after_sleep: float = 0,
            interval: int = -1
    ):

        if key_type == "common":
            actual_key = self.key_manager.resolve_common_key(key)
        elif key_type == "industry":
            actual_key = self.key_manager.resolve_industry_key(key)
        elif key_type == "combat":
            actual_key = self.key_manager.resolve_combat_key(key)
        else:
            raise ValueError(f"未知 key_type: {key_type}")

        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def press_key(self, key, **kwargs):
        return self.press_game_key(key, "common", **kwargs)

    def press_industry_key(self, key, **kwargs):
        return self.press_game_key(key, "industry", **kwargs)

    def press_combat_key(self, key, **kwargs):
        return self.press_game_key(key, "combat", **kwargs)

    def move_keys(self, keys, duration, need_back=False):
        """向当前窗口发送按键移动指令
        
        Args:
            keys: 按键或按键列表，例如 "w" 或 ["w", "a"]
            duration: 按键持续时间（秒），例如 0.5
            need_back: 是否需要回到之前的窗口
        """
        if need_back:
            prev = win32gui.GetForegroundWindow()
        move_keys(self.hwnd.hwnd, keys, duration)
        if need_back:
            back_window(prev)

    def _dodge_with_direction(self, direction_key: str, pre_hold: float = 0.004,
                              dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """按住方向键后触发闪避键。

        Args:
            direction_key: 方向键，通常为 'w'（前）或 's'（后）
            pre_hold: 方向键预按时长（秒）
            dodge_down_time: 闪避键按下时长（秒）
            after_sleep: 动作结束后等待时长（秒）
        """
        # WASD 移动统一走 move_keys（src/interaction/Key.py）
        # 与闪避键并发执行，保证“同时”触发
        move_thread = threading.Thread(target=self.move_keys, args=(direction_key, pre_hold), daemon=True)
        move_thread.start()
        self.sleep(0.005)
        # 闪避键支持全局热键映射（默认 lshift）
        self.press_key('lshift', down_time=dodge_down_time)
        move_thread.join(timeout=max(pre_hold + 0.002, 0.05))
        if after_sleep > 0:
            self.sleep(after_sleep)

    def dodge_forward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """向前闪避（W + 闪避键）。"""
        self._dodge_with_direction('w', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def dodge_backward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """向后闪避（S + 闪避键）。"""
        self._dodge_with_direction('s', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def move_to_target_once(self, ocr_obj, max_step=100, min_step=20, slow_radius=200):
        """根据目标位置执行一次视角/鼠标对准
        
        Args:
            ocr_obj: OCR识别到的目标对象
            max_step: 最大移动步长
            min_step: 最小移动步长
            slow_radius: 减速半径
        """
        return move_to_target_once(self.hwnd.hwnd, ocr_obj, self.screen_center, max_step=max_step, min_step=min_step,
                                   slow_radius=slow_radius)

    def active_and_send_mouse_delta(self, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
        """激活窗口并发送鼠标位移
        
        Args:
            dx: 水平位移
            dy: 垂直位移
            activate: 是否先激活窗口
            only_activate: 是否仅激活不移动
            delay: 每步延迟
            steps: 分步次数
        """
        return active_and_send_mouse_delta(self.hwnd.hwnd, dx, dy, activate, only_activate, delay, steps)

    def isolate_by_hsv_ranges(self, frame, ranges, invert=True, kernel_size=2):
        """按HSV范围提取颜色区域
        
        Args:
            frame: 输入图像（BGR）
            ranges: HSV区间列表
            invert: 是否反转结果
            kernel_size: 形态学核大小
        """
        return isolate_by_hsv_ranges(frame, ranges, invert, kernel_size)

    def make_hsv_isolator(self, ranges):
        """生成固定HSV范围的图像处理函数
        
        Args:
            ranges: HSV区间列表
        """
        return partial(self.isolate_by_hsv_ranges, ranges=ranges)

    def yolo_detect(
            self,
            name: str | list[str],
            frame=None,
            box: Box | None = None,
            conf: float = 0.7,
    ) -> list[Box]:
        """使用 YOLO 识别目标，并按名称过滤后返回 Box 列表。"""

        if not name:
            raise ValueError("yolo_detect 至少需要传入一个 name")
        raw_names = [name] if isinstance(name, str) else name
        target_names = {
            str(n.value) if isinstance(n, Enum) else str(n)
            for n in raw_names
            if n is not None
        }

        frame = frame if frame is not None else self.next_frame()
        if frame is None:
            return []

        offset_x = 0
        offset_y = 0
        detect_frame = frame

        # ROI裁剪
        if box is not None:
            detect_frame = box.crop_frame(frame)
            offset_x = int(box.x)
            offset_y = int(box.y)

        # YOLO检测
        detections = self.detector.detect(detect_frame, threshold=conf)
        self.log_info(f"yolo_detect: raw detections count = {len(detections)}")
        results: list[Box] = []

        for det in detections:
            self.log_info(f"Raw detection: name={getattr(det, 'name', None)}, conf={det.confidence:.3f}")
            if getattr(det, "name", None) not in target_names:
                continue

            # 重新生成 Box（加偏移）
            new_box = Box(
                int(det.x + offset_x),
                int(det.y + offset_y),
                int(det.width),
                int(det.height),
            )

            new_box.name = det.name
            new_box.confidence = det.confidence

            results.append(new_box)

        self.log_info(f"yolo_detect: filtered detections count = {len(results)}")

        return sorted(results, key=lambda item: item.confidence, reverse=True)

    def click_with_alt(self, x: int | float | Box | List[Box] = -1, y: int | float = -1, move_back: bool = False,
                       name: str | None = None, interval: int = -1, move: bool = True, down_time: float = 0.01,
                       after_sleep: float = 0, key: str = 'left'):
        """按住Alt并点击指定位置
        
        Args:
            x: 点击X坐标（0-1为比例，或像素值）
            y: 点击Y坐标
            move_back: 点击后是否移回原位
            name: 点击目标名称
            interval: 多次点击间隔
            move: 是否移动鼠标到目标
            down_time: 鼠标按下时间
            after_sleep: 点击后等待时间
            key: 鼠标按键('left'/'right'/'middle')
        """
        self.send_key_down("alt")
        self.sleep(0.5)
        self.click(x=x, y=y, move_back=move_back, name=name, interval=interval, move=move, down_time=down_time,
                   after_sleep=after_sleep, key=key)
        self.send_key_up("alt")

    def scroll(self, x: int, y: int, count: int) -> None:
        """在指定像素坐标滚动鼠标滚轮
        
        Args:
            x: 滚动位置X坐标（像素）
            y: 滚动位置Y坐标（像素）
            count: 滚动次数（正数向上，负数向下）
        """
        run_at_window_pos(self.hwnd.hwnd, super().scroll, x, y, 0.5, x, y, count)

    def scroll_relative(self, x: float, y: float, count: int) -> None:
        """在指定比例坐标滚动鼠标滚轮
        
        Args:
            x: 滚动位置X坐标（0-1的比例）
            y: 滚动位置Y坐标（0-1的比例）
            count: 滚动次数
        """
        run_at_window_pos(self.hwnd.hwnd, super().scroll_relative, int(x * self.width), int(y * self.height), 0.5, x, y,
                          count)

    def screen_center(self):
        """获取屏幕中心坐标
        
        Returns:
            tuple: (中心X, 中心Y)
        """
        return int(self.width / 2), int(self.height / 2)

    def wait_ui_stable(
            self,
            method="phash",
            threshold=5,
            stable_time=0.5,
            max_wait=5,
            refresh_interval=0.2,
    ):
        """等待界面稳定（UI停止变化）再执行操作
        
        Args:
            method: 比较两帧相似度方法("phash"/"dhash"/"pixel"/"ssim")
            threshold: 方法对应的阈值
                - phash/dhash: 汉明距离，默认5
                - pixel: 平均像素差，默认5
                - ssim: 相似度(0~1)，默认0.98
            stable_time: 连续稳定时间（秒），默认0.5秒
            max_wait: 最大等待时间（秒），默认5秒
            refresh_interval: 每次获取新帧的间隔（秒），默认0.2秒
        
        Returns:
            bool: True表示UI已稳定，False表示超时仍未稳定
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
            self.sleep(refresh_interval)

    def enter_home_room_list(self, timeout=6):
        """
        进入基地房间列表页面（i 面板）

        Returns:
            bool: 是否成功进入
        """

        self.log_info("进入基地房间列表页面")

        # 1️⃣ 回到基地
        self.transfer_to_home_point(should_check_out_boat=True)

        # 2️⃣ 打开 i 面板
        self.press_key("i")

        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)

        # 3️⃣ 判定是否进入成功（识别房间关键词）
        room_keywords = [re.compile("会客室"), re.compile("制造")]

        results = self.wait_ocr(match=room_keywords, time_out=timeout, box=exchange_help_box)

        if results:
            self.log_info(f"已进入房间列表: {[r.name for r in results]}")
            return True

        self.log_info("未识别到房间列表")
        return False

    def to_model_area(self, area, model):
        """导航到指定区域的特定模块
        
        Args:
            area: 区域名称（如'地区建设'、'仓储'等）
            model: 模块名称（如'仓储节点'、'据点管理'等）
        """
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

    def skip_dialog(self, end_list=re.compile("确认"), end_box=None):
        """跳过对话框，自动点击"确认"或"跳过"按钮
        
        Args:
            end_list: 结束按钮的OCR匹配模式（默认'确认'）
            end_box: 查找按钮的区域框（默认右下角）
        
        Returns:
            bool: 成功跳过返回True，超时返回False
        """
        if not end_box:
            end_box = self.box.bottom_right
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log_info("skip_dialog 超时退出")
                return False
            if self.wait_ocr(match=["工业", "探索"], box=self.box.top_left, time_out=2):
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
            if end_list and self.wait_click_ocr(match=end_list, box=end_box, time_out=1):
                return True

    def in_bg(self):
        """判断游戏窗口是否在后台
        
        Returns:
            bool: True表示在后台，False表示在前台
        """
        return not self.hwnd.is_foreground()

    def find_confirm(self):
        """寻找对话框中的"确认"按钮
        
        Returns:
            Box: 找到的按钮位置，否则None
        """
        return self.find_one(
            "skip_dialog_confirm", horizontal_variance=0.05, vertical_variance=0.05
        )

    def in_combat_world(self):
        """判断是否在战斗场景中
        
        Returns:
            bool: True表示在战斗，False表示不在
        """
        in_combat_world = self.find_one("top_left_tab")
        if in_combat_world:
            self._logged_in = True
        return in_combat_world

    def find_reward_ok(self):
        """寻找"奖励"对话框中的"确定"按钮

        Returns:
            Box: 找到的按钮位置，否则None
        """
        return self.find_one("reward_ok", vertical_variance=0.05,
                             box=self.box_of_screen(1760 / 3840, 1760 / 2160, 2100 / 3840, 2100 / 2160))

    def find_f(self):
        """寻找"F"键提示（拾取物品）
        
        Returns:
            Box: 找到的F键提示位置
        """
        return self.find_one("pick_f", vertical_variance=0.05)

    def in_friend_boat(self):
        """判断是否在好友的帝江号舰船中
        
        Returns:
            bool: True表示在舰船中
        """
        return self.wait_ocr(match=re.compile("离开"), box=self.box.top_left)

    def ensure_in_friend_boat(self):
        """确保进入好友帝江号舰船，超时30秒
        
        Returns:
            bool: 成功进入返回True，超时返回False
        """
        start_time = time.time()
        while True:
            if time.time() - start_time > 30:
                self.log_info("进入好友帝江号超时")
                return False
            if self.in_friend_boat():
                return True

    def ensure_main(self, esc=True, time_out=60, after_sleep=2, need_active=True):
        """确保回到主界面（游戏世界），超时会抛出异常
        
        Args:
            esc: 是否按ESC键返回主界面
            time_out: 等待超时时间（秒）
            after_sleep: 完成后等待时间（秒）
            need_active: 是否需要激活窗口（默认True）
        
        Raises:
            Exception: 无法回到主界面时抛出异常
        """
        self.info_set("current task", f"wait main esc={esc}")
        if not self.wait_until(
                lambda: self.is_main(esc=esc, need_active=need_active), time_out=time_out, raise_if_not_found=False
        ):
            raise Exception("Please start in game world and in team!")
        self.sleep(after_sleep)
        self.info_set("current task", f"in main esc={esc}")

    def in_world(self):
        """判断是否在游戏世界中（非菜单/对话状态）
        
        Returns:
            bool: True表示在世界中
        """
        in_world = self.find_one("esc") and self.find_one("b") and self.find_one("c")
        if in_world:
            self._logged_in = True
        return in_world

    def is_main(self, esc=False, need_active=True):
        """判断是否处于可执行任务的主界面状态
            
        Args:
            esc: 如果是菜单状态，是否按ESC返回（返回False不意味着不是主界面）
            need_active: 是否需要激活窗口（默认True）
            
        Returns:
            bool: True表示处于主界面，False表示需要继续处理
        """
        self.next_frame()  # 确保拿到最新的截图
        if not self._logged_in:
            if need_active:
                self.active_and_send_mouse_delta(activate=True, only_activate=True)  # 激活窗口，获取最新状态
        if self.in_world():
            self._logged_in = True
            return True
        if self.wait_login():
            return True
        rules = [
            [[re.compile("离开"), re.compile("退出"), re.compile("结束")], self.box.center, [re.compile("确认"), re.compile("确定")], self.box.bottom_right],
            [None, None, re.compile("结束拜访"), self.box.bottom_right],
            [None, None, re.compile("空白"), self.box.bottom]
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
                if not self.ocr(match=need, box=need_box):
                    continue

            if result := self.ocr(match=match, box=box):
                self.click_with_alt(result, after_sleep=self.once_sleep_time)
                return False

        return True

    def ensure_map(self, addtional_match=None, time_out=30):
        """确保进入地图界面，超时30秒"""
        self.ensure_main()
        start_time = time.time()
        if addtional_match:
            match = [re.compile("事务")]+addtional_match if isinstance(addtional_match, list) else [re.compile("事务"), re.compile(addtional_match)]
        else:
            match = [re.compile("事务")]
        while not self.wait_ocr(match=match, time_out=2, box=self.box.top_left):
            if time.time() - start_time > time_out:
                raise Exception("进入地图失败")
            self.press_key("m")

    def wait_pop_up(self,time_out=15, after_sleep=0):
        """等待奖励弹窗出现并点击"OK"按钮
        
        Args:
            after_sleep: 点击后等待时间（秒）
        
        Returns:
            bool: 成功点击返回True，超时返回False
        """
        count = 0
        start_time=time.time()
        while True:
            if time.time()-start_time>time_out:
                return False
            if count > 30:
                return False
            result = self.find_one(
                feature_name="reward_ok", box=self.box.bottom, threshold=0.8
            )
            if not result:
                result=self.wait_ocr(match=re.compile("空白"),time_out=1,box=self.box.bottom)
            if result:
                self.click(result, after_sleep=after_sleep)
                return True
            self.sleep(1)
            count += 1

    def wait_login(self):
        """处理登录界面的各种弹窗（月卡、签到、奖励等）
        
        Returns:
            bool: 成功处理返回True（进入世界），否则False
        """
        close = None
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
        """读取当前屏幕中的精华信息（用于装备识别）
        
        Returns:
            EssenceInfo: 精华信息对象，失败返回None
        """
        return read_essence_info(self)

    def kill_game(self):
        try:

            hwnd = self.hwnd.hwnd
            if hwnd:
                tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                win32api.TerminateProcess(handle, 0)
                win32api.CloseHandle(handle)
                self.log_info(f"已终止进程 pid={pid}", notify=True)
            else:
                self.log_info("未获取到 hwnd，无法终止进程", notify=True)
        except Exception as e2:
            self.log_info(f"终止进程失败: {e2}", notify=True)

    def kill_all_related_processes(self):
        """尝试杀死游戏进程和本软件自身进程（除当前进程外）"""
        import os, sys
        import win32process, win32gui, win32api, win32con
        import psutil

        # 1. 杀死游戏进程
        try:
            hwnd = getattr(self, "hwnd", None)
            if hwnd is not None:
                hwnd_val = getattr(hwnd, "hwnd", None)
                if hwnd_val:
                    tid, pid = win32process.GetWindowThreadProcessId(hwnd_val)
                    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                    win32api.TerminateProcess(handle, 0)
                    win32api.CloseHandle(handle)
                    self.log_info(f"已终止游戏进程 pid={pid}", notify=True)
                else:
                    self.log_info("未获取到 hwnd，无法终止游戏进程", notify=True)
            else:
                self.log_info("未获取到 hwnd 属性，无法终止游戏进程", notify=True)
        except Exception as e:
            self.log_info(f"终止游戏进程失败: {e}", notify=True)
        # 2. 杀死本软件所有同名进程（除当前进程）
        try:
            current_pid = os.getpid()
            exe_name = psutil.Process(current_pid).name()
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] == exe_name and proc.info["pid"] != current_pid:
                    try:
                        proc.kill()
                        self.log_info(f"已终止本软件进程 pid={proc.info['pid']}", notify=True)
                    except Exception as e2:
                        self.log_info(f"终止本软件进程失败: {e2}", notify=True)
        except Exception as e:
            self.log_info(f"查找/终止本软件进程失败: {e}", notify=True)
