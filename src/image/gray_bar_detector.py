"""固定 UI 区域内横向灰色条的局部对比度检测。"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class GrayBar:
    x: int
    y: int
    width: int
    height: int
    center_x: float
    center_y: float


def detect_gray_bars(
    frame: np.ndarray,
    *,
    y_min_ratio: float = 1077 / 1440,
    y_max_ratio: float = 1115 / 1440,
    x_min_ratio: float = 0.0,
    x_max_ratio: float = 1.0,
    min_width_ratio: float = 34 / 1920,
    max_width_ratio: float = 78 / 1920,
    min_height_ratio: float = 3 / 1440,
    max_height_ratio: float = 12 / 1440,
    contrast_threshold_ratio: float = 8 / 255,
    max_gray_lightness_ratio: float = 180 / 255,
    min_vertical_ratio: float = 3 / 1440,
    min_aspect_ratio: float = 4.0,
    max_gap_ratio: float = 2 / 2560,
) -> list[GrayBar]:
    """检测归一化 UI ROI 内、相对局部背景更亮的细长横条。

    所有可配置几何量、亮度阈值和坐标范围均为 $0~1$ 的归一化比例，避免
    绑定到任一截图的绝对像素位置。默认 Y ROI 对应基准
    截图 ``2560x1440`` 中 ``y=1077`` 至 ``y=1115``，X 方向搜索整张图。
    ``min_width_ratio`` 默认值 $34/1920$，覆盖 1920×1080 下宽度为 34
    像素的短条；调用方仍可按实际 UI 比例调整。

    ``max_gray_lightness_ratio`` 是候选内部 Lab L 通道的最大平均亮度，用于排除
    亮白色 UI 长条。它只约束候选颜色，仍通过局部对比度判断横条是否存在。
    """
    if frame is None or frame.size == 0:
        return []
    if not (0 <= x_min_ratio < x_max_ratio <= 1 and 0 <= y_min_ratio < y_max_ratio <= 1):
        raise ValueError("ROI ratios must satisfy 0 <= min < max <= 1")
    normalized_values = (
        min_width_ratio,
        max_width_ratio,
        min_height_ratio,
        max_height_ratio,
        contrast_threshold_ratio,
        max_gray_lightness_ratio,
        min_vertical_ratio,
        max_gap_ratio,
    )
    if not all(0 <= value <= 1 for value in normalized_values):
        raise ValueError("gray-bar detector thresholds must be normalized ratios in [0, 1]")
    if min_width_ratio > max_width_ratio or min_height_ratio > max_height_ratio:
        raise ValueError("minimum size ratios must not exceed maximum size ratios")

    height, width = frame.shape[:2]
    y1, y2 = int(height * y_min_ratio), int(height * y_max_ratio)
    x1, x2 = int(width * x_min_ratio), int(width * x_max_ratio)
    if x2 <= x1 or y2 <= y1:
        return []

    roi = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    lightness = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)[:, :, 0] if roi.ndim == 3 else roi
    min_width_px, max_width_px = _ratio_range(min_width_ratio, max_width_ratio, width)
    min_height_px, max_height_px = _ratio_range(min_height_ratio, max_height_ratio, height, upper_padding=1)
    vertical_pixels = max(1, round(min_vertical_ratio * height))
    gap_px = max(0, round(max_gap_ratio * width))

    # 高斯模糊仅用于估计同一区域的局部背景，不依赖任何绝对灰度值。
    background = cv2.GaussianBlur(
        gray, ksize=(0, 0), sigmaX=max(1, width * 12 / 2560), sigmaY=max(1, height * 4 / 1440)
    )
    diff = gray.astype(np.float32) - background.astype(np.float32)
    mask = (diff >= contrast_threshold_ratio * 255).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 2)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))

    x_candidate = np.count_nonzero(mask, axis=0) >= vertical_pixels
    results: list[GrayBar] = []
    for start, end in _merge_runs(_runs(x_candidate), gap_px):
        bar_width = end - start + 1
        if not min_width_px <= bar_width <= max_width_px:
            continue
        ys, _ = np.nonzero(mask[:, start : end + 1] > 0)
        if not len(ys):
            continue
        local_y1, local_y2 = int(ys.min()), int(ys.max())
        bar_height = local_y2 - local_y1 + 1
        if not min_height_px <= bar_height <= max_height_px or bar_width / bar_height < min_aspect_ratio:
            continue
        # 仅评估候选内部，去掉一像素边缘以降低抗锯齿和背景混入的影响。
        inset_y1, inset_y2 = local_y1 + 1, max(local_y1 + 2, local_y2)
        inset_x1, inset_x2 = start + 1, max(start + 2, end)
        if lightness[inset_y1:inset_y2, inset_x1:inset_x2].mean() > max_gray_lightness_ratio * 255:
            continue
        absolute_x, absolute_y = x1 + start, y1 + local_y1
        results.append(
            GrayBar(
                absolute_x,
                absolute_y,
                bar_width,
                bar_height,
                absolute_x + bar_width / 2,
                absolute_y + bar_height / 2,
            )
        )
    return sorted(results, key=lambda bar: bar.x)


def draw_gray_bars(frame: np.ndarray, bars: list[GrayBar]) -> np.ndarray:
    """返回画有检测框、编号和中心点的 debug 图。"""
    output = frame.copy()
    for index, bar in enumerate(bars, start=1):
        cv2.rectangle(output, (bar.x, bar.y), (bar.x + bar.width, bar.y + bar.height), (0, 255, 0), 2)
        cv2.circle(output, (round(bar.center_x), round(bar.center_y)), 3, (0, 0, 255), -1)
        cv2.putText(output, f"bar #{index}", (bar.x, max(16, bar.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return output


def _ratio_range(minimum: float, maximum: float, size: int, upper_padding: int = 0) -> tuple[int, int]:
    scaled_minimum = max(1, round(minimum * size))
    # 高度允许形态学处理带来的一像素边界余量；宽度上下限保持调用方显式定义。
    return scaled_minimum, max(scaled_minimum, round(maximum * size) + upper_padding)


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x, valid in enumerate(values):
        if valid and start is None:
            start = x
        elif not valid and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, len(values) - 1))
    return runs


def _merge_runs(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]
