# -*- coding: utf-8 -*-
"""MinimapPositionFusion 单元测试。

用桩里程计验证：
- sync 设置绝对锚点并清零里程计；
- 里程计增量按 map_to_world_px 映射到世界坐标并叠加；
- try_sync 只在静止时校准；移动中收到的样本直接忽略（不暂存，避免把位置拽回去）；
- 自定义轴映射（含符号/交换）生效。
"""
import math
import unittest

from src.tasks.mixin.minimap_position_fusion import MinimapPositionFusion


class _StubOd:
    """模拟 MinimapOdometry 对外提供的接口。"""

    def __init__(self):
        self._pos = [0.0, 0.0]
        self._last = None

    def reset_position(self):
        self._pos = [0.0, 0.0]

    def position_px(self):
        return (float(self._pos[0]), float(self._pos[1]))

    def last_sample(self):
        return self._last

    def add(self, dx, dy, dt):
        self._pos[0] += dx
        self._pos[1] += dy
        self._last = {"sampled": True, "ok": True, "dt": dt, "dmap_px": (dx, dy)}

    def sample(self, frame=None):
        return self._last


class TestSyncAndEstimate(unittest.TestCase):
    def test_sync_sets_anchor(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0)
        est = fusion.sync((100.0, 25.0, 200.0))  # x, y(忽略), z
        self.assertIsNotNone(est)
        self.assertAlmostEqual(est["x"], 100.0)
        self.assertAlmostEqual(est["z"], 200.0)
        self.assertEqual(est["dmap_px"], (0.0, 0.0))
        # 锚点采用水平 (x, z)
        self.assertEqual(fusion.estimate()["x"], 100.0)
        self.assertEqual(fusion.estimate()["z"], 200.0)

    def test_step_maps_displacement(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0)
        fusion.sync((100.0, 0.0, 200.0))
        # 里程计移动 (3, 4) 像素，默认轴 = diag(scale,-scale)
        # （图右=东 +x、图下=南 -z）-> 世界 (6, -8)
        od.add(3.0, 4.0, 0.5)
        est = fusion.step()
        self.assertAlmostEqual(est["x"], 106.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 192.0, delta=1e-6)
        self.assertEqual(est["dmap_px"], (3.0, 4.0))

    def test_default_axis_maps_image_down_to_south(self):
        """#5 回归：默认轴把地图 y 向下（南）映射为世界 -z（北朝上）。"""
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(0.0, 5.0, 0.5)   # 图上往下（南）
        est = fusion.step()
        self.assertAlmostEqual(est["x"], 0.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], -10.0, delta=1e-6)
        # 图上往北 = -y -> 世界 +z
        od.reset_position()
        od.add(0.0, -5.0, 0.5)
        est = fusion.step()
        self.assertAlmostEqual(est["z"], 10.0, delta=1e-6)


