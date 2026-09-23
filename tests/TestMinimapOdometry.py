# -*- coding: utf-8 -*-
"""MinimapOdometry 单元测试。

覆盖：
- annulus_mask 圆环几何（内圈 0 / 环带 1 / 外圈 0、羽化）。
- phase_shift 的位移符号约定（内容 A->B 位移 = (dx, dy)）。
- MinimapOdometry 采样积分（玩家位移 = -(内容位移)）与守卫（响应低/超位移/时间窗）。
- body_axes / decompose_body 前向-右向正交、右手侧与罗盘方位角约定。

这些测试不需要游戏窗口，仅依赖 numpy/opencv。
"""
import cv2
import math
import numpy as np
import unittest

from src.tasks.mixin.minimap_odometry import (
    DEFAULT_CENTER_RATIO,
    DEFAULT_R_INNER_RATIO,
    DEFAULT_R_OUTER_RATIO,
    MinimapOdometry,
    annulus_mask,
    angle_delta,
    arrow_angle_to_bearing,
    bearing_to_arrow_angle,
    body_axes_from_heading,
    decompose_body,
    minimap_crop_box,
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
    """验证小地图环带 mask 的几何与羽化。"""

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
    """验证相位相关的位移方向和响应。"""

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
    """验证采样、积分和重锚守卫。"""

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

    def test_benign_reanchor_flag_only_for_too_long_dt(self):
        """只有 ``too_long_dt`` 算良性换锚：相关可信、只是基线到了上限。

        上层用它决定要不要撤销 ``position_trusted``——和"测坏了"混为一谈的话，
        位移提交阈值开启后导航会反复停车等重新校准。
        """
        base = _texture(200, 200)
        f0 = _bgr(base)

        # too_long_dt：相关良好，只是 dt 到了上限
        task, od = self._make([f0], sample_max_dt=1.0)
        od.sample(frame=f0, now=0.0)
        task._t = 2.0
        r = od.sample(frame=_bgr(_shifted(base, 3, 0)))
        self.assertEqual(r["reason"], "too_long_dt")
        self.assertTrue(r["benign_reanchor"], r)

        # exceed_max_shift：位移超出可信范围 -> 非良性
        task2, od2 = self._make([f0], max_shift_ratio=0.35)
        od2.sample(frame=f0, now=0.0)
        task2._t = 0.5
        r2 = od2.sample(frame=_bgr(_shifted(base, 50, 0)))
        self.assertEqual(r2["reason"], "exceed_max_shift")
        self.assertFalse(r2.get("benign_reanchor"), r2)

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

    def test_normal_block_gap_keeps_displacement(self):
        """正常转向/等待超过旧 1.5s 门限时，不应丢掉整段位移。"""
        base = _texture(200, 200)
        task, od = self._make([_bgr(base), _bgr(_shifted(base, 4, 0))])
        od.sample(frame=None, now=0.0)

        task._t = 2.5
        result = od.sample(frame=None)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["sampled"], result)
        self.assertEqual(result["reason"], "ok")
        self.assertAlmostEqual(od.position_px()[0], -4.0, delta=0.8)


