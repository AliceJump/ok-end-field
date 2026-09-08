# -*- coding: utf-8 -*-
"""小地图箭头朝向推断（几何法，参考 MaaEnd MapLocator::InferYellowArrowRotation）。

原理：
- 小地图固定北方朝上，中心箭头代表角色朝向；
- 在中心极小 ROI 内用亮度掩码（白色箭头）提取轮廓，放大 16 倍亚像素平滑，
  取最小外接三角形距离质心最远的顶点作为箭头尖；
- 朝向角 = atan2(dx, -dy)：返回屏幕角，0° = 上（北），90° = 右（东）；
  任务层再按世界坐标约定映射到“正西 90°”。
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# 默认中心：全屏帧上的小地图箭头位置（归一化，与 get_arrow_angle 一致）
_DEFAULT_CENTER_NORM = (215.0 / 2560.0, 222.0 / 1440.0)


def infer_arrow_heading(
    frame: np.ndarray,
    center: tuple | None = None,
    radius: int = 24,
    bright_min: int = 220,
    upscale: int = 16,
    centre_max_dist: float = 25.0,
) -> float | None:
    """从一帧画面推断角色朝向。

    Args:
        frame: BGR/BGRA 图像。
        center: 箭头中心 (x, y) 像素坐标；None 用默认归一化中心。
        radius: 中心 ROI 半径（像素）。
        bright_min: 亮度掩码阈值（箭头主体为白色 220-255）。
        upscale: 上采样倍数（亚像素平滑）。
        centre_max_dist: 选中轮廓质心离 ROI 中心的平方距离上限，超过视为噪点。

    Returns:
        屏幕朝向角（度，0-360；0°=上/北，90°=右/东），失败返回 None。
    """
    if frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    if center is None:
        center = (w * _DEFAULT_CENTER_NORM[0], h * _DEFAULT_CENTER_NORM[1])
    cx = int(round(center[0]))
    cy = int(round(center[1]))
    r = int(radius)
    if cx - r < 0 or cy - r < 0 or cx + r > w or cy + r > h:
        return None

    roi = frame[cy - r:cy + r, cx - r:cx + r]
    if roi.shape[-1] == 4:
        roi = cv2.cvtColor(roi, cv2.COLOR_BGRA2BGR)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    _, mask = cv2.threshold(gray, int(bright_min), 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # 选距离 ROI 中心最近的轮廓（避免误拾取外围白色元素）
    best = None
    best_d = float("inf")
    for c in contours:
        m = cv2.moments(c)
        if m["m00"] > 0:
            cc = (m["m10"] / m["m00"], m["m01"] / m["m00"])
        else:
            cc = (float(c[0][0][0]), float(c[0][0][1]))
        d = (cc[0] - r) ** 2 + (cc[1] - r) ** 2
        if d < best_d:
            best_d = d
            best = c
    if best is None or best_d > centre_max_dist:
        return None

    # 上采样 16 倍做亚像素级三角形拟合
    isolated = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(isolated, [best], -1, 255, cv2.FILLED)
    hr = cv2.resize(isolated, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    _, hr = cv2.threshold(hr, 127, 255, cv2.THRESH_BINARY)
    hr_contours, _ = cv2.findContours(hr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not hr_contours:
        return None
    big = max(hr_contours, key=cv2.contourArea)

    m = cv2.moments(big)
    if m["m00"] <= 0:
        return None
    centroid = (m["m10"] / m["m00"], m["m01"] / m["m00"])

    # 最小外接三角形，取距质心最远的顶点为箭头尖
    tip = None
    retval, tri = cv2.minEnclosingTriangle(big)
    if tri is not None:
        pts = np.asarray(tri).reshape(-1, 2)
        tip = max(pts, key=lambda p: (p[0] - centroid[0]) ** 2 + (p[1] - centroid[1]) ** 2)
    if tip is None:
        # 兜底：取轮廓上距质心最远的点
        tip = max(big.reshape(-1, 2),
                  key=lambda p: (p[0] - centroid[0]) ** 2 + (p[1] - centroid[1]) ** 2)

    dx = tip[0] - centroid[0]
    dy = tip[1] - centroid[1]
    return (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
