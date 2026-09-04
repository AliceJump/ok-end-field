"""蓝色血条检测与头像定位模块。

检测游戏战斗界面左下角的蓝色血条，根据血条位置定位角色头像。
支持任意分辨率，使用归一化坐标（基于1920x1080标准）。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# 标准分辨率 1920x1080 下的参数
STANDARD_WIDTH = 1920
STANDARD_HEIGHT = 1080

# 血条检测区域（归一化坐标）
BLOOD_BAR_ROI = {
    "x1": 0,
    "y1": 980 / STANDARD_HEIGHT,  # 约0.907
    "x2": 500 / STANDARD_WIDTH,  # 约0.260
    "y2": 1010 / STANDARD_HEIGHT,  # 约0.935
}

# 血条位置范围（归一化Y坐标）
BLOOD_BAR_Y_MIN = 988 / STANDARD_HEIGHT  # 约0.915
BLOOD_BAR_Y_MAX = 1005 / STANDARD_HEIGHT  # 约0.931

# 头像参数（归一化）
PORTRAIT_OFFSET_X = -17 / STANDARD_WIDTH  # 相对血条中心左移
PORTRAIT_OFFSET_Y = -46 / STANDARD_HEIGHT  # 相对血条中心上移
PORTRAIT_WIDTH = 54 / STANDARD_WIDTH  # 头像宽度
PORTRAIT_HEIGHT = 46 / STANDARD_HEIGHT  # 头像高度

# 血条满血参数（1080p 下的像素值）
FULL_HP_WIDTH_PX = 88  # 满血时血条宽度（像素）


@dataclass(frozen=True)
class BloodBar:
    """检测到的血条信息。"""

    x: float
    y: float
    width: float
    height: float
    center_x: float
    center_y: float


@dataclass(frozen=True)
class Portrait:
    """根据血条定位的头像信息。"""

    name: str
    x: int
    y: int
    width: int
    height: int
    bar: BloodBar


def detect_blue_bars(
    screenshot: np.ndarray,
    roi: dict[str, float] | None = None,
    *,
    y_min: float = BLOOD_BAR_Y_MIN,
    y_max: float = BLOOD_BAR_Y_MAX,
) -> list[BloodBar]:
    """检测蓝色血条位置。

    Args:
        screenshot: BGR 格式的游戏截图
        roi: 血条搜索区域（归一化坐标），None 则搜索整张图
        y_min: 血条最小归一化 Y 坐标
        y_max: 血条最大归一化 Y 坐标

    Returns:
        按 X 坐标排序的血条列表
    """
    if screenshot is None or screenshot.size == 0:
        return []

    height, width = screenshot.shape[:2]

    if roi is not None:
        x1 = int(roi["x1"] * width)
        y1 = int(roi["y1"] * height)
        x2 = int(roi["x2"] * width)
        y2 = int(roi["y2"] * height)
        search_area = screenshot[y1:y2, x1:x2]
        offset_x, offset_y = x1, y1
    else:
        search_area = screenshot
        offset_x, offset_y = 0, 0

    hsv = cv2.cvtColor(search_area, cv2.COLOR_BGR2HSV)

    # 根据 hsl(196, 98%, 54%) 调整 HSV 范围
    lower_blue = np.array([90, 180, 100])
    upper_blue = np.array([110, 255, 255])

    mask = cv2.inRange(hsv, lower_blue, upper_blue)

    # 形态学操作
    kernel = np.ones((2, 5), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)

    # 找轮廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bars: list[BloodBar] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        # 转换为归一化坐标
        norm_x = (x + offset_x) / width
        norm_y = (y + offset_y) / height
        norm_w = w / width
        norm_h = h / height

        # 血条应该是细长的水平条，y坐标在范围内
        # 50px 和 12px 是基于标准分辨率 1920x1080 的阈值
        min_width_ratio = 50 / STANDARD_WIDTH
        max_height_ratio = 12 / STANDARD_HEIGHT
        if norm_w > min_width_ratio and norm_h < max_height_ratio and norm_w > norm_h * 3 and y_min <= norm_y <= y_max:
            center_x = (x + offset_x + w // 2) / width
            center_y = (y + offset_y + h // 2) / height

            bars.append(
                BloodBar(
                    x=norm_x,
                    y=norm_y,
                    width=norm_w,
                    height=norm_h,
                    center_x=center_x,
                    center_y=center_y,
                )
            )

    return sorted(bars, key=lambda b: b.x)


def locate_portraits(
    bars: list[BloodBar],
) -> list[Portrait]:
    """根据血条位置定位头像。

    Args:
        bars: detect_blue_bars 返回的血条列表

    Returns:
        头像列表
    """
    portraits: list[Portrait] = []
    for i, bar in enumerate(bars):
        portraits.append(
            Portrait(
                name=f"P{i + 1}",
                x=0,  # 延迟计算，需要实际分辨率
                y=0,
                width=0,
                height=0,
                bar=bar,
            )
        )
    return portraits


def get_portrait_bbox(
    bar: BloodBar,
    screen_width: int,
    screen_height: int,
    *,
    offset_x: float = PORTRAIT_OFFSET_X,
    offset_y: float = PORTRAIT_OFFSET_Y,
    portrait_width: float = PORTRAIT_WIDTH,
    portrait_height: float = PORTRAIT_HEIGHT,
) -> tuple[int, int, int, int]:
    """获取头像的像素坐标 bbox。

    Args:
        bar: 血条信息
        screen_width: 屏幕宽度
        screen_height: 屏幕高度
        offset_x: 头像中心相对血条中心的 X 偏移（归一化）
        offset_y: 头像中心相对血条中心的 Y 偏移（归一化）
        portrait_width: 头像宽度（归一化）
        portrait_height: 头像高度（归一化）

    Returns:
        (x1, y1, x2, y2) 像素坐标
    """
    center_x = int((bar.center_x + offset_x) * screen_width)
    center_y = int((bar.center_y + offset_y) * screen_height)
    w = int(portrait_width * screen_width)
    h = int(portrait_height * screen_height)
    return (
        max(0, center_x - w // 2),
        max(0, center_y - h // 2),
        max(0, center_x - w // 2) + w,
        max(0, center_y - h // 2) + h,
    )


def validate_bars(bars: list[BloodBar], *, tolerance: float = 0.005) -> bool:
    """验证血条位置是否符合等差数列。

    Args:
        bars: 血条列表
        tolerance: 允许的公差

    Returns:
        True 如果符合等差数列
    """
    if len(bars) < 2:
        return False
    x_coords = [b.center_x for b in bars]
    diffs = [x_coords[i + 1] - x_coords[i] for i in range(len(x_coords) - 1)]
    return all(abs(d - diffs[0]) < tolerance for d in diffs)