class TestCommitShiftGate(unittest.TestCase):
    """位移提交阈值：误差按**采样次数**累积，所以要让每米提交次数与走路快慢脱钩。

    每样本误差大致是绝对量（亚像素），而每样本位移 = 速度 × 采样间隔；
    慢走 / 原地小步走时每米要积更多样本，漂移更大。阈值让"没攒够就不提交"。
    """

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

    def test_shift_gate_keeps_anchor_and_reports_pending(self):
        """位移不够阈值：不提交、**不换锚帧**，但位置要带上待提交量（保持连续）。"""
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 2, 0))
        f2 = _bgr(_shifted(base, 7, 0))
        task, od = self._make([f0, f1, f2], commit_min_shift_px=5.0)

        od.sample(frame=f0, now=0.0)

        task._t = 0.5
        r1 = od.sample(frame=f1)
        self.assertTrue(r1["ok"], r1)
        # 必须是"有效测量"：is_rest() 靠 last_sample() 算速度，报成没采样会让静止校准失效
        self.assertTrue(r1["sampled"], r1)
        self.assertEqual(r1["reason"], "shift_too_small")
        self.assertIsNotNone(od.last_sample())
        # 待提交量已计入对外位置：位置连续，不是卡在 0
        self.assertAlmostEqual(od.position_px()[0], -2.0, delta=0.8)

        task._t = 1.0
        r2 = od.sample(frame=f2)
        self.assertEqual(r2["reason"], "ok", r2)
        # 相对**最初锚帧**测出的 7px —— 若第二拍换了锚帧，这里只会是 5px
        self.assertAlmostEqual(r2["dmap_px"][0], -7.0, delta=0.8)
        self.assertAlmostEqual(od.position_px()[0], -7.0, delta=0.8)

    def test_gate_off_matches_old_behaviour(self):
        """阈值 0 = 关闭（旧行为）：小位移照常每拍提交。"""
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 2, 0))
        task, od = self._make([f0, f1], commit_min_shift_px=0.0)

        od.sample(frame=f0, now=0.0)
        task._t = 0.5
        r1 = od.sample(frame=f1)
        self.assertEqual(r1["reason"], "ok")
        self.assertAlmostEqual(od.position_px()[0], -2.0, delta=0.8)

    def test_slow_shuffle_never_commits(self):
        """原地小步走：位移一直攒不够阈值 → 一次都不提交，漂移不累积。

        这是本改动的核心目标。旧行为下每一拍都提交一次，每次都带一份样本误差。
        """
        base = _texture(200, 200)
        # 每拍只挪 0.2px，跑 10 拍共 1.8px，始终 < 阈值 5px
        frames = [_bgr(_shifted(base, 0.2 * i, 0)) for i in range(10)]
        task, od = self._make(frames, commit_min_shift_px=5.0, sample_max_dt=100.0)

        reasons = []
        for i, f in enumerate(frames):
            task._t = 0.5 * (i + 1)
            reasons.append(od.sample(frame=f)["reason"])

        self.assertEqual(reasons.count("ok"), 0, f"不该有提交，实际 {reasons}")
        # 但位置照样跟着走（待提交量在报告里），不是卡死
        self.assertAlmostEqual(od.position_px()[0], -1.8, delta=0.8)

    def test_long_baseline_commits_instead_of_dropping(self):
        """基线超时但相关可信：先把位移收下再换锚，不能白丢。

        阈值开启后锚帧会活得更久，这条退出路径会真的被走到；丢了它位置会停止前进，
        导航会当成"卡住"去重规划——比漂移更难查。
        """
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 3, 0))
        task, od = self._make([f0, f1], commit_min_shift_px=10.0, sample_max_dt=1.0)

        od.sample(frame=f0, now=0.0)
        task._t = 2.0                                   # dt >= sample_max_dt
        r1 = od.sample(frame=f1)

        self.assertFalse(r1["ok"])
        self.assertEqual(r1["reason"], "too_long_dt")
        self.assertTrue(r1["reanchored"])
        self.assertTrue(r1.get("committed"), r1)
        self.assertAlmostEqual(od.position_px()[0], -3.0, delta=0.8)

    def test_long_baseline_does_not_bypass_large_shift_guard(self):
        """长间隔仍必须先过位移硬上限，不能被误判成良性换锚并注入大位移。"""
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 50, 0))
        task, od = self._make(
            [f0, f1],
            commit_min_shift_px=2.0,
            sample_max_dt=1.0,
        )

        od.sample(frame=f0, now=0.0)
        task._t = 2.0
        result = od.sample(frame=f1)

        self.assertEqual(result["reason"], "exceed_max_shift", result)
        self.assertFalse(result["committed"])
        self.assertFalse(result["benign_reanchor"])
        self.assertEqual(od.position_px(), (0.0, 0.0))

    def test_long_baseline_commits_even_with_gate_off(self):
        """守卫链先判"相关可不可信"：能走到 too_long_dt 就说明相关是好的，该收下而不是丢。

        顺带修掉的一处旧行为——原本这条路径不管相关好坏都会白丢最多一个 sample_max_dt
        的位移。阈值关闭时也一样。
        """
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 3, 0))
        task, od = self._make([f0, f1], commit_min_shift_px=0.0, sample_max_dt=1.0)

        od.sample(frame=f0, now=0.0)
        task._t = 2.0
        r1 = od.sample(frame=f1)

        self.assertEqual(r1["reason"], "too_long_dt")
        self.assertFalse(r1["ok"])
        self.assertTrue(r1.get("committed"), r1)
        self.assertAlmostEqual(od.position_px()[0], -3.0, delta=0.8)

    def test_low_response_still_discards(self):
        """相关不可信时该丢就丢：不能把噪声当位移收下。"""
        base = _texture(200, 200)
        f0 = _bgr(base)
        blank = np.zeros((200, 200, 3), np.uint8)
        task, od = self._make([f0, blank], commit_min_shift_px=2.0, response_low=0.2)

        od.sample(frame=f0, now=0.0)
        task._t = 2.0
        r1 = od.sample(frame=blank)

        self.assertEqual(r1["reason"], "low_response")
        self.assertFalse(r1.get("committed"), r1)
        self.assertEqual(od.position_px(), (0.0, 0.0))

    def test_reset_position_clears_pending(self):
        """静止重锚会清零积分：待提交量也必须清零，否则位置会立刻跳一下。"""
        base = _texture(200, 200)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 2, 0))
        task, od = self._make([f0, f1], commit_min_shift_px=5.0)

        od.sample(frame=f0, now=0.0)
        task._t = 0.5
        od.sample(frame=f1)
        self.assertNotEqual(od.position_px(), (0.0, 0.0))

        od.reset_position()
        self.assertEqual(od.position_px(), (0.0, 0.0))


