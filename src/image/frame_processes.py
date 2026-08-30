import cv2
import numpy as np


def isolate_by_hsv_ranges(frame, ranges, invert=True, kernel_size=2):
    """
    通用 HSV 颜色提取器

    :param frame: BGR 图像
    :param ranges: [
        ((h1,s1,v1), (h2,s2,v2)),
        ((...), (...)),
    ]
    :param invert: 是否反转（默认 True）
    :param kernel_size: 形态学核大小（默认 2）
    """

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    combined_mask = None

    for lower, upper in ranges:
        lower_np = np.array(lower, dtype=np.uint8)
        upper_np = np.array(upper, dtype=np.uint8)

        mask = cv2.inRange(hsv, lower_np, upper_np)

        if combined_mask is None:
            combined_mask = mask
        else:
            combined_mask = cv2.bitwise_or(combined_mask, mask)

    # ===== 形态学 =====
    if kernel_size > 0:
        kernel = np.ones((kernel_size, kernel_size), np.uint8)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

    # ===== 默认反转 =====
    if invert:
        combined_mask = cv2.bitwise_not(combined_mask)

    return cv2.cvtColor(combined_mask, cv2.COLOR_GRAY2BGR)