class TestRestGatedSync(unittest.TestCase):
    """静止 = 小地图没动 **且** WS 没动，两者同时成立才用 WS 校准。"""

    def test_try_sync_ignores_samples_while_moving(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od)  # 默认阈值：0.2 m/s、0.5 m
        fusion.sync((0.0, 0.0, 0.0))
        # 移动：10 m/s > 阈值 -> 不校准
        od.add(10.0, 0.0, 1.0)
        applied = fusion.try_sync((50.0, 0.0, 60.0))
        self.assertFalse(applied)
        self.assertEqual(fusion.estimate()["x"], 0.0 + 10.0)  # 仍是旧锚点+位移
        self.assertEqual(fusion.rest_diag["reason"], "map_moving")

        # 停下后拿到一致的两条 WS -> 用最新那条校准
        od.add(0.0, 0.0, 1.0)
        self.assertTrue(fusion.try_sync((50.0, 0.0, 60.0)))
        est = fusion.estimate()
        self.assertAlmostEqual(est["x"], 50.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 60.0, delta=1e-6)
        self.assertEqual(est["dmap_px"], (0.0, 0.0))  # 已清零

    def test_stale_sample_never_pulls_position_back(self):
        """回归：移动途中收到的（陈旧）WS 样本不能在静止后被用来重锚。

        实机日志里出现过"静止时位置被拽回 1.7m"：暂存的样本是移动途中的旧坐标，
        静止后应用它就会覆盖里程计已经推进的位置。现在不做暂存。
        """
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=1.0)
        fusion.sync((0.0, 0.0, 0.0))
        # 移动 10m，而 WS 因延迟仍报旧位置 (0,0)：忽略这条，不暂存
        od.add(10.0, 0.0, 1.0)
        self.assertFalse(fusion.try_sync((0.0, 0.0, 0.0)))
        # 停下，WS 追上到 (10,0)：先来一条（与上一条不同 -> 仍算 WS 在动），再来一条才判静止
        od.add(0.0, 0.0, 1.0)
        self.assertFalse(fusion.try_sync((10.0, 0.0, 0.0)))
        self.assertTrue(fusion.try_sync((10.0, 0.0, 0.0)))
        # 位置本来就对 -> 空操作，绝不能被拽回 0
        self.assertTrue(fusion.last_sync_redundant)
        self.assertAlmostEqual(fusion.estimate()["x"], 10.0, delta=1e-6)

    def test_try_sync_immediate_when_rest(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        # 锚定在 (80, 90)：随后一条 0.4m 外的 WS 样本
        # （相邻两次 WS 位移 0.4m < rest_ws_m 0.5m，仍算"WS 没动"）
        fusion.sync((80.0, 0.0, 90.0))
        od.add(0.0, 0.0, 1.0)  # 小地图静止
        applied = fusion.try_sync((80.4, 0.0, 90.0))
        self.assertTrue(applied)
        self.assertFalse(fusion.last_sync_redundant)
        self.assertAlmostEqual(fusion.estimate()["x"], 80.4, delta=1e-6)
        self.assertAlmostEqual(fusion.estimate()["z"], 90.0, delta=1e-6)

    def test_allow_sync_false_records_ws_without_reanchor(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        fusion.sync((80.0, 0.0, 90.0))
        od.add(0.0, 0.0, 1.0)

        self.assertFalse(fusion.try_sync((80.4, 0.0, 90.0), allow_sync=False))
        self.assertAlmostEqual(fusion.estimate()["x"], 80.0, delta=1e-6)
        self.assertEqual(fusion.rest_diag["reason"], "ok")

        self.assertTrue(fusion.try_sync((80.4, 0.0, 90.0)))
        self.assertAlmostEqual(fusion.estimate()["x"], 80.4, delta=1e-6)

    def test_skips_redundant_resync(self):
        """估计已与 WS 重合 → 重复的 WS 样本不再重锚。

        静止时 WS 每秒重复推同一坐标；不跳过的话日志会被"静止校准"刷屏，
        而且上一次真正有意义的残差会被 0 残差覆盖掉。
        """
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        fusion.sync((100.0, 0.0, 200.0))
        od.add(0.0, 0.0, 1.0)                       # 小地图静止：估计 == 锚点 == WS
        self.assertTrue(fusion.try_sync((100.0, 0.0, 200.0)))
        self.assertTrue(fusion.last_sync_redundant)
        self.assertIsNone(fusion.last_sync_residual)

    def test_applies_sync_when_estimate_drifted(self):
        """里程计漂了 5m：不是空操作，必须重锚并把残差记下来。"""
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=1.0)
        fusion.sync((100.0, 0.0, 200.0))
        od.add(3.0, 4.0, 1.0)                       # 估计漂到 (103, 196)
        od.add(0.0, 0.0, 1.0)                       # 然后停下（最后一条样本速度为 0）
        self.assertTrue(fusion.try_sync((100.0, 0.0, 200.0)))
        self.assertFalse(fusion.last_sync_redundant)
        self.assertAlmostEqual(fusion.last_sync_residual["dist"], 5.0, delta=1e-6)
        self.assertAlmostEqual(fusion.estimate()["x"], 100.0, delta=1e-6)
        self.assertAlmostEqual(fusion.estimate()["z"], 200.0, delta=1e-6)

    def test_ws_moving_blocks_rest(self):
        """小地图没动、但 WS 还在动 -> 不算静止（两个条件必须同时成立）。"""
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(0.0, 0.0, 1.0)                         # 小地图没动
        applied = fusion.try_sync((30.0, 0.0, 40.0))  # WS 移动了 50m
        self.assertFalse(applied)
        self.assertEqual(fusion.rest_diag["reason"], "ws_moving")
        self.assertFalse(fusion.is_rest())

    def test_no_odom_sample_is_not_rest(self):
        """里程计没有有效样本时不判静止：无法确认"没动"，不猜。"""
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        fusion.sync((0.0, 0.0, 0.0))  # 此后 last_sample() 仍为 None
        self.assertFalse(fusion.is_rest())
        self.assertEqual(fusion.rest_diag["reason"], "no_odom_sample")

    def test_rest_speed_is_world_units(self):
        """静止阈值是世界系米/秒：慢走不再被误判为静止。

        1.72 px/s（scale 0.64 -> 约 1.1 m/s，即日志里 #003/#045 那种慢走）
        在旧的 2.0 px/s 阈值下会判"静止"，新阈值下正确判为移动。
        """
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=0.64)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(1.72, 0.0, 1.0)
        self.assertFalse(fusion.is_rest())
        self.assertEqual(fusion.rest_diag["reason"], "map_moving")
        self.assertAlmostEqual(fusion.rest_diag["map_speed_m_s"], 1.1, delta=0.01)