class TestDecompose(unittest.TestCase):
    """验证朝向坐标约定和位移分解。"""

    def test_axes_orthonormal(self):
        for deg in (0, 90, 180, 270, 45):
            fwd, right = body_axes_from_heading(float(deg), "compass")
            self.assertAlmostEqual(fwd[0] ** 2 + fwd[1] ** 2, 1.0, delta=1e-6)
            self.assertAlmostEqual(right[0] ** 2 + right[1] ** 2, 1.0, delta=1e-6)
            self.assertAlmostEqual(fwd[0] * right[0] + fwd[1] * right[1], 0.0, delta=1e-6)

    def test_arrow_angle_to_bearing_is_mirror(self):
        """箭头角（屏幕上逆时针）与罗盘方位角（顺时针）互为镜像。"""
        self.assertAlmostEqual(arrow_angle_to_bearing(0.0), 0.0, delta=1e-6)      # 北
        self.assertAlmostEqual(arrow_angle_to_bearing(90.0), 270.0, delta=1e-6)   # 箭头 90°=西 -> 方位 270°
        self.assertAlmostEqual(arrow_angle_to_bearing(270.0), 90.0, delta=1e-6)   # 箭头 270°=东 -> 方位 90°
        for deg in (13.0, 76.5, 283.5, 359.0):
            self.assertAlmostEqual(bearing_to_arrow_angle(arrow_angle_to_bearing(deg)), deg, delta=1e-6)

    def test_compass_convention(self):
        """罗盘方位角：0°=北、90°=东、180°=南、270°=西（顺时针）。"""
        fwd, _ = body_axes_from_heading(0.0, "compass")     # 北 -> 图像 y 向上
        self.assertAlmostEqual(fwd[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(fwd[1], -1.0, delta=1e-6)
        fwd, _ = body_axes_from_heading(90.0, "compass")    # 东 -> 图像 x 向右
        self.assertAlmostEqual(fwd[0], 1.0, delta=1e-6)
        self.assertAlmostEqual(fwd[1], 0.0, delta=1e-6)
        fwd, _ = body_axes_from_heading(180.0, "compass")   # 南 -> 图像 y 向下
        self.assertAlmostEqual(fwd[1], 1.0, delta=1e-6)
        fwd, _ = body_axes_from_heading(270.0, "compass")   # 西 -> 图像 x 向左
        self.assertAlmostEqual(fwd[0], -1.0, delta=1e-6)
        # "north_up" 是历史别名，语义相同
        self.assertEqual(body_axes_from_heading(90.0, "north_up"),
                         body_axes_from_heading(90.0, "compass"))

    def test_right_is_player_right_hand(self):
        """right 必须是玩家右手侧：朝北时右手在东，朝东时右手在南。"""
        _, right = body_axes_from_heading(0.0, "compass")
        self.assertAlmostEqual(right[0], 1.0, delta=1e-6)
        self.assertAlmostEqual(right[1], 0.0, delta=1e-6)
        _, right = body_axes_from_heading(90.0, "compass")
        self.assertAlmostEqual(right[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(right[1], 1.0, delta=1e-6)

    def test_matches_arrow_angle_convention_from_real_log(self):
        """回归实机日志：箭头角换算成方位角后，应与地图系位移方向一致。

        数据取自 2026-09-10「小地图实时位置」30s 跑测（同一校准区间内朝向恒定）：
          #036->#044 箭头角 283.5°，位移 (6.5,4.4)->(56.4,-4.4)px → 方位 80.0°
          #024->#029 箭头角  71.0°，位移 (-20.6,-0.1)->(-48.9,-10.7)px → 方位 287.7°
        若不换算（把箭头角当方位角用），两处分别差 156.5° / 140.5°。
        """
        for arrow_deg, measured_bearing in ((283.5, 80.0), (71.0, 287.7)):
            fwd, _ = body_axes_from_heading(arrow_angle_to_bearing(arrow_deg), "compass")
            predicted = math.degrees(math.atan2(fwd[0], -fwd[1])) % 360.0
            diff = abs((predicted - measured_bearing + 180.0) % 360.0 - 180.0)
            self.assertLess(
                diff, 5.0,
                f"箭头角 {arrow_deg}° 预测 {predicted:.1f}°，实测 {measured_bearing}°")

    def test_decompose_heading(self):
        # 朝北走 10 米（地图系：东 x、南 y），北= (0,-1)
        fwd, _ = decompose_body((0.0, -10.0), heading_deg=0.0, scale_m_per_px=1.0, convention="compass")
        self.assertAlmostEqual(fwd, 10.0, delta=1e-6)
        # 朝东走 10 米
        fwd, _ = decompose_body((10.0, 0.0), heading_deg=90.0, scale_m_per_px=1.0, convention="compass")
        self.assertAlmostEqual(fwd, 10.0, delta=1e-6)
        # 朝东时向西走应判为后退
        fwd, _ = decompose_body((-10.0, 0.0), heading_deg=90.0, scale_m_per_px=1.0, convention="compass")
        self.assertAlmostEqual(fwd, -10.0, delta=1e-6)
        # 朝北时向东横移 = 右手侧 → strafe 为正
        _, strafe = decompose_body((10.0, 0.0), heading_deg=0.0, scale_m_per_px=1.0, convention="compass")
        self.assertAlmostEqual(strafe, 10.0, delta=1e-6)

    def test_wrap_deg(self):
        self.assertAlmostEqual(wrap_deg(-30), 330.0, delta=1e-6)
        self.assertAlmostEqual(wrap_deg(390), 30.0, delta=1e-6)

    def test_angle_delta_wraps_and_signed(self):
        """最短有向差，跨 0/360 边界取短边。"""
        self.assertAlmostEqual(angle_delta(2.0, 358.0), 4.0, delta=1e-6)
        self.assertAlmostEqual(angle_delta(358.0, 2.0), -4.0, delta=1e-6)
        self.assertAlmostEqual(angle_delta(90.0, 0.0), 90.0, delta=1e-6)
        self.assertAlmostEqual(angle_delta(0.0, 90.0), -90.0, delta=1e-6)
        self.assertAlmostEqual(abs(angle_delta(180.0, 0.0)), 180.0, delta=1e-6)


class TestReadYaw(unittest.TestCase):
    """read_yaw 对外的角度是罗盘方位角（正北 0°、正东 90°）。"""

    def test_converts_arrow_angle_to_bearing(self):
        task = _FakeTask(width=200, height=200, arrow_angle=90.0)  # 箭头 90° = 正西
        bearing, score = MinimapOdometry(task).read_yaw()
        self.assertAlmostEqual(bearing, 270.0, delta=1e-6)        # 方位 270° = 正西
        self.assertAlmostEqual(score, 1.0, delta=1e-6)

    def test_none_angle_stays_none(self):
        task = _FakeTask(width=200, height=200)
        task.get_arrow_angle = lambda smoothing_threshold=None: (None, 0.0)
        bearing, score = MinimapOdometry(task).read_yaw()
        self.assertIsNone(bearing)
        self.assertAlmostEqual(score, 0.0, delta=1e-6)


class TestCropRestIsExactlyStill(unittest.TestCase):
    """回归：默认外扩比例下，静止（两帧完全相同）必须解出**严格 0** 位移。

    裁剪等于给相位相关换了个 FFT 窗；窗太小时"相同两帧"会解出 ±0.5px 偏置，
    0.5px/0.5s ≈ 0.67 m/s 的假速度会超过融合层的静止阈值 0.2 m/s ——
    后果是永远判不了"静止"、再也不重新校准，漂移无界。所以这条必须钉死。
    """

    @staticmethod
    def _frame(w, h, noise=40.0, blur=0.0):
        rng = np.random.default_rng(0)
        base = rng.normal(128, noise, (h, w)).astype(np.float32)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        base += 30 * np.sin(xx / 12.0) + 25 * np.cos(yy / 9.0)
        if blur:
            base = cv2.GaussianBlur(base, (0, 0), blur)
        base = np.clip(base, 0, 255).astype(np.uint8)
        return cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)

    def _rest_shift(self, w, h, noise, blur):
        task = _FakeTask(w, h)
        od = MinimapOdometry(task)                 # 默认 pad
        frame = self._frame(w, h, noise, blur)
        od.sample(frame=frame, now=0.0)
        task._t = 0.5
        return tuple(round(float(v), 6) for v in od.sample(frame=frame)["dmap_px"])

    def test_rest_shift_is_exactly_zero(self):
        for w, h in ((2560, 1440), (1920, 1080), (1280, 720)):
            self.assertEqual(self._rest_shift(w, h, 40.0, 0.0), (0.0, 0.0), (w, h))
        # 低噪+模糊（更接近真实小地图纹理）
        self.assertEqual(self._rest_shift(1280, 720, 10.0, 2.0), (0.0, 0.0))


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

    def test_mask_matches_region_geometry_on_crop(self):
        """不变量：里程计在裁剪框内建的掩膜 == 整帧掩膜裁到该框的那一片。

        「小地图区域检查」任务用整帧的 region_geometry/annulus_mask 画圈，
        「里程计」把它裁到外接框后做相位相关——两者必须指向同一批像素，
        否则那个检查就没意义。
        """
        task = _FakeTask(width=200, height=200)
        od = MinimapOdometry(task)
        x0, y0, x1, y1 = od._box()
        cx, cy, r_in, r_out = region_geometry(200, 200)
        full = annulus_mask(200, 200, (cx, cy), r_in, r_out, feather=od._feather)
        self.assertTrue(np.array_equal(od._mask(), full[y0:y1, x0:x1]))
        # 外接框必须把环带整个装下：框内非零像素数与整帧一致（没有把环切掉）
        self.assertEqual(int((od._mask() > 0).sum()), int((full > 0).sum()))


class TestCropBox(unittest.TestCase):
    """相位相关前把帧裁到环带外接框——几何不能变，只是少算画面外的东西。"""

    def test_box_contains_ring_and_shrinks_area(self):
        w, h = 2560, 1440
        x0, y0, x1, y1 = minimap_crop_box(w, h)
        cx, cy, _r_in, r_out = region_geometry(w, h)
        self.assertLessEqual(x0, cx - r_out)
        self.assertGreaterEqual(x1, cx + r_out)
        self.assertLessEqual(y0, cy - r_out)
        self.assertGreaterEqual(y1, cy + r_out)
        self.assertLess((x1 - x0) * (y1 - y0), w * h * 0.1)

    def test_degenerate_size_returns_empty_box(self):
        self.assertEqual(minimap_crop_box(0, 0), (0, 0, 0, 0))


class TestCropKeepsDisplacement(unittest.TestCase):
    """裁剪后位移估计与整帧一致（真实几何下环带只占画面一小块）。"""

    def test_estimates_shift_on_cropped_ring(self):
        w, h = 1280, 720
        base = _texture(w, h)
        f0 = _bgr(base)
        f1 = _bgr(_shifted(base, 4, 3))
        task = _FakeTask(w, h, frames=[f0, f1])
        od = MinimapOdometry(task)                 # 默认几何：环带在左上角的小框内
        x0, y0, x1, y1 = od._box()
        self.assertLess((x1 - x0) * (y1 - y0), w * h * 0.1)   # 确实裁掉了一大块
        od.sample(frame=f0, now=0.0)
        task._t = 0.5
        r = od.sample(frame=f1)
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["dmap_px"][0], -4.0, delta=1.0)
        self.assertAlmostEqual(r["dmap_px"][1], -3.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
