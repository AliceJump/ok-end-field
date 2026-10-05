from dataclasses import dataclass

import cv2
import numpy as np
from ok import Box
from ok.feature.Box import find_boxes_by_name, relative_box, sort_boxes

from src.image.frame_processes import isolate_by_hsv_ranges


@dataclass(frozen=True)
class TextBlockCandidate:
    x: int
    y: int
    width: int
    height: int
    image: np.ndarray


class HsvBlockOcrProcessor:
    """先用 HSV/连通块定位小文本，再直接调用 OCR recognizer。

    该对象同时保留普通 ``frame_processor`` 的可调用语义；如果当前 OCR
    后端不支持 ``det=False``，调用方可退回现有整图 OCR 路径。
    """

    def __init__(
        self,
        ranges,
        *,
        kernel_size=2,
        min_height_ratio=0.006,
        max_height_ratio=0.08,
        min_width_ratio=0.003,
        max_width_ratio=0.18,
        join_width_ratio=0.012,
        padding_ratio=0.004,
    ):
        self.ranges = ranges
        self.kernel_size = kernel_size
        self.min_height_ratio = min_height_ratio
        self.max_height_ratio = max_height_ratio
        self.min_width_ratio = min_width_ratio
        self.max_width_ratio = max_width_ratio
        self.join_width_ratio = join_width_ratio
        self.padding_ratio = padding_ratio

    def __call__(self, frame):
        return isolate_by_hsv_ranges(
            frame,
            self.ranges,
            invert=True,
            kernel_size=self.kernel_size,
        )

    def candidate_blocks(self, frame):
        """返回按屏幕坐标排序的文本候选块，候选图为白底黑字。"""
        if frame is None or frame.size == 0:
            return []

        processed = self(frame)
        gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
        foreground = cv2.bitwise_not(gray)
        height, width = foreground.shape[:2]

        # 水平膨胀只负责把同一距离文本的数字/单位连接起来；不做纵向膨胀，
        # 避免把上下两行 UI 文字粘成一个大块。
        join_width = max(3, round(height * self.join_width_ratio))
        join_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (join_width, 1))
        joined = cv2.dilate(foreground, join_kernel, iterations=1)
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_height = max(6, round(height * self.min_height_ratio))
        max_height = max(min_height, round(height * self.max_height_ratio))
        min_width = max(4, round(width * self.min_width_ratio))
        max_width = max(min_width, round(width * self.max_width_ratio))
        padding = max(2, round(height * self.padding_ratio))

        candidates = []
        for contour in contours:
            x, y, block_width, block_height = cv2.boundingRect(contour)
            if not (min_width <= block_width <= max_width and min_height <= block_height <= max_height):
                continue

            x0 = max(0, x - padding)
            y0 = max(0, y - padding)
            x1 = min(width, x + block_width + padding)
            y1 = min(height, y + block_height + padding)
            if x1 <= x0 or y1 <= y0:
                continue

            # 图标、单个噪点即使被膨胀成候选，其原始前景占比通常很低。
            foreground_crop = foreground[y0:y1, x0:x1]
            foreground_ratio = cv2.countNonZero(foreground_crop) / foreground_crop.size
            if foreground_ratio < 0.01:
                continue

            candidates.append(
                TextBlockCandidate(
                    x=x0,
                    y=y0,
                    width=x1 - x0,
                    height=y1 - y0,
                    image=processed[y0:y1, x0:x1],
                )
            )

        candidates.sort(key=lambda candidate: (candidate.y, candidate.x))
        return candidates

    def recognize(
        self,
        task,
        x=0,
        y=0,
        to_x=1,
        to_y=1,
        match=None,
        width=0,
        height=0,
        box=None,
        name=None,
        threshold=0,
        frame=None,
        target_height=0,
        use_grayscale=False,
        log=False,
        screenshot=False,
        lib="default",
        **_kwargs,
    ):
        """执行候选块 + rec-only OCR。

        返回 ``None`` 表示当前 OCR 后端不支持 rec-only，调用方应回退到
        普通 ``Task.ocr``；正常未识别到文本时返回空列表。
        """
        del name, target_height, use_grayscale, log, screenshot

        image = frame if frame is not None else task.executor.frame
        if image is None:
            return []
        if box and isinstance(box, str):
            box = task.get_box_by_name(box)

        frame_height, frame_width = image.shape[:2]
        if box is None:
            box = relative_box(frame_width, frame_height, x, y, to_x, to_y, width, height)
        roi = image[box.y : box.y + box.height, box.x : box.x + box.width]
        candidates = self.candidate_blocks(roi)
        if not candidates:
            return []

        backend = task.executor.ocr_lib(lib)
        ocr_method = getattr(backend, "ocr", None)
        if not callable(ocr_method):
            return None

        try:
            raw_result = ocr_method(
                [candidate.image for candidate in candidates],
                det=False,
                rec=True,
                cls=False,
            )
        except TypeError:
            # 非 OnnxOCR 或旧后端不接受 det/rec/cls 参数时保持原行为。
            return None

        rec_results = raw_result[0] if raw_result else []
        if len(rec_results) != len(candidates):
            # recognizer 对批量输入应一一返回；形状异常时不要猜坐标映射。
            return None

        if threshold == 0:
            threshold = task.ocr_default_threshold

        detected_boxes = []
        for candidate, rec_result in zip(candidates, rec_results, strict=True):
            if not rec_result or len(rec_result) < 2:
                continue
            text, confidence = rec_result[0], float(rec_result[1])
            if confidence < threshold:
                continue
            detected_boxes.append(
                Box(
                    box.x + candidate.x,
                    box.y + candidate.y,
                    candidate.width,
                    candidate.height,
                    confidence,
                    str(text),
                )
            )

        task.fix_texts(detected_boxes)
        match = task.fix_match_regex(match)
        if match is not None:
            detected_boxes = find_boxes_by_name(detected_boxes, match)
        return sort_boxes(detected_boxes)
