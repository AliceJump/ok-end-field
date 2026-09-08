# -*- coding: utf-8 -*-
"""小地图箭头朝向推断（几何法，参考 MaaEnd）单测。"""

import unittest

import cv2
import numpy as np

from src.image.arrow_heading import infer_arrow_heading


def make_arrow_frame(pointing_deg: float, size: int = 128, radius: int = 24):
    """生成一张白底黑箭头图：中心一个等腰三角形箭头，尖指向 pointing_deg。

    pointing_deg: 屏幕角度，0=上(北)、90=右(东)、180=下、270=左。
    """
    img = np.zeros((size, size, 3), np.uint8)
    c = size // 2
    # 箭头尖（指向方向，长度 radius*0.7），底的两个顶点在尖的反方向两侧
    rad = np.deg2rad(pointing_deg)
    tip = (c + int(round(radius * 0.7 * np.sin(rad))),
           c - int(round(radius * 0.7 * np.cos(rad))))
    # 底边两顶点：尖的反方向（-rad），偏移 ±45°
    for off in (-1, 1):
        a = rad + np.pi + off * np.deg2rad(45)
        p = (c + int(round(radius * 0.7 * np.sin(a))),
             c - int(round(radius * 0.7 * np.cos(a))))
        x = int(round((tip[0] + p[0]) / 2))
        y = int(round((tip[1] + p[1]) / 2))
    # 直接画三角形
    pts = []
    for off in (-1, 1):
        a = rad + np.pi + off * np.deg2rad(45)
        pts.append([c + radius * 0.7 * np.sin(a), c - radius * 0.7 * np.cos(a)])
    tri = np.array([[tip[0], tip[1]], pts[0], pts[1]], np.int32)
    cv2.fillPoly(img, [tri], (255, 255, 255))
    return img


class TestArrowHeading(unittest.TestCase):
    def _assert_dir(self, deg, expect, tol=10.0):
        frame = make_arrow_frame(deg)
        got = infer_arrow_heading(frame, center=(frame.shape[1] // 2, frame.shape[0] // 2),
                                  radius=24)
        self.assertIsNotNone(got, f"朝向 {deg}° 推断失败")
        diff = (got - expect + 180.0) % 360.0 - 180.0
        self.assertLess(abs(diff), tol, f"朝向 {deg}° 期望 ~{expect}°，实际 {got:.1f}°")

    def test_north_zero(self):
        self._assert_dir(0.0, 0.0)     # 上 → 北 0°

    def test_east_ninety(self):
        self._assert_dir(90.0, 90.0)   # 右 → 东 90°

    def test_south_180(self):
        self._assert_dir(180.0, 180.0)  # 下 → 南 180°

    def test_west_270(self):
        self._assert_dir(270.0, 270.0)  # 左 → 西 270°

    def test_empty_frame_returns_none(self):
        img = np.zeros((64, 64, 3), np.uint8)
        self.assertIsNone(infer_arrow_heading(img, center=(32, 32), radius=24))


if __name__ == "__main__":
    unittest.main()
