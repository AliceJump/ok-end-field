import threading
import time
from enum import Enum
from functools import partial
from typing import List

import cv2
import imagehash
import numpy as np
import win32gui
from PIL import Image
from ok import BaseTask, Box
from skimage.metrics import structural_similarity as ssim

from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
from src.config import config as app_config
from src.image.frame_processes import isolate_by_hsv_ranges
from src.data.FeatureList import FeatureList as fL
from src.interaction.Key import move_keys
from src.interaction.KeyConfig import KeyConfigManager
from src.interaction.Mouse import active_and_send_mouse_delta, move_to_target_once, run_at_window_pos
from src.interaction.ScreenPosition import ScreenPosition
from src.tasks.mixin.account_override_mixin import AccountOverrideMixin
from src.tasks.mixin.game_flow_mixin import GameFlowMixin
from src.tasks.mixin.process_manager import ProcessManager

feature_values = [f.value for f in fL]


def back_window(prev):
    current = win32gui.GetForegroundWindow()

    if prev and win32gui.IsWindow(prev) and current != prev:
        try:
            win32gui.SetForegroundWindow(prev)
        except:
            pass


class BaseEfTask(AccountOverrideMixin, GameFlowMixin, BaseTask, ProcessManager):
    """游戏自动化任务基类，提供通用的交互和识别功能"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False  # 记录是否已登录游戏
        self.current_user = ""  # 记录当前用户
        self.current_account_id = ""  # 记录当前账号稳定ID（优先用于账号覆盖）
        self.support_multi_account = False  # 明确标识该任务是否支持多账号执行逻辑
        self._bind_account_aware_config_get()
        self.box = ScreenPosition(self)  # 屏幕位置辅助对象，提供top/bottom/left/right等边界
        self.key_config = self.get_global_config('Game Hotkey Config')  # 获取全局热键配置
        self.once_sleep_time = self.get_global_config('Ensure Main Once Action Sleep').get("SingleActionWithDelay",
                                                                                           1.5)  # 获取全局配置的单次动作睡眠时间
        self.key_manager = KeyConfigManager(self.key_config)  # 初始化热键管理器
        self._detector = None
        self._detector_loading = False
        self._detector_loaded_event = threading.Event()
        self._start_detector_loading()

    def find_danger(self):
        danger_group_fixed = ["danger_" + str(i) for i in range(3, 6)]
        for danger in danger_group_fixed:
            result = self.find_one(danger, threshold=0.8, vertical_variance=0.01, horizontal_variance=0.01)
            if result:
                return True
        danger_group = ["danger_" + str(i) for i in range(1, 3)]
        danger_group_box = self.box_of_screen(640 / 1920, 480 / 1080, 1300 / 1920, 600 / 1080)
        for danger in danger_group:
            result = self.find_one(danger, threshold=0.8, box=danger_group_box, vertical_variance=0.01,
                                   horizontal_variance=0.01)
            if result:
                return True
        return False

    def click(self, x=-1, y=-1, move_back=False, name=None, interval=-1, move=True, down_time=0.01, after_sleep=0,
              key='left'):
        self.sleep(0.1)
        if self.find_danger():
            self.log_info("dangerous")
            self.kill_game()
            raise Exception("dangerous")
        return super().click(x, y, move_back, name, interval, move, down_time, after_sleep, key)

    def info_set(self, key, value):
        if self.current_user:
            key = f"{key}({self.current_user[-4:]})"
        return super().info_set(key, value)

    def find_feature(self, feature_name=None, horizontal_variance=0, vertical_variance=0, threshold=0,
                     use_gray_scale=False, x=-1, y=-1, to_x=-1, to_y=-1, width=-1, height=-1, box=None, canny_lower=0,
                     canny_higher=0, frame_processor=None, template=None, match_method=cv2.TM_CCOEFF_NORMED,
                     screenshot=False, mask_function=None, frame=None):
        feature_name = self.get_feature_by_resolution(feature_name)
        return super().find_feature(feature_name, horizontal_variance, vertical_variance, threshold, use_gray_scale, x,
                                    y, to_x, to_y, width, height, box, canny_lower, canny_higher, frame_processor,
                                    template, match_method, screenshot, mask_function, frame)

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

    def get_feature_by_resolution(self, base_name: str):
        cache_key = (base_name, self.width)

        if not hasattr(self, "_feature_cache"):
            self._feature_cache = {}

        if cache_key in self._feature_cache:
            return self._feature_cache[cache_key]

        # 分辨率优先级
        if self.width >= 3800:
            suffixes = ("_4k", "_2k", "")
        elif self.width >= 2500:
            suffixes = ("_2k", "_4k", "")
        else:
            suffixes = ("", "_2k", "_4k")

        for suffix in suffixes:
            feature_name = base_name + suffix
            if feature_name in feature_values:
                self._feature_cache[cache_key] = feature_name
                return feature_name

        raise AttributeError(f"未找到任何可用资源: {base_name}")

    def safe_back(self, match, box=None, time_out: float = 30, ocr_time_out: float = 2):
        """
        超时版本的返回操作：在 time_out 内等待 match 出现，如果未出现则执行 back。

        Args:
            match: OCR 匹配条件，通常是正则
            box: OCR 搜索区域
            time_out: 最大等待时间（秒）
            ocr_time_out: OCR 等待时间（秒）
        """
        self.start_time = time.time()
        while not self.wait_ocr(match=match, time_out=ocr_time_out, box=box):  # 每次短等待
            if time.time() - self.start_time > time_out:
                return False
            # 超时未找到，则执行 back
            self.back()
        return True

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

    def press_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        actual_key = self.key_manager.resolve_key(key, "common")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def press_industry_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        actual_key = self.key_manager.resolve_key(key, "industry")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def press_combat_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        actual_key = self.key_manager.resolve_key(key, "combat")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

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
            frame: np.ndarray | None = None,
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
        self.send_key_down("alt")  # 确认使用send_key：alt为系统修饰键，用于alt+点击操作，非游戏可配置热键
        self.sleep(0.5)
        self.click(x=x, y=y, move_back=move_back, name=name, interval=interval, move=move, down_time=down_time,
                   after_sleep=after_sleep, key=key)
        self.send_key_up("alt")  # 确认使用send_key：释放alt修饰键

    def screen_center(self) -> tuple[int, int]:
        """获取屏幕中心坐标
        
        Returns:
            tuple: (中心X, 中心Y)
        """
        return int(self.width / 2), int(self.height / 2)

    def wait_ui_stable(
            self,
            method="phash",
            threshold: int = 5,
            stable_time: float = 0.5,
            max_wait: float = 5,
            refresh_interval: float = 0.2,
            box: Box | tuple | list | None = None,
    ):
        """等待界面稳定（支持局部区域/对象）

        Args:
            method: 比较两帧相似度方法("phash"/"dhash"/"pixel"/"ssim")
            threshold: 方法对应的阈值
                - phash/dhash: 汉明距离，默认5
                - pixel: 平均像素差，默认5
                - ssim: 相似度(0~1)，默认0.98
            stable_time: 连续稳定时间（秒），默认0.5秒
            max_wait: 最大等待时间（秒），默认5秒
            refresh_interval: 每次获取新帧的间隔（秒），默认0.2秒
            box: 可选的屏幕区域（Box对象或(x,y,w,h)），仅监测该区域的稳定性
        Returns:
            bool: True表示UI已稳定，False表示超时仍未稳定
        """

        def parse_box(frame, box: Box | tuple | list | None):
            if box is None:
                return frame

            # ✅ 对象模式（优先）
            if hasattr(box, "x"):
                x = int(box.x)
                y = int(box.y)
                w = int(box.width)
                h = int(box.height)
                return frame[y:y + h, x:x + w]

            # ✅ tuple 兼容
            if isinstance(box, (tuple, list)) and len(box) == 4:
                x, y, w, h = map(int, box)
                return frame[y:y + h, x:x + w]

            raise ValueError("box must be None / (x,y,w,h) / object(x,y,width,height)")

        start_time = time.time()
        last_frame = parse_box(self.next_frame(), box)
        stable_start = None

        while True:
            current_frame = parse_box(self.next_frame(), box)

            # ===== 相似度 =====
            if method in ("phash", "dhash"):
                img1 = Image.fromarray(last_frame)
                img2 = Image.fromarray(current_frame)

                h1 = imagehash.phash(img1) if method == "phash" else imagehash.dhash(img1)
                h2 = imagehash.phash(img2) if method == "phash" else imagehash.dhash(img2)

                is_stable = (h1 - h2) <= threshold

            elif method == "pixel":
                if last_frame.shape != current_frame.shape:
                    is_stable = False
                else:
                    diff = cv2.absdiff(last_frame, current_frame)
                    is_stable = np.mean(diff) <= threshold

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

            # ===== 稳定计时 =====
            if is_stable:
                if stable_start is None:
                    stable_start = time.time()
                elif time.time() - stable_start >= stable_time:
                    return True
            else:
                stable_start = None

            if time.time() - start_time > max_wait:
                return False

            last_frame = current_frame
            self.sleep(refresh_interval)
