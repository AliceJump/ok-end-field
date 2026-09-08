# -*- coding: utf-8 -*-
"""MinimapPositionFusion 单元测试。

用桩里程计验证：
- sync 设置绝对锚点并清零里程计；
- 里程计增量按 map_to_world_px 映射到世界坐标并叠加；
- try_sync 在移动时暂存、静止后应用；
- 自定义轴映射（含符号/交换）生效；
- world_from_map_px 纯函数。
"""
import unittest

from src.tasks.mixin.minimap_position_fusion import MinimapPositionFusion, world_from_map_px


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
        # 里程计移动 (3, 4) 像素，轴默认 = scale*I -> 世界 (6, 8)
        od.add(3.0, 4.0, 0.5)
        est = fusion.step()
        self.assertAlmostEqual(est["x"], 106.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 208.0, delta=1e-6)
        self.assertEqual(est["dmap_px"], (3.0, 4.0))


class TestRestGatedSync(unittest.TestCase):
    def test_try_sync_defers_when_moving(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, rest_speed_px_s=2.0)
        fusion.sync((0.0, 0.0, 0.0))
        # 移动：速度 10 px/s > 阈值 -> 不应同步
        od.add(10.0, 0.0, 1.0)
        applied = fusion.try_sync((50.0, 0.0, 60.0))
        self.assertFalse(applied)
        self.assertEqual(fusion.estimate()["x"], 0.0 + 10.0)  # 仍是旧锚点+位移

        # 静止：速度 0 < 阈值 -> step 时应用 pending，重设锚点并清零里程计
        od.add(0.0, 0.0, 1.0)
        est = fusion.step()
        self.assertAlmostEqual(est["x"], 50.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 60.0, delta=1e-6)
        self.assertEqual(est["dmap_px"], (0.0, 0.0))  # 已清零

    def test_try_sync_immediate_when_rest(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, rest_speed_px_s=2.0)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(0.0, 0.0, 1.0)  # 静止
        applied = fusion.try_sync((80.0, 0.0, 90.0))
        self.assertTrue(applied)
        self.assertEqual(fusion.estimate()["x"], 80.0)
        self.assertEqual(fusion.estimate()["z"], 90.0)


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

    def test_world_from_map_px(self):
        m = [[2.0, 0.0], [0.0, -1.0]]
        w = world_from_map_px((3.0, 4.0), m)
        self.assertAlmostEqual(w[0], 6.0, delta=1e-6)
        self.assertAlmostEqual(w[1], -4.0, delta=1e-6)


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
        self.assertAlmostEqual(st["z"], 208.0, delta=1e-6)
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


    def test_set_from_calibration(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=1.0)
        # 标定输出：轴交换+符号，比例尺 3.0
        calib = {
            "scale_m_per_px": 3.0,
            "map_to_world_px": [[0.0, -1.0], [1.0, 0.0]],
        }
        fusion.set_from_calibration(calib)
        fusion.sync((0.0, 0.0, 0.0))
        od.add(5.0, 2.0, 0.5)
        est = fusion.step()
        # world_delta = M @ (5,2) = (-2,5)   （单位已是米，因 map_to_world_px 已含比例）
        self.assertAlmostEqual(est["x"], -2.0, delta=1e-6)
        self.assertAlmostEqual(est["z"], 5.0, delta=1e-6)

    def test_set_from_calibration_empty_is_noop(self):
        od = _StubOd()
        fusion = MinimapPositionFusion(od, scale_m_per_px=1.0)
        fusion.set_from_calibration(None)  # 不应报错
        self.assertEqual(fusion.map_to_world_px, ((1.0, 0.0), (0.0, 1.0)))


if __name__ == "__main__":
    unittest.main()
