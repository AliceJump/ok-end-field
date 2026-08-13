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
    smooth_drag
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
    RESOLUTION_STABLE_SECONDS = 2.0
    RESOLUTION_STABLE_TIMEOUT = 6.0
    RESOLUTION_STABLE_INTERVAL = 0.1
    def normalize_pos(self, pos):
        """
        将归一化坐标转换为当前窗口坐标。

        Args:
            pos: (x, y)，范围 [0, 1]

        Returns:
            tuple[int, int]
        """
        x, y = pos

        width = getattr(self, "width", self.BASE_WIDTH) or self.BASE_WIDTH
        height = getattr(self, "height", self.BASE_HEIGHT) or self.BASE_HEIGHT

        return (
            round(x * width),
            round(y * height),
        )
    
    def smooth_drag(self, start, end, duration=0.12):
        smooth_drag(
            self.get_game_hwnd(),
            self.normalize_pos(start),
            self.normalize_pos(end),
            duration,
        )
        
    _resolution_warned = False

    def feature_stable(self, feature, box, duration):
        if duration <= 0:
            return True

        end_time = time.time() + duration
        while time.time() < end_time:
            if not self.find_feature(
                feature_name=feature, box=box, frame=self.next_frame()
            ):
                return False
            self.sleep(0.05)

        return True

    def click_feature(
        self,
        feature,
        boxes=None,
        time_out=5,
        after_sleep=0,
        click_after_delay=0,
        settle_time=0,
        blind_point=None,
        blind_delay=1,
        verify_disappear=True,
        verify_timeout=0.5,
        max_click_retry=3
    ):
        boxes = [None] + (boxes or [])

        start_time = time.time()
        last_blind_time = 0

        while time.time() - start_time < time_out:
            frame = self.next_frame()

            for box in boxes:
                result = self.find_feature(
                    feature_name=feature,
                    box=box,
                    frame=frame,
                )

                if result and self.feature_stable(feature, box, settle_time):
                    retry_count = 0

                    while retry_count < max_click_retry:
                        self.sleep(click_after_delay)

                        self.click(result, after_sleep=after_sleep)

                        if not verify_disappear:
                            return True

                        if self.wait_feature_disappear(
                            feature,
                            box,
                            verify_timeout,
                        ):
                            return True

                        self.log_warning(
                            f"{feature} 点击后未消失，重试 {retry_count + 1}/{max_click_retry}"
                        )

                        retry_count += 1

            if blind_point and (time.time() - last_blind_time >= blind_delay):
                self.click(
                    blind_point[0],
                    blind_point[1],
                    after_sleep=after_sleep,
                )
                last_blind_time = time.time()

        return False
    
    def wait_feature_disappear(
        self,
        feature,
        box=None,
        timeout=1,
    ):
        start = time.time()

        while time.time() - start < timeout:
            frame = self.next_frame()

            if not self.find_feature(
                feature_name=feature,
                box=box,
                frame=frame,
            ):
                return True

        return False
    

    def _wait_for_stable_resolution(self):
        """等待捕获帧尺寸稳定，避免启动阶段的中间帧触发误报。"""
        next_frame = getattr(self, "next_frame", None)
        if not callable(next_frame):
            width = getattr(self, "width", self.BASE_WIDTH) or self.BASE_WIDTH
            height = getattr(self, "height", self.BASE_HEIGHT) or self.BASE_HEIGHT
            return int(width), int(height)

        deadline = time.monotonic() + self.RESOLUTION_STABLE_TIMEOUT
        last_resolution = None
        stable_since = None
        resolution = None

        while time.monotonic() < deadline:
            try:
                frame = next_frame()
                if frame is not None and getattr(frame, "ndim", 0) >= 2:
                    resolution = (int(frame.shape[1]), int(frame.shape[0]))
            except Exception:
                # Resolution checking should not stop a task when a startup frame is unavailable.
                pass

            if resolution is None:
                width = getattr(self, "width", self.BASE_WIDTH) or self.BASE_WIDTH
                height = getattr(self, "height", self.BASE_HEIGHT) or self.BASE_HEIGHT
                resolution = (int(width), int(height))

            now = time.monotonic()
            if resolution != last_resolution:
                last_resolution = resolution
                stable_since = now
            elif stable_since is not None and now - stable_since >= self.RESOLUTION_STABLE_SECONDS:
                return resolution

            time.sleep(self.RESOLUTION_STABLE_INTERVAL)

        return resolution or (self.BASE_WIDTH, self.BASE_HEIGHT)

    def check_resolution(self):
        if RuntimeMixin._resolution_warned:
            return
        width, height = self._wait_for_stable_resolution()
        min_size = app_config.get("supported_resolution", {}).get("min_size", (1920, 1080))
        min_w, min_h = min_size
        if width < min_w or height < min_h:
            self.log_info(
                f"当前分辨率 {width}x{height} 低于要求最小值 {min_w}x{min_h}（1080P），不保证正常运行",
                notify=True
            )
        RuntimeMixin._resolution_warned = True

    def resolution_scale(self) -> float:
        """
        返回当前分辨率相对于基准分辨率的缩放系数。

        Returns:
            float: 当前窗口分辨率相对于基准分辨率的缩放比例。
        """
        width = getattr(self, "width", self.BASE_WIDTH) or self.BASE_WIDTH
        height = getattr(self, "height", self.BASE_HEIGHT) or self.BASE_HEIGHT
        return min(width / self.BASE_WIDTH, height / self.BASE_HEIGHT)

    def scale_distance(self, value: int | float, minimum: int = 1) -> int:
        """
        按当前分辨率缩放距离并保证不小于最小值。

        Args:
            value: 原始距离值。
            minimum: 缩放后的最小返回值。

        Returns:
            int: 缩放后的距离。
        """
        return max(minimum, int(round(value * self.resolution_scale())))

    def find_danger(self):
        """
        检测危险状态图标，必要时触发保底退出。

        Returns:
            bool: 检测到危险图标返回 True，否则返回 False。
        """
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
        """
        带危险态检查的点击封装。

        Args:
            x: 点击位置 X 或目标框。
            y: 点击位置 Y。
            move_back: 点击后是否恢复窗口前台。
            name: 目标名称。
            interval: 点击间隔。
            move: 是否先移动鼠标。
            down_time: 鼠标按下时长。
            after_sleep: 点击后额外等待时间。
            key: 鼠标按键名称。
            hcenter: 是否水平居中。
            vcenter: 是否垂直居中。

        Returns:
            Any: 基类 click 的返回值。

        Raises:
            Exception: 检测到危险状态时抛出。
        """
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
                     screenshot=False, mask_function=None, frame=None, limit=0, target_height=0, feature=None):
        """
        按当前分辨率映射后执行特征识别。

        Args:
            feature_name: 特征名称或名称列表。
            horizontal_variance: 水平容差。
            vertical_variance: 垂直容差。
            threshold: 匹配阈值。
            use_gray_scale: 是否使用灰度图。
            x: 区域左上角 X 坐标。
            y: 区域左上角 Y 坐标。
            to_x: 区域右下角 X 坐标。
            to_y: 区域右下角 Y 坐标。
            width: 识别区域宽度。
            height: 识别区域高度。
            box: 识别框。
            canny_lower: Canny 下限。
            canny_higher: Canny 上限。
            frame_processor: 额外帧处理器。
            template: 自定义模板。
            match_method: 模板匹配方法。
            screenshot: 是否截图后识别。
            mask_function: 掩码函数。
            frame: 输入帧。
            limit: 返回数量限制。
            target_height: 目标缩放高度。

        Returns:
            list: 特征识别结果列表。
        """
        if feature is not None and feature_name is None:
            feature_name = feature
        if isinstance(feature_name, (list, tuple)):
            feature_name = [self.get_feature_by_resolution(name) for name in feature_name]
        else:
            feature_name = self.get_feature_by_resolution(feature_name)
        result = super().find_feature(feature_name, horizontal_variance, vertical_variance, threshold, use_gray_scale, x,
                                    y, to_x, to_y, width, height, box, canny_lower, canny_higher, frame_processor,
                                    template, match_method, screenshot, mask_function, frame, limit, target_height)
        return result

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
        run_at_window_pos(self.get_game_hwnd(), super().scroll, x, y, 0.5, x, y, count)

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
        run_at_window_pos(self.get_game_hwnd(), super().scroll_relative, int(x * self.width), int(y * self.height), 0.5, x,
                          y, count)

    def get_feature_by_resolution(self, base_name: str):
        """
        根据当前分辨率选择最合适的资源后缀。

        Args:
            base_name: 资源基础名称。

        Returns:
            str: 匹配到的资源名称。

        Raises:
            AttributeError: 当没有任何可用资源时抛出。
        """
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

    def safe_back(self, match=None, feature=None,
                  box=None, time_out: float = 30, once_time_out: float = 2):
        """
        安全返回：持续点击返回直到找到指定目标（OCR文本或特征）。

        Args:
            match: 需要等待出现的 OCR 文本。
            feature: 需要等待出现的特征名。
            box: 识别范围。
            time_out: 总超时时间。
            once_time_out: 单次等待超时。

        Returns:
            bool: 是否成功找到目标。
        """
        if match is None and feature is None:
            self.log_warning("safe_back 被调用时 match 和 feature 都为空")
            return False

        start_time = self.active_time()

        while True:
            # 检查是否已超时
            if self.active_time() - start_time > time_out:
                self.log_info(f"safe_back 超时（{time_out}s），目标未出现")
                return False

            remaining = time_out - (self.active_time() - start_time)

            def target_visible():
                if match is not None and self.ocr(match=match, box=box):
                    return True
                if feature is not None and self.find_one(
                        feature,
                        vertical_variance=0.05,
                        horizontal_variance=0.05,
                        box=box,
                ):
                    return True
                return False

            if self.wait_until(
                    target_visible,
                    time_out=max(0.01, min(once_time_out, remaining)),
                    raise_if_not_found=False,
            ):
                return True

            # Only recover after the target had time to appear naturally.
            self.log_info("safe_back 观察超时，发送返回键")
            self.back()

    def yolo_loader(self) -> YoloModelLoader:
        """
        返回当前任务使用的 YOLO 加载器实例。

        Returns:
            YoloModelLoader: 当前任务的 YOLO 加载器。
        """
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

    def make_hsv_isolator(self, ranges):
        """返回一个可直接调用的 HSV 过滤函数"""
        return lambda frame, invert=True, kernel_size=2: isolate_by_hsv_ranges(
            frame, ranges, invert=invert, kernel_size=kernel_size
        )

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
        """
        对当前帧执行 YOLO 检测并返回命中的框。

        Args:
            name: 目标名称或名称列表。
            frame: 输入图像帧。
            box: 裁剪检测区域。
            conf: 置信度阈值。
            detections: 外部提供的检测结果。
            model_key: 指定的模型键。

        Returns:
            list[Box]: 命中的检测框，按置信度降序排列。

        Raises:
            ValueError: 当 name 为空或无效时抛出。
        """
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
        运行时包装：在当前帧或给定 target_image 上执行旋转模板匹配。

        参数:
          template_image: 模板图像（ndarray，支持 alpha）或模板文件路径（string）
          target_image: 可选，若为空则使用 `self.next_frame()`
          target_center: (x,y)，若为 None 则使用目标图中心
          template_center: (x,y)，若为 None 则使用模板中心
          angle_start/angle_end/angle_step: 角度范围与步进
          roi: (x,y,w,h) 可选裁剪目标区域以提高性能
          method: cv2.matchTemplate 方法

        Returns:
            tuple[float, float]: 最佳角度和对应分数。

        Raises:
            ValueError: 当模板文件无法读取或目标图像为空时抛出。
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

        Returns:
            tuple[float, float]: 检测到的角度和分数。

        Raises:
            ValueError: 当目标图像为空时抛出。
        
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
            refresh_interval: float = 1,
            box: Box | tuple | list | None = None,
    ):
        """
        等待指定区域在视觉上稳定下来。

        Args:
            method: 稳定性判断方法。
            threshold: 稳定阈值。
            stable_time: 持续稳定时长。
            max_wait: 最长等待时间。
            refresh_interval: 帧刷新间隔。
            box: 需要监测的区域。

        Returns:
            bool: 稳定后返回 True，超时返回 False。

        Raises:
            ValueError: 当 method 不支持或 box 非法时抛出。
        """
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

        start_time = self.active_time()
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
                    stable_start = self.active_time()
                elif self.active_time() - stable_start >= stable_time:
                    return True
            else:
                stable_start = None

            if self.active_time() - start_time > max_wait:
                return False

            last_frame = current_frame
            self.sleep(refresh_interval)

    def info_set(self, key, value):
        """
        写入运行时信息，并自动追加当前账号后缀。

        Args:
            key: 信息键名。
            value: 信息值。

        Returns:
            Any: 基类 info_set 的返回值。
        """
        if self.current_user:
            suffix = self.current_user[-4:] if len(self.current_user) >= 4 else self.current_user
            key = f"{key}({suffix})"

        if value is not None:
            value = str(value).replace("⭐", "")

        return super().info_set(key, value)

    def press_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        """
        按配置映射后的通用按键。

        Args:
            key: 按键名称。
            down_time: 按下时长。
            after_sleep: 释放后等待时间。
            interval: 按键间隔。

        Returns:
            Any: send_key 的返回值。
        """
        actual_key = self.key_manager.resolve_key(key, "common")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def press_industry_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        """
        按配置映射后的行业按键。

        Args:
            key: 按键名称。
            down_time: 按下时长。
            after_sleep: 释放后等待时间。
            interval: 按键间隔。

        Returns:
            Any: send_key 的返回值。
        """
        actual_key = self.key_manager.resolve_key(key, "industry")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def press_combat_key(self, key: str, down_time: float = 0.02, after_sleep: float = 0, interval: int = -1):
        """
        按配置映射后的战斗按键。

        Args:
            key: 按键名称。
            down_time: 按下时长。
            after_sleep: 释放后等待时间。
            interval: 按键间隔。

        Returns:
            Any: send_key 的返回值。
        """
        actual_key = self.key_manager.resolve_key(key, "combat")
        return self.send_key(actual_key, interval=interval, down_time=down_time, after_sleep=after_sleep)

    def move_keys(self, keys, duration, need_back=False):
        """
        在窗口中持续按下移动键。

        Args:
            keys: 按键序列。
            duration: 持续时间。
            need_back: 结束后是否恢复窗口焦点。

        Returns:
            None
        """
        send_move_keys(self, keys, duration)

    def _dodge_with_direction(self, direction_key: str, pre_hold: float = 0.004,
                              dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """
        按指定方向执行闪避。

        Args:
            direction_key: 方向键。
            pre_hold: 闪避前预按时长。
            dodge_down_time: 闪避键按下时长。
            after_sleep: 闪避后等待时间。

        Returns:
            None
        """
        move_thread = threading.Thread(target=self.move_keys, args=(direction_key, pre_hold), daemon=True)
        move_thread.start()
        self.sleep(0.005)
        self.press_key('lshift', down_time=dodge_down_time)
        move_thread.join(timeout=max(pre_hold + 0.002, 0.05))
        if after_sleep > 0:
            self.sleep(after_sleep)

    def dodge_forward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """
        向前闪避。

        Args:
            pre_hold: 闪避前预按时长。
            dodge_down_time: 闪避键按下时长。
            after_sleep: 闪避后等待时间。

        Returns:
            None
        """
        self._dodge_with_direction('w', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def dodge_backward(self, pre_hold: float = 0.004, dodge_down_time: float = 0.003, after_sleep: float = 0.005):
        """
        向后闪避。

        Args:
            pre_hold: 闪避前预按时长。
            dodge_down_time: 闪避键按下时长。
            after_sleep: 闪避后等待时间。

        Returns:
            None
        """
        self._dodge_with_direction('s', pre_hold=pre_hold, dodge_down_time=dodge_down_time, after_sleep=after_sleep)

    def move_to_target_once(self, ocr_obj, max_step=100, min_step=20, slow_radius=200, deadzone=4):
        """
        移动一次以逼近 OCR 目标。

        Args:
            ocr_obj: OCR 目标对象。
            max_step: 最大步长。
            min_step: 最小步长。
            slow_radius: 减速半径。
            deadzone: 误差死区。

        Returns:
            Any: move_to_target_once_impl 的返回值。
        """
        scaled_max_step = self.scale_distance(max_step)
        scaled_min_step = min(scaled_max_step, self.scale_distance(min_step))
        scaled_slow_radius = self.scale_distance(slow_radius)
        scaled_deadzone = self.scale_distance(deadzone)
        return move_to_target_once_impl(
            self.get_game_hwnd(),
            ocr_obj,
            self.screen_center,
            max_step=scaled_max_step,
            min_step=scaled_min_step,
            slow_radius=scaled_slow_radius,
            deadzone=scaled_deadzone,
        )

    def active_and_send_mouse_delta(self, dx=1, dy=1, activate=True, only_activate=False, delay=0.02, steps=3):
        """
        激活窗口后发送鼠标位移。

        Args:
            dx: 水平位移。
            dy: 垂直位移。
            activate: 是否激活窗口。
            only_activate: 是否只激活不移动。
            delay: 步进间隔延迟。
            steps: 步进次数。

        Returns:
            Any: send_mouse_delta 的返回值。
        """
        return send_mouse_delta(self.get_game_hwnd(), dx, dy, activate, only_activate, delay, steps)

    def click_with_alt(self, x: int | float | Box | List[Box] = -1, y: int | float = -1, move_back: bool = False,
                       name: str | None = None, interval: int = -1, move: bool = True, down_time: float = 0.01,
                       after_sleep: float = 0, key: str = 'left'):
        self.send_key_down("alt")
        self.sleep(0.5)
        self.click(x=x, y=y, move_back=move_back, name=name, interval=interval, move=move, down_time=down_time,
                   after_sleep=after_sleep, key=key)
        self.send_key_up("alt")

    def wait_click_feature(self, feature, horizontal_variance=0, vertical_variance=0, threshold=0, relative_x=0.5,
                           relative_y=0.5, time_out=0, pre_action=None, post_action=None, box=None,
                           raise_if_not_found=True, use_gray_scale=False, canny_lower=0, canny_higher=0,
                           click_after_delay=0, settle_time=-1, after_sleep=0, target_height=0,
                           alt: bool = False):
        result = self.wait_until(
            lambda: self.find_one(
                feature,
                horizontal_variance,
                vertical_variance,
                threshold,
                box=box,
                use_gray_scale=use_gray_scale,
                canny_lower=canny_lower,
                canny_higher=canny_higher,
                target_height=target_height,
            ),
            time_out=time_out,
            pre_action=pre_action,
            post_action=post_action,
            raise_if_not_found=raise_if_not_found,
            settle_time=settle_time,
        )
        if result is not None:
            if click_after_delay > 0:
                self.sleep(click_after_delay)
            if alt:
                x, y = result.relative_with_variance(relative_x, relative_y)
                self.click_with_alt(x, y, name=result.name, after_sleep=after_sleep)
            else:
                self.click_box(result, relative_x, relative_y, after_sleep=after_sleep)
            return True
        return False

    def wait_click_ocr(self, x=0, y=0, to_x=1, to_y=1, width=0, height=0, box=None, name=None, match=None,
                       threshold=0, frame=None, target_height=0, time_out=0, raise_if_not_found=False,
                       recheck_time=0, after_sleep=0, post_action=None, log=False, screenshot=False,
                       settle_time=-1, lib="default", alt: bool = False):
        """
        等待 OCR 命中后立即点击目标。

        Args:
            x: 区域左上角相对 X 坐标。
            y: 区域左上角相对 Y 坐标。
            to_x: 区域右下角相对 X 坐标。
            to_y: 区域右下角相对 Y 坐标。
            width: 识别区域宽度。
            height: 识别区域高度。
            box: 识别框。
            name: 识别区域名称。
            match: 需要匹配的文本或正则。
            threshold: OCR 置信度阈值。
            frame: 输入帧。
            target_height: 目标缩放高度。
            time_out: 等待超时时间。
            raise_if_not_found: 是否在未找到时抛异常。
            recheck_time: 复检等待时间。
            after_sleep: 点击后等待时间。
            post_action: 后置动作。
            log: 是否记录日志。
            screenshot: 是否截图。
            settle_time: 稳定等待时间。
            lib: OCR 引擎名称。
            alt: 是否使用 alt+click。

        Returns:
            Any: 命中时返回 OCR 结果，否则返回 None。
        """
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
            self.sleep(recheck_time)
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
        """
        返回当前屏幕中心点坐标。

        Returns:
            tuple[int, int]: 屏幕中心点坐标。
        """
        return int(self.width / 2), int(self.height / 2)

    def find_one(self, feature_name=None, horizontal_variance=0, vertical_variance=0, threshold=0,
             use_gray_scale=False, box=None, canny_lower=0, canny_higher=0,
             frame_processor=None, template=None, mask_function=None, frame=None,
             match_method=cv2.TM_CCOEFF_NORMED, screenshot=False, limit=1,
             target_height=0, feature=None):
        """
        按当前分辨率执行单个特征识别。

        本方法为父类 ``find_one()`` 的兼容封装，支持使用 ``feature`` 作为
        ``feature_name`` 的别名。特征名称映射逻辑由底层 ``find_feature()`` 统一处理。

        Args:
            feature_name: 特征名称。
            horizontal_variance: 水平容差。
            vertical_variance: 垂直容差。
            threshold: 匹配阈值。
            use_gray_scale: 是否使用灰度图匹配。
            box: 识别区域。
            canny_lower: Canny 边缘检测下限阈值。
            canny_higher: Canny 边缘检测上限阈值。
            frame_processor: 额外图像处理函数。
            template: 自定义模板图像。
            mask_function: 掩码处理函数。
            frame: 输入图像，为 None 时自动截图。
            match_method: OpenCV 模板匹配方法。
            screenshot: 是否强制重新截图。
            limit: 最大返回结果数量。
            target_height: 匹配前缩放到指定高度，0 表示不缩放。
            feature: ``feature_name`` 的兼容别名。

        Returns:
            Box: 匹配到的第一个结果；未匹配到时返回空 Box 或 None（取决于父类实现）。
        """
        if feature_name is None and feature is not None:
            feature_name = feature
        return super().find_one(feature_name, horizontal_variance, vertical_variance, threshold,
                                use_gray_scale, box, canny_lower, canny_higher, frame_processor,
                                template, mask_function, frame, match_method, screenshot,
                                limit, target_height)
    def send_key(
        self,
        key,
        down_time=0.1,
        interval=0,
        after_sleep=0
    ):
        """
        Sends a key event.

        发送键事件。

        Parameters
        ----------
        key:
            Key to send. 要发送的键。

        down_time:
            Down time. 按下时间。

        interval:
            Interval check. 间隔检查。

        after_sleep:
            Sleep after. 后睡眠。

        Returns
        -------
        bool:
            True if sent. 如果发送返回 True。
        """
        return super().send_key(
            key,
            down_time,
            interval,
            after_sleep
        )
