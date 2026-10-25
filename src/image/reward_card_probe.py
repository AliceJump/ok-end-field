"""活动页奖励卡片的底色判定。

活动中心的任务卡有三种底色：未领取为暗红底，任务未完成是近黑底，已领取转灰底。
据此只需判断一个 OCR 文本框落在哪种底色上，就能区分「可领取」「未完成」与
「已领取」三态。

颜色范围按饱和度与亮度下限约束，排除卡面文字、奖励图标和背景特效。
"""

from __future__ import annotations

import cv2
import numpy as np

# 未领取卡片的暗红底：实测 S≈178、V≈153；已领取灰底 S≈0，未完成黑底 V≈3。
# 取 S≥120 与 V≥60 覆盖暗红，同时排除灰字、灰底与纯黑底。
_RED_LOWER = (0, 120, 60)
_RED_UPPER = (16, 255, 255)
_RED_LOWER_WRAP = (166, 120, 60)
_RED_UPPER_WRAP = (180, 255, 255)

# 以文本框为基准外扩的倍数。宽度方向保留一倍余量便于覆盖卡片内空白，
# 高度方向只留半倍，避免越界进入相邻卡片。
DEFAULT_PAD_X = 1.0
DEFAULT_PAD_Y = 0.5

# 实测：可领取卡在文本框周围红色占比 0.47~0.83，已领取与未完成卡 0.00~0.04。
DEFAULT_MIN_RED_RATIO = 0.30

# 非红卡的底色靠亮度中位数区分：实测未完成黑底 V=29~42，已领取灰底 V=70。
# 阈值取实测区间中点，两侧各留约 18 的余量。
DEFAULT_GRAY_MIN_BRIGHTNESS = 60.0

# 底色判定的三种结果：可领取、任务未完成（黑）、已领取（灰）。
CARD_CLAIMABLE = "claimable"
CARD_UNFINISHED = "unfinished"
CARD_CLAIMED = "claimed"


def red_ratio_near_box(frame, box, pad_x: float = DEFAULT_PAD_X, pad_y: float = DEFAULT_PAD_Y) -> float:
    """统计文本框四周外扩区域内的红色像素占比。

    Args:
        frame: BGR 帧。
        box: OCR 返回的文本框，需含 x/y/width/height。
        pad_x: 左右各外扩 box.width 的倍数。
        pad_y: 上下各外扩 box.height 的倍数。

    Returns:
        float: 红色像素占取样区域的比例；帧或区域无效时返回 0.0。
    """
    region = _sample_region(frame, box, pad_x, pad_y)
    if region is None:
        return 0.0
    return _red_ratio(region)


def is_claimable_card(
    frame, box, pad_x: float = DEFAULT_PAD_X, pad_y: float = DEFAULT_PAD_Y, min_ratio: float = DEFAULT_MIN_RED_RATIO
) -> bool:
    """判断 OCR 文本框是否位于未领取的红色卡片上。"""
    return red_ratio_near_box(frame, box, pad_x, pad_y) >= min_ratio


def card_state(
    frame,
    box,
    pad_x: float = DEFAULT_PAD_X,
    pad_y: float = DEFAULT_PAD_Y,
    min_ratio: float = DEFAULT_MIN_RED_RATIO,
    gray_min_brightness: float = DEFAULT_GRAY_MIN_BRIGHTNESS,
) -> str:
    """判断 OCR 文本框所在卡片的底色，返回 ``CARD_CLAIMABLE``/``CARD_UNFINISHED``/``CARD_CLAIMED``。

    红底优先：红底一定可领取。非红底再按亮度中位数分流——未完成的卡是近黑底，
    已领取的卡提亮成灰底。取样区无效时按未完成处理，避免误判成「已领取」而跳过任务。
    """
    region = _sample_region(frame, box, pad_x, pad_y)
    if region is None:
        return CARD_UNFINISHED
    if _red_ratio(region) >= min_ratio:
        return CARD_CLAIMABLE
    return CARD_CLAIMED if _median_brightness(region) >= gray_min_brightness else CARD_UNFINISHED


def _sample_region(frame, box, pad_x: float, pad_y: float):
    """按外扩倍数截取文本框周围区域；帧或区域无效时返回 None。"""
    if frame is None or getattr(frame, "size", 0) == 0 or getattr(frame, "ndim", 0) != 3:
        return None
    height, width = frame.shape[:2]
    x1 = max(0, int(box.x - box.width * pad_x))
    y1 = max(0, int(box.y - box.height * pad_y))
    x2 = min(width, int(box.x + box.width * (1 + pad_x)))
    y2 = min(height, int(box.y + box.height * (1 + pad_y)))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def _red_mask(image) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, _RED_LOWER, _RED_UPPER) | cv2.inRange(hsv, _RED_LOWER_WRAP, _RED_UPPER_WRAP)


def _red_ratio(image) -> float:
    if image.ndim != 3 or image.shape[-1] != 3 or image.size == 0:
        return 0.0
    mask = _red_mask(image)
    return float(np.count_nonzero(mask)) / float(mask.size)


def _median_brightness(image) -> float:
    """取区域 V 通道的中位数：卡面文字与图标是少数，底色占多数。"""
    if image.ndim != 3 or image.shape[-1] != 3 or image.size == 0:
        return 0.0
    return float(np.median(cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 2]))
