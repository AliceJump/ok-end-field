# -*- coding: utf-8 -*-
"""MinimapOdometry 单元测试。

覆盖：
- annulus_mask 圆环几何（内圈 0 / 环带 1 / 外圈 0、羽化）。
- phase_shift 的位移符号约定（内容 A->B 位移 = (dx, dy)）。
- MinimapOdometry 采样积分（玩家位移 = -(内容位移)）与守卫（响应低/超位移/时间窗）。
- body_axes / decompose_body 前向-右向正交与符号约定。

这些测试不需要游戏窗口，仅依赖 numpy/opencv。
"""
import cv2
import numpy as np
import unittest

from src.tasks.mixin.minimap_odometry import (
    DEFAULT_CENTER_RATIO,
    DEFAULT_R_INNER_RATIO,
    DEFAULT_R_OUTER_RATIO,
    MinimapOdometry,
    annulus_mask,
    body_axes_from_heading,
    decompose_body,
    phase_shift,
    region_geometry,
    wrap_deg,
)


def _texture(width=200, height=200, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.normal(128, 40, (height, width)).astype(np.float32)
    # 加一点低频结构，提升相关稳定度
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    base += 30 * np.sin(xx / 12.0) + 25 * np.cos(yy / 9.0)
    return base


def _bgr(array):
    g = np.clip(array, 0, 255).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _shifted(base, tx, ty):
    h, w = base.shape
    m = np.float32([[1, 0, tx], [0, 1, ty]])
    return cv2.warpAffine(base, m, (w, h), flags=cv2.INTER_LINEAR)


class _FakeTask:
    """提供 MinimapOdometry 所需的鸭子类型接口。"""

    def __init__(self, width=200, height=200, frames=None, arrow_angle=0.0):
        self.width = width
        self.height = height
        self._frames = frames or []
        self._idx = 0
        self._t = 0.0
        self._arrow_angle = arrow_angle
        self.logs = []

    def next_frame(self):
        if not self._frames:
            return None
        f = self._frames[min(self._idx, len(self._frames) - 1)]
        self._idx += 1
        return f

    def active_time(self):
        return self._t

    def get_arrow_angle(self, smoothing_threshold=None):
        return self._arrow_angle, 1.0

    def log_info(self, msg):
        self.logs.append(("info", msg))

    def log_warning(self, msg):
        self.logs.append(("warn", msg))

    def log_error(self, msg):
        self.logs.append(("error", msg))


class TestAnnulusMask(unittest.TestCase):
    def test_shape_and_region(self):
        h, w = 200, 200
        center = (100.0, 100.0)
        mask = annulus_mask(h, w, center, r_inner=20, r_outer=80, feather=0)
        self.assertEqual(mask.shape, (h, w))
        # 中心（箭头）
        self.assertEqual(mask[100, 100], 0.0)
        # 环带内部
        self.assertEqual(mask[100, 100 + 50], 1.0)  # 右方向 r=50
        # 外圈外
        self.assertEqual(mask[100, 100 + 95], 0.0)
        # 值为 0/1
        self.assertTrue(set(np.unique(mask)).issubset({0.0, 1.0}))

    def test_feather_soft_edge(self):
        h, w = 200, 200
        center = (100.0, 100.0)
        mask = annulus_mask(h, w, center, r_inner=20, r_outer=80, feather=3)
        # 内圈内侧仍为 0，环带内为 1
        self.assertEqual(mask[100, 100], 0.0)
        self.assertGreater(mask[100, 100 + 50], 0.999)
        # 羽化带存在渐变值
        border_vals = mask[100, 100 + 82]  # 接近外缘
        self.assertGreaterEqual(border_vals, 0.0)
        self.assertLessEqual(border_vals, 1.0)


class TestPhaseShift(unittest.TestCase):
    def test_sign_convention(self):
        h, w = 200, 200
        center = (100.0, 100.0)
        mask = annulus_mask(h, w, center, r_inner=20, r_outer=80, feather=2)
        base = _texture(w, h)
        # 内容从 A 到 B 向右下移动 (6, -4)：A 中 (x,y) -> B 中 (x+6, y-4)
        tx, ty = 6.0, -4.0
        a = _bgr(base)
        b = _bgr(_shifted(base, tx, ty))
        dx, dy, response = phase_shift(a, b, mask)
        self.assertAlmostEqual(dx, tx, delta=0.6)
        self.assertAlmostEqual(dy, ty, delta=0.6)
        self.assertGreater(response, 0.05)


class TestIntegration(unittest.TestCase):
    def _make(self, frames, **kwargs):
        task = _FakeTask(200, 200, frames=frames)
        od = MinimapOdometry(
            task,
            center_ratio=(0.5, 0.5),
            r_outer_ratio=0.4,
            r_inner_ratio=0.1,
            feather=2,
            **kwargs,
        )
        return task, od

    def test_accumulates_player_opposite_content(self):
        base = _texture(200, 200)
        # 内容向右下移 (4,3)，再向右下移 (2,-1)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 4, 3))
        f2 = _bgr(_shifted(base, 6, 2))
        task, od = self._make([f0, f1, f2])

        r0 = od.sample(frame=f0, now=0.0)
        self.assertTrue(r0["ok"])
        self.assertFalse(r0["sampled"])  # anchor_init

        task._t = 0.3
        r1 = od.sample(frame=f1)
        self.assertTrue(r1["ok"], r1)
        self.assertAlmostEqual(r1["dmap_px"][0], -4.0, delta=0.8)
        self.assertAlmostEqual(r1["dmap_px"][1], -3.0, delta=0.8)

        task._t = 0.6
        r2 = od.sample(frame=f2)
        self.assertTrue(r2["ok"], r2)
        # 第二拍相对新锚帧 f1：内容又移了 (2, -1) → 玩家 ( -2, +1)
        self.assertAlmostEqual(r2["dmap_px"][0], -2.0, delta=0.8)
        self.assertAlmostEqual(r2["dmap_px"][1], 1.0, delta=0.8)

        px, py = od.position_px()
        self.assertAlmostEqual(px, -6.0, delta=0.7)
        self.assertAlmostEqual(py, -2.0, delta=0.7)

    def test_reanchor_on_large_shift(self):
        base = _texture(200, 200)
        f0 = _bgr(base)
        # 内容整体右移 50px > max_shift(=0.35*0.4*200=28)，但仍在窗口内所以可测出
        f1 = _bgr(_shifted(base, 50, 0))
        task, od = self._make([f0, f1], max_shift_ratio=0.35)
        r0 = od.sample(frame=f0, now=0.0)
        task._t = 0.3
        r1 = od.sample(frame=f1)
        self.assertFalse(r1["ok"])
        self.assertEqual(r1["reason"], "exceed_max_shift")
        self.assertTrue(r1["reanchored"])
        self.assertEqual(od.position_px(), (0.0, 0.0))  # 未积分

    def test_reanchor_on_low_response(self):
        base = _texture(200, 200)
        f0 = _bgr(base)
        blank = np.zeros((200, 200, 3), np.uint8)
        task, od = self._make([f0, blank], response_low=0.2)
        r0 = od.sample(frame=f0, now=0.0)
        task._t = 0.3
        r1 = od.sample(frame=blank)
        self.assertFalse(r1["ok"])
        self.assertIn(r1["reason"], ("low_response",))

    def test_too_soon_skips(self):
        base = _texture(200, 200)
        task, od = self._make([_bgr(base), _bgr(_shifted(base, 3, 0))])
        r0 = od.sample(frame=None, now=0.0)
        self.assertFalse(r0["sampled"])
        # 时间差小于 sample_min_dt（默认 0.15）
        task._t = 0.05
        r1 = od.sample(frame=None)
        self.assertTrue(r1["ok"])
        self.assertFalse(r1["sampled"])
        self.assertEqual(r1["reason"], "too_soon")


class TestDecompose(unittest.TestCase):
    def test_axes_orthonormal(self):
        for deg in (0, 90, 180, 270, 45):
            fwd, right = body_axes_from_heading(float(deg), "north_up")
            self.assertAlmostEqual(fwd[0] ** 2 + fwd[1] ** 2, 1.0, delta=1e-6)
            self.assertAlmostEqual(right[0] ** 2 + right[1] ** 2, 1.0, delta=1e-6)
            self.assertAlmostEqual(fwd[0] * right[0] + fwd[1] * right[1], 0.0, delta=1e-6)

    def test_north_up_convention(self):
        # 0° = 北（图像 y 向上 → 单位向量 (0, -1)）
        fwd, right = body_axes_from_heading(0.0, "north_up")
        self.assertAlmostEqual(fwd[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(fwd[1], -1.0, delta=1e-6)
        # 90° = 东（图像 x 向右 → (1, 0)）
        fwd, _ = body_axes_from_heading(90.0, "north_up")
        self.assertAlmostEqual(fwd[0], 1.0, delta=1e-6)

    def test_decompose_heading(self):
        # 朝北走 10 米（地图系：东 x、南 y），北= (0,-1)
        fwd, _ = decompose_body((0.0, -10.0), heading_deg=0.0, scale_m_per_px=1.0, convention="north_up")
        self.assertAlmostEqual(fwd, 10.0, delta=1e-6)
        # 朝东走 10 米
        fwd, _ = decompose_body((10.0, 0.0), heading_deg=90.0, scale_m_per_px=1.0, convention="north_up")
        self.assertAlmostEqual(fwd, 10.0, delta=1e-6)

    def test_wrap_deg(self):
        self.assertAlmostEqual(wrap_deg(-30), 330.0, delta=1e-6)
        self.assertAlmostEqual(wrap_deg(390), 30.0, delta=1e-6)


class TestRegionGeometry(unittest.TestCase):
    """region_geometry 是"里程计掩膜"与"区域检查任务"共用的单一事实来源。"""

    def test_default_values_at_2560x1440(self):
        cx, cy, r_in, r_out = region_geometry(2560, 1440)
        self.assertAlmostEqual(cx, 0.084 * 2560, delta=1e-6)     # 215.04
        self.assertAlmostEqual(cy, 0.154 * 1440, delta=1e-6)     # 221.76
        self.assertAlmostEqual(r_in, 0.014 * 2560, delta=1e-6)   # 35.84
        self.assertAlmostEqual(r_out, 0.044 * 2560, delta=1e-6)  # 112.64

    def test_radii_follow_width_only(self):
        """半径只按宽换算（圆不为椭圆的假设）：换高度半径不变，圆心 y 变。"""
        _, cy1, r_in1, r_out1 = region_geometry(2560, 1440)
        _, cy2, r_in2, r_out2 = region_geometry(2560, 1080)
        self.assertAlmostEqual(r_in1, r_in2, delta=1e-6)
        self.assertAlmostEqual(r_out1, r_out2, delta=1e-6)
        self.assertNotAlmostEqual(cy1, cy2, delta=1.0)

    def test_custom_ratios(self):
        cx, cy, r_in, r_out = region_geometry(
            1000, 500, (0.1, 0.2), r_outer_ratio=0.05, r_inner_ratio=0.01)
        self.assertAlmostEqual(cx, 100.0, delta=1e-6)
        self.assertAlmostEqual(cy, 100.0, delta=1e-6)
        self.assertAlmostEqual(r_in, 10.0, delta=1e-6)
        self.assertAlmostEqual(r_out, 50.0, delta=1e-6)

    def test_matches_odometry_mask(self):
        """不变量：里程计建的掩膜 == 用 region_geometry 参数建的掩膜。

        「小地图区域检查」任务正是用 region_geometry 画圈的，这条不变量保证
        它圈出来的区域与实际参与相位相关的像素完全一致。
        """
        task = _FakeTask(width=200, height=200)
        od = MinimapOdometry(task)
        cx, cy, r_in, r_out = region_geometry(200, 200)
        ref = annulus_mask(200, 200, (cx, cy), r_in, r_out, feather=od._feather)
        self.assertTrue(np.array_equal(od._mask(), ref))


if __name__ == "__main__":
    unittest.main()
