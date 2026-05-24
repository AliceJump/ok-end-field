import gc
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

from src.config import config as app_config
from src.data.FeatureList import FeatureList as fL
from src.image.frame_processes import isolate_by_hsv_ranges
from src.interaction.Key import move_keys as send_move_keys
from src.interaction.Mouse import (
    active_and_send_mouse_delta as send_mouse_delta,
    move_to_target_once as move_to_target_once_impl,
    run_at_window_pos,
)
from src.yolo.loader import YoloModelLoader
from src.image.rotated_template import rotated_template_match

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
    BASE_WIDTH = 1920
    BASE_HEIGHT = 1080

    def resolution_scale(self) -> float:
        width = getattr(self, "width", self.BASE_WIDTH) or self.BASE_WIDTH
        height = getattr(self, "height", self.BASE_HEIGHT) or self.BASE_HEIGHT
        return min(width / self.BASE_WIDTH, height / self.BASE_HEIGHT)

    def scale_distance(self, value: int | float, minimum: int = 1) -> int:
        return max(minimum, int(round(value * self.resolution_scale())))

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
                     screenshot=False, mask_function=None, frame=None, limit=0, target_height=0):
        feature_name = self.get_feature_by_resolution(feature_name)
        return super().find_feature(feature_name, horizontal_variance, vertical_variance, threshold, use_gray_scale, x,
                                    y, to_x, to_y, width, height, box, canny_lower, canny_higher, frame_processor,
                                    template, match_method, screenshot, mask_function, frame, limit, target_height)

    def scroll(self, x: int, y: int, count: int) -> None:
        """按屏幕绝对像素坐标滚轮。

        Args:
            x: 滚动位置的绝对像素 X 坐标
            y: 滚动位置的绝对像素 Y 坐标
            count: 滚动量。
                正数（向上滚动）：地图 UI 放大视角 / 列表 UI 向上翻页显示靠前内容。
                负数（向下滚动）：地图 UI 缩小视角或向下平移 / 列表 UI 向下翻页显示靠后内容。

        适用场景：
        - 地图 UI：已确定地图中心/图标附近的像素坐标时，精确缩放或平移视角。
        - 列表 UI：已通过 OCR/特征拿到某一行条目的绝对坐标时，在该条目处滚动翻页。
        """
        run_at_window_pos(self.hwnd.hwnd, super().scroll, x, y, 0.5, x, y, count)

    def scroll_relative(self, x: float, y: float, count: int) -> None:
        """按屏幕相对坐标比例滚轮（x/y 范围 0~1）。

        Args:
            x: 滚动位置的相对 X 坐标（0~1，0 为左边缘，1 为右边缘）
            y: 滚动位置的相对 Y 坐标（0~1，0 为上边缘，1 为下边缘）
            count: 滚动量。
                正数（向上滚动）：地图 UI 放大视角 / 列表 UI 向上翻页显示靠前内容。
                负数（向下滚动）：地图 UI 缩小视角或向下平移 / 列表 UI 向下翻页显示靠后内容。

        适用场景：
        - 地图 UI：用 (0.5, 0.5) 等比例坐标在地图中心连续缩放，适配不同分辨率。
        - 列表 UI：在固定相对区域（如左侧列表 0.1/0.5）滚动查找条目，避免硬编码像素。
        """
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

    def yolo_loader(self) -> YoloModelLoader:
        loader = getattr(self, "_yolo_loader", None)
        if loader is not None:
            return loader

        lock = getattr(self, "_detector_lock", None)
        if lock is None:
            return self._create_yolo_loader()

        with lock:
            loader = getattr(self, "_yolo_loader", None)
            if loader is None:
                loader = self._create_yolo_loader()
            return loader

    def _create_yolo_loader(self) -> YoloModelLoader:
        yolo_config = app_config.get("yolo", {})
        self._yolo_loader = YoloModelLoader(yolo_config)
        if not getattr(self, "_yolo_model_key", None):
            self._yolo_model_key = self._yolo_loader.default_model_key
        return self._yolo_loader

    @property
    def detector(self):
        return self.set_yolo_model(getattr(self, "_yolo_model_key", None) or self.yolo_loader().default_model_key)

    def release_yolo_detector(self):
        lock = getattr(self, "_detector_lock", None)
        if lock is None:
            self._release_yolo_detector_unlocked()
            return

        with lock:
            self._release_yolo_detector_unlocked()

    def _release_yolo_detector_unlocked(self):
        loader = getattr(self, "_yolo_loader", None)
        detector = getattr(self, "_detector", None)

        if loader is not None and hasattr(loader, "release"):
            loader.release()
        elif detector is not None and hasattr(detector, "release"):
            detector.release()

        self._detector = None
        self._yolo_loader = None
        self._yolo_model_key = None
        gc.collect()

    def list_yolo_models(self) -> list[str]:
        return self.yolo_loader().available_models()

    def list_yolo_targets(self, model_key: str | None = None) -> list[str]:
        return self.yolo_loader().target_names(model_key or self._yolo_model_key)

    def set_yolo_model(self, model_key: str):
        loader = self.yolo_loader()
        key = model_key or loader.default_model_key
        self._detector = loader.get_detector(key)
        self._yolo_model_key = key
        return self._detector

    def isolate_by_hsv_ranges(self, frame, ranges, invert=True, kernel_size=2):
        return isolate_by_hsv_ranges(frame, ranges, invert, kernel_size)

    def make_hsv_isolator(self, ranges):
        return partial(self.isolate_by_hsv_ranges, ranges=ranges)

    def _is_debug_overlay_enabled(self) -> bool:
        config_holders = (
            getattr(self, "executor", None),
            self,
        )
        for holder in config_holders:
            ok_config = getattr(holder, "ok_config", None)
            if ok_config is None:
                continue
            getter = getattr(ok_config, "get", None)
            if callable(getter):
                return bool(getter("use_overlay", False))

        try:
            from ok import og  # type: ignore

            app = getattr(og, "app", None)
            ok_config = getattr(app, "ok_config", None)
            getter = getattr(ok_config, "get", None)
            if callable(getter):
                return bool(getter("use_overlay", False))
        except Exception:
            pass

        return False

    def yolo_detect(
            self,
            name: str | list[str],
            frame: np.ndarray | None = None,
            box: Box | None = None,
            conf: float = 0.7,
            detections: list[Box] | None = None,
            model_key: str | None = None,
    ) -> list[Box]:
        if not name:
            raise ValueError("yolo_detect 至少需要传入一个 name")
        raw_names = [name] if isinstance(name, str) else name
        ordered_target_names = [
            str(n.value) if isinstance(n, Enum) else str(n)
            for n in raw_names
            if n is not None
        ]
        target_names = {n for n in ordered_target_names}
        if not ordered_target_names:
            raise ValueError("yolo_detect 至少需要一个有效 name")

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

        if detections is None:
            if model_key is None:
                loader = self.yolo_loader()
                first_name = ordered_target_names[0]
                resolved_model_key, detector = loader.get_detector_for_name(first_name)
                self._yolo_model_key = resolved_model_key
                self._detector = detector
            else:
                detector = self.set_yolo_model(model_key)
            if detector is None:
                self.log_error("yolo_detect: detector is not available")
                return []
            detections = detector.detect(detect_frame, threshold=conf)
        detections = detections or []

        self.log_info(f"yolo_detect: raw detections count = {len(detections)}")
        raw_results: list[Box] = []
        filtered_results: list[Box] = []

        for det in detections:
            if not all(hasattr(det, attr) for attr in ("x", "y", "width", "height")):
                continue
            det_name = getattr(det, "name", None)
            det_conf = float(getattr(det, "confidence", 0.0) or 0.0)
            self.log_info(f"Raw detection: name={det_name}, conf={det_conf:.3f}")

            new_box = Box(
                int(det.x + offset_x),
                int(det.y + offset_y),
                int(det.width),
                int(det.height),
            )

            new_box.name = det_name
            new_box.confidence = det_conf
            raw_results.append(new_box)

            if det_name in target_names:
                filtered_results.append(new_box)

        debug_overlay_enabled = self._is_debug_overlay_enabled()
        if debug_overlay_enabled:
            debug_tag = "_".join(sorted(target_names)) or "no_target"
            self.draw_boxes(f"yolo_raw_{debug_tag}", raw_results, color="yellow", debug=debug_overlay_enabled)
            self.draw_boxes(f"yolo_filtered_{debug_tag}", filtered_results, color="red", debug=debug_overlay_enabled)

        self.log_info(f"yolo_detect: filtered detections count = {len(filtered_results)}")

        return sorted(filtered_results, key=lambda item: item.confidence, reverse=True)

    def rotated_template_match_runtime(
            self,
            template_image,
            target_image: np.ndarray | None = None,
            target_center: tuple | None = None,
            template_center: tuple | None = None,
            angle_start: float = 0.0,
            angle_end: float = 360.0,
            angle_step: float = 5.0,
            roi: tuple | None = None,
            method: int = cv2.TM_CCORR_NORMED,
    ):
        """
        运行时包装：在当前帧或给定 `target_image` 上执行旋转模板匹配。

        参数:
          template_image: 模板图像（ndarray，支持 alpha）或模板文件路径（string）
          target_image: 可选，若为空则使用 `self.next_frame()`
          target_center: (x,y)，若为 None 则使用目标图中心
          template_center: (x,y)，若为 None 则使用模板中心
          angle_start/angle_end/angle_step: 角度范围与步进
          roi: (x,y,w,h) 可选裁剪目标区域以提高性能
          method: cv2.matchTemplate 方法

        返回: (best_angle, best_score)
        """
        # 加载/解析 template_image
        tpl = template_image
        if isinstance(template_image, str):
            tpl = cv2.imread(template_image, cv2.IMREAD_UNCHANGED)
            if tpl is None:
                raise ValueError(f"无法读取模板文件: {template_image}")

        # 获取目标帧
        tgt = target_image if target_image is not None else self.next_frame()
        if tgt is None:
            raise ValueError("target_image is None and self.next_frame() 返回 None")

        # 计算默认中心：如果未提供，则使用项目约定的归一化默认点 (215/2560, 222/1440)
        if target_center is None:
            # 传入归一化坐标，底层 rotated_template_match 会按目标尺寸反归一化
            target_center = (215.0 / 2560.0, 222.0 / 1440.0)

        if template_center is None:
            th, tw = tpl.shape[:2]
            template_center = (tw // 2, th // 2)

        return rotated_template_match(
            tgt,
            tpl,
            target_center,
            template_center,
            angle_start,
            angle_end,
            angle_step,
            roi=roi,
            method=method,
        )

    def get_arrow_angle(self, center: tuple | None = None, target_image: np.ndarray | None = None,
                        two_stage: bool = True, benchmark_width: int = 2560, max_cache_scales: int = 10,
                        smoothing_threshold: float = 0.35):
        """
        便捷 API：使用 ArrowAngleMatcher 检测 arrow.png 在目标图中的旋转角度（二阶段搜索）。
        支持多分辨率自动适应（缓存键 (scale_key, angle)，scale_key 四舍五入避免浮点误差）。

        参数:
          center: 目标图中的中心坐标，若为 None 则使用默认 (215/2560, 222/1440)
          target_image: 可选，若为空则使用 `self.next_frame()`
          two_stage: 是否使用二阶段搜索（粗搜 10° + 精搜 0.5°），默认 True
          benchmark_width: 基准分辨率宽度（默认 2560），用于计算模板缩放比例
          max_cache_scales: 最多缓存多少个不同 scale（默认 10，防止 LRU 膨胀）

        返回: (best_angle, best_score)
        
        说明: 
          - 缓存键 (scale_key, angle)：scale_key = round(scale, 4) 避免浮点误差
          - 不在每次 match() 中清空缓存，仅按需生成旋转结果
          - scaled_template 缓存避免同一分辨率重复 resize
          - 正常 ROI 提取返回 view 避免复制，仅越界时才 padding copy
          - LRU 限制缓存大小，防止长期运行内存膨胀
          - 粗搜阶段搜索 36 个角度，精搜阶段在最佳粗搜 ± 10° 范围以 0.5° 步长精细搜索
          - 自动处理角度环绕（如 355° ± 10° 跨越 0/360° 边界）
        """
        from src.image.rotated_template import ArrowAngleMatcher

        tgt = target_image if target_image is not None else self.next_frame()
        if tgt is None:
            raise ValueError("target_image is None and self.next_frame() 返回 None")

        if center is None:
            center = (215.0 / 2560.0, 222.0 / 1440.0)

        # 使用缓存匹配器，支持多分辨率自适应与 LRU 限制
        matcher = ArrowAngleMatcher(template_path=None, template_center=(12, 12),
                                    benchmark_width=benchmark_width, max_cache_scales=max_cache_scales)

        detected_angle, score = matcher.match(tgt, center=center, two_stage=two_stage)

        # 角度平滑：当得分低于阈值时，继承上一帧角度
        last_angle = getattr(self, "_last_arrow_angle", None)
        last_score = getattr(self, "_last_arrow_score", None)

        if score is None:
            score = 0.0

        if smoothing_threshold is not None and last_angle is not None and score < smoothing_threshold:
            # 继承上一帧角度和得分（保留历史得分以便后续逻辑参考）
            smoothed_angle = last_angle
            smoothed_score = last_score if last_score is not None else score
            # 不覆盖 _last_arrow_angle，使得低分连续时继续沿用上一帧
            return smoothed_angle, smoothed_score

        # 更新历史记录并返回检测结果
        self._last_arrow_angle = detected_angle
        self._last_arrow_score = score
        return detected_angle, score

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
            suffix = self.current_user[-4:] if len(self.current_user) >= 4 else self.current_user
            key = f"{key}({suffix})"

        if value is not None:
            value = str(value).replace("⭐", "")

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

    def move_to_target_once(self, ocr_obj, max_step=100, min_step=20, slow_radius=200, deadzone=4):
        scaled_max_step = self.scale_distance(max_step)
        scaled_min_step = min(scaled_max_step, self.scale_distance(min_step))
        scaled_slow_radius = self.scale_distance(slow_radius)
        scaled_deadzone = self.scale_distance(deadzone)
        return move_to_target_once_impl(
            self.hwnd.hwnd,
            ocr_obj,
            self.screen_center,
            max_step=scaled_max_step,
            min_step=scaled_min_step,
            slow_radius=scaled_slow_radius,
            deadzone=scaled_deadzone,
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

    def wait_click_ocr(self, x=0, y=0, to_x=1, to_y=1, width=0, height=0, box=None, name=None, match=None,
                       threshold=0, frame=None, target_height=0, time_out=0, raise_if_not_found=False,
                       recheck_time=0, after_sleep=0, post_action=None, log=False, screenshot=False,
                       settle_time=-1, lib="default", alt: bool = False):
        result = self.wait_ocr(
            x,
            y,
            width=width,
            height=height,
            to_x=to_x,
            to_y=to_y,
            box=box,
            name=name,
            match=match,
            threshold=threshold,
            frame=frame,
            target_height=target_height,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
            post_action=post_action,
            log=log,
            screenshot=screenshot,
            settle_time=settle_time,
            lib=lib,
        )
        if recheck_time > 0:
            self.sleep(1)
            result = self.ocr(
                x,
                y,
                width=width,
                height=height,
                to_x=to_x,
                to_y=to_y,
                box=box,
                name=name,
                match=match,
                threshold=threshold,
                frame=frame,
                target_height=target_height,
                log=log,
                screenshot=screenshot,
                lib=lib,
            )

        if result is not None:
            if alt:
                self.click_with_alt(result, after_sleep=after_sleep)
            else:
                self.click(result, after_sleep=after_sleep)
            return result

        self.log_info(f"wait ocr no box {x} {y} {width} {height} {to_x} {to_y} {match}")

    def screen_center(self) -> tuple[int, int]:
        return int(self.width / 2), int(self.height / 2)