class TestSyncResidual(unittest.TestCase):
    """静止校准时记录"校准前小地图推算坐标 vs WS"，用来量小地图漂了多少。"""

    def test_first_sync_has_no_residual(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od)
        self.assertIsNone(fusion.last_sync_residual)
        fusion.sync((1.0, 0.0, 2.0))
        self.assertIsNone(fusion.last_sync_residual)  # 首次校准没有"之前的推算"

    def test_residual_reports_minimap_drift(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=1.0)
        fusion.sync((100.0, 0.0, 200.0))
        od.add(3.0, 4.0, 1.0)   # 小地图推算又走 (3,4)px -> 世界 (3,-4)（图下=南）
        fusion.sync((106.0, 0.0, 204.0))
        res = fusion.last_sync_residual
        self.assertAlmostEqual(res["map_x"], 103.0, delta=1e-6)
        self.assertAlmostEqual(res["map_z"], 196.0, delta=1e-6)
        self.assertAlmostEqual(res["ws_x"], 106.0, delta=1e-6)
        self.assertAlmostEqual(res["ws_z"], 204.0, delta=1e-6)
        self.assertAlmostEqual(res["dx"], -3.0, delta=1e-6)
        self.assertAlmostEqual(res["dz"], -8.0, delta=1e-6)
        self.assertAlmostEqual(res["dist"], math.hypot(3.0, 8.0), delta=1e-6)
        # 校准后位置等于 WS（基准替换），残差仍保留校准前的推算值
        self.assertAlmostEqual(fusion.estimate()["x"], 106.0, delta=1e-6)
        self.assertAlmostEqual(fusion.estimate()["z"], 204.0, delta=1e-6)


class TestAxisMapping(unittest.TestCase):
    def test_custom_matrix(self):
        od = _StubOd()
        # 轴映射：地图 x -> 世界 z，地图 y -> 世界 -x（模拟交换+符号）
        m = [[0.0, -1.0], [1.0, 0.0]]
        fusion = MinimapPositionFusion(od, map_to_world_px=m)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(5.0, 2.0, 0.5)
        est = fusion.step()
        # world_delta = M @ (5, 2) = (-2, 5)
        self.assertAlmostEqual(est["x"], -2.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 5.0, delta=1e-6)


class TestState(unittest.TestCase):
    def _arrow(self, captured):
        def fn(frame):
            captured.append(frame)
            return (45.0, 0.9)
        return fn

    def test_state_returns_heading_and_position_same_frame(self):
        od = _StubOd()
        frames = []
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0, arrow_func=self._arrow(frames))
        fusion.sync((100.0, 0.0, 200.0))
        od.add(3.0, 4.0, 0.5)
        frame = object()  # 哨兵对象：验证两处用的是同一帧
        st = fusion.state(now=1.0, frame=frame)
        self.assertEqual(len(frames), 1)
        self.assertIs(frames[0], frame)  # 箭头读取的是同一个 frame
        self.assertAlmostEqual(st["x"], 106.0, delta=1e-6)
        self.assertAlmostEqual(st["z"], 192.0, delta=1e-6)
        self.assertAlmostEqual(st["heading"], 45.0, delta=1e-6)
        self.assertAlmostEqual(st["heading_score"], 0.9, delta=1e-6)

    def test_state_without_anchor_still_has_heading(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0, arrow_func=self._arrow([]))
        st = fusion.state()
        self.assertIsNone(st["x"])
        self.assertIsNone(st["z"])
        self.assertFalse(st["anchor_set"])
        self.assertAlmostEqual(st["heading"], 45.0, delta=1e-6)

    def test_state_without_arrow_func(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=2.0)
        fusion.sync((0.0, 0.0, 0.0))
        st = fusion.state()
        self.assertIsNone(st["heading"])
        self.assertIsNone(st["heading_score"])


if __name__ == "__main__":
    unittest.main()
