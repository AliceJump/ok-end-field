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
from ok import Box
from skimage.metrics import structural_similarity as ssim

from src.OpenVinoYolo8Detect import OpenVinoYolo8Detect
from src.config import config as app_config
from src.data.FeatureList import FeatureList as fL
from src.image.frame_processes import isolate_by_hsv_ranges
from src.interaction.Key import move_keys as send_move_keys
from src.interaction.Mouse import (
    active_and_send_mouse_delta as send_mouse_delta,
    move_to_target_once as move_to_target_once_impl,
    run_at_window_pos,
)

feature_values = [f.value for f in fL]


def _back_window(prev):
    current = win32gui.GetForegroundWindow()

    if prev and win32gui.IsWindow(prev) and current != prev:
        try:
            win32gui.SetForegroundWindow(prev)
        except Exception:
            pass


class RuntimeMixin:
    """视觉识别、按键输入、鼠标控制与模型加载能力。"""

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
              key='left', hcenter=False, vcenter=False):
        self.sleep(0.1)
        if self.find_danger():
            self.log_info("dangerous")
            self.kill_game()
            raise Exception("dangerous")
        return super().click(x, y, move_back=move_back, name=name, interval=interval, move=move,
                             down_time=down_time, after_sleep=after_sleep, key=key,
                             hcenter=hcenter, vcenter=vcenter)

    def find_feature(self, feature_name=None, horizontal_variance=0, vertical_variance=0, threshold=0,
                     use_gray_scale=False, x=-1, y=-1, to_x=-1, to_y=-1, width=-1, height=-1, box=None, canny_lower=0,
                     canny_higher=0, frame_processor=None, template=None, match_method=cv2.TM_CCOEFF_NORMED,
                     screenshot=False, mask_function=None, frame=None):
        feature_name = self.get_feature_by_resolution(feature_name)
        return super().find_feature(feature_name, horizontal_variance, vertical_variance, threshold, use_gray_scale, x,
                                    y, to_x, to_y, width, height, box, canny_lower, canny_higher, frame_processor,
                                    template, match_method, screenshot, mask_function, frame)

    def scroll(self, x: int, y: int, count: int) -> None:
        run_at_window_pos(self.hwnd.hwnd, super().scroll, x, y, 0.5, x, y, count)

    def scroll_relative(self, x: float, y: float, count: int) -> None:
        run_at_window_pos(self.hwnd.hwnd, super().scroll_relative, int(x * self.width), int(y * self.height), 0.5, x,
                          y, count)

    def get_feature_by_resolution(self, base_name: str):
        cache_key = (base_name, self.width)

        if not hasattr(self, "_feature_cache"):
            self._feature_cache = {}

        if cache_key in self._feature_cache:
            return self._feature_cache[cache_key]

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
        self.start_time = time.time()
        while not self.wait_ocr(match=match, time_out=ocr_time_out, box=box):
            if time.time() - self.start_time > time_out:
                return False
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

        self._start_detector_loading()
        self._detector_loaded_event.wait()

        return self._detector

    def isolate_by_hsv_ranges(self, frame, ranges, invert=True, kernel_size=2):
        return isolate_by_hsv_ranges(frame, ranges, invert, kernel_size)

    def make_hsv_isolator(self, ranges):
        return partial(self.isolate_by_hsv_ranges, ranges=ranges)

    def yolo_detect(
            self,
            name: str | list[str],
            frame: np.ndarray | None = None,
            box: Box | None = None,
            conf: float = 0.7,
    ) -> list[Box]:
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

        if box is not None:
            detect_frame = box.crop_frame(frame)
            offset_x = int(box.x)
            offset_y = int(box.y)

        detections = self.detector.detect(detect_frame, threshold=conf)
        self.log_info(f"yolo_detect: raw detections count = {len(detections)}")
        results: list[Box] = []

        for det in detections:
            self.log_info(f"Raw detection: name={getattr(det, 'name', None)}, conf={det.confidence:.3f}")
            if getattr(det, "name", None) not in target_names:
                continue

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

    def wait_ui_stable(
            self,
            method="phash",
            threshold: int = 5,
            stable_time: float = 0.5,
            max_wait: float = 5,
            refresh_interval: float = 0.2,
            box: Box | tuple | list | None = None,
    ):
        def parse_box(frame, box: Box | tuple | list | None):
            if box is None:
                return frame

            if hasattr(box, "x"):
                x = int(box.x)
                y = int(box.y)
                w = int(box.width)
                h = int(box.height)
                return frame[y:y + h, x:x + w]

            if isinstance(box, (tuple, list)) and len(box) == 4:
                x, y, w, h = map(int, box)
                return frame[y:y + h, x:x + w]

            raise ValueError("box must be None / (x,y,w,h) / object(x,y,width,height)")

        start_time = time.time()
        last_frame = parse_box(self.next_frame(), box)
        stable_start = None

        while True:
            current_frame = parse_box(self.next_frame(), box)

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

    def info_set(self, key, value):
        if self.current_user:
            key = f"{key}({self.current_user[-4:]})"
        return super().info_set(key, value)

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
        if need_back:
            prev = win32gui.GetForegroundWindow()
        send_move_keys(self.hwnd.hwnd, keys, duration)
        if need_back:
            _back_window(prev)

    def _dodge_with_direction(self, direction_key: str, pre_hold: float = 0.004,
                              dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        move_thread = threading.Thread(target=self.move_keys, args=(direction_key, pre_hold), daemon=True)
        move_thread.start()
        self.sleep(0.005)
        self.press_key('lshift', down_time=dodge_down_time)
        move_thread.join(timeout=max(pre_hold + 0.002, 0.05))
        if after_sleep > 0:
            self.sleep(after_sleep)

    def dodge_forward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        self._dodge_with_direction('w', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def dodge_backward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        self._dodge_with_direction('s', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def move_to_target_once(self, ocr_obj, max_step=100, min_step=20, slow_radius=200):
        return move_to_target_once_impl(
            self.hwnd.hwnd,
            ocr_obj,
            self.screen_center,
            max_step=max_step,
            min_step=min_step,
            slow_radius=slow_radius,
        )

    def active_and_send_mouse_delta(self, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
        return send_mouse_delta(self.hwnd.hwnd, dx, dy, activate, only_activate, delay, steps)

    def click_with_alt(self, x: int | float | Box | List[Box] = -1, y: int | float = -1, move_back: bool = False,
                       name: str | None = None, interval: int = -1, move: bool = True, down_time: float = 0.01,
                       after_sleep: float = 0, key: str = 'left'):
        self.send_key_down("alt")
        self.sleep(0.5)
        self.click(x=x, y=y, move_back=move_back, name=name, interval=interval, move=move, down_time=down_time,
                   after_sleep=after_sleep, key=key)
        self.send_key_up("alt")

    def screen_center(self) -> tuple[int, int]:
        return int(self.width / 2), int(self.height / 2)
