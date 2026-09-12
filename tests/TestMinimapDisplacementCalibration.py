"""小地图位移标定任务：拟合纯函数的单元测试。

标定任务改为被动采集后，唯一有算法的部分就是 `_fit_scale_axis`（段内相邻差分最小二乘）。
这里用合成数据验证它能还原已知的轴映射、并在三种退化情形下给出正确结论。
"""

import unittest

from src.tasks.test.MinimapDisplacementCalibration import MinimapDisplacementCalibration


def _records_from(world_points, *, to_px, seg=0, map_id="m1"):
    """把世界坐标路径按 to_px 映射成 (世界, 像素) 记录；像素是**绝对累计值**。

    与真实采集一致：里程计像素是单调累加的绝对值，配对后取相邻差分。
    """
    out = []
    for index, (x, z) in enumerate(world_points):
        px = to_px(x, z)
        out.append({
            "t": float(index),
            "x": float(x),
            "z": float(z),
            "px": [float(px[0]), float(px[1])],
            "seg": seg,
            "map_id": map_id,
        })
    return out


class TestFitScaleAxis(unittest.TestCase):
    #: 真实比例尺 米/像素
    SCALE = 0.67

    def _to_px(self, x, z):
        """世界(米) -> 地图系像素：inv(diag(k, -k))（图右=东 +x、图下=南 -z）。"""
        inv = 1.0 / self.SCALE
        return (x * inv, -z * inv)

    def _walk_two_directions(self):
        """先向东走，再向北走——两个方向都激励到。"""
        points = [(x * 1.0, 0.0) for x in range(0, 11)]
        points += [(10.0, z * 1.0) for z in range(1, 9)]
        return points

    def test_recovers_scale_and_axis_matrix(self):
        result = MinimapDisplacementCalibration._fit_scale_axis(
            _records_from(self._walk_two_directions(), to_px=self._to_px)
        )
        self.assertIsNotNone(result)
        self.assertFalse(result["ill_conditioned"])
        self.assertAlmostEqual(result["scale_m_per_px"], self.SCALE, delta=1e-5)
        matrix = result["map_to_world_px"]
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(matrix[0][0], self.SCALE, delta=1e-5)
        self.assertAlmostEqual(matrix[1][1], -self.SCALE, delta=1e-5)
        self.assertAlmostEqual(matrix[0][1], 0.0, delta=1e-5)
        self.assertAlmostEqual(matrix[1][0], 0.0, delta=1e-5)

    def test_single_direction_is_ill_conditioned(self):
        """只朝一个方向走：垂直轴没被激励，轴映射不可信，但比例尺仍可用。"""
        points = [(x * 1.0, 0.0) for x in range(0, 11)]
        result = MinimapDisplacementCalibration._fit_scale_axis(
            _records_from(points, to_px=self._to_px)
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["ill_conditioned"])
        self.assertIsNone(result["map_to_world_px"])
        self.assertAlmostEqual(result["scale_m_per_px"], self.SCALE, delta=1e-5)

    def test_cross_segment_pairs_are_skipped(self):
        """换地图时里程计像素原点会重置：跨段差分必须跳过，否则会把拟合带偏。"""
        a = _records_from([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)], to_px=self._to_px, seg=0)
        # seg=1 的像素原点被人为平移（模拟换图时里程计清零），且走了另一个方向
        b = _records_from(
            [(100.0, 100.0), (100.0, 105.0), (100.0, 110.0), (105.0, 110.0)],
            to_px=lambda x, z: (self._to_px(x, z)[0] + 999.0, self._to_px(x, z)[1] - 500.0),
            seg=1,
        )
        result = MinimapDisplacementCalibration._fit_scale_axis(a + b)

        self.assertIsNotNone(result)
        self.assertEqual(result["n"], 5)          # 段内相邻对 2 + 3；跨段那一对被跳过
        self.assertFalse(result["ill_conditioned"])
        self.assertAlmostEqual(result["scale_m_per_px"], self.SCALE, delta=1e-5)
        self.assertAlmostEqual(result["map_to_world_px"][0][0], self.SCALE, delta=1e-5)
        self.assertAlmostEqual(result["map_to_world_px"][1][1], -self.SCALE, delta=1e-5)

    def test_too_few_samples_returns_none(self):
        records = _records_from([(0.0, 0.0), (5.0, 0.0)], to_px=self._to_px)
        self.assertIsNone(MinimapDisplacementCalibration._fit_scale_axis(records))

    def test_stationary_samples_are_ignored(self):
        """站着不动时 (Δworld≈0, Δpx≈0) 的样本对不参与拟合。"""
        points = [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
        self.assertIsNone(MinimapDisplacementCalibration._fit_scale_axis(
            _records_from(points, to_px=self._to_px)
        ))


class _StubOd:
    def __init__(self, px=(0.0, 0.0)):
        self._px = (float(px[0]), float(px[1]))

    def position_px(self):
        return self._px


def _make_task(px=(0.0, 0.0)):
    """绕过 BaseEfTask.__init__ 造一个只够 _sample_once 用的对象。"""
    task = MinimapDisplacementCalibration.__new__(MinimapDisplacementCalibration)
    task._records = []
    task._map_id = None
    task._seg = 0
    task._last_recorded_ws = None
    task._travel_m = 0.0
    task._minimap_od = _StubOd(px)
    task.active_time = lambda: 0.0
    task.next_frame = lambda: None
    task.log_warning = lambda *a, **k: None
    return task


class TestSampleOnceSegmenting(unittest.TestCase):
    """重锚/换地图会清零里程计，必须切段——这是标定精度的关键。"""

    @staticmethod
    def _state(*, ws, just_synced=False, sync_checked=False, map_id="m1", odom_ok=True):
        return {
            "just_synced": just_synced,
            "sync_checked": sync_checked,
            "map_id": map_id,
            "odom_ok": odom_ok,
            "ws": ws,
        }

    def _run(self, task, states):
        queue = list(states)
        task.minimap_position = lambda **kwargs: queue.pop(0)
        for _ in states:
            task._sample_once(allow_sync=True)

    def test_reanchor_bumps_segment(self):
        task = _make_task()
        self._run(task, [
            self._state(ws=(0.0, 0.0)),
            self._state(ws=(1.0, 0.0), just_synced=True),
            self._state(ws=(2.0, 0.0)),
        ])
        self.assertEqual([r["seg"] for r in task._records], [0, 1, 1])

    def test_checked_but_not_reanchored_keeps_segment(self):
        """sync_checked 但非 just_synced = 已对齐、里程计没被清零，不能切段。"""
        task = _make_task()
        self._run(task, [
            self._state(ws=(0.0, 0.0)),
            self._state(ws=(1.0, 0.0), sync_checked=True),
            self._state(ws=(2.0, 0.0), sync_checked=True),
        ])
        self.assertEqual([r["seg"] for r in task._records], [0, 0, 0])

    def test_map_change_bumps_segment(self):
        task = _make_task()
        self._run(task, [
            self._state(ws=(0.0, 0.0), map_id="m1"),
            self._state(ws=(1.0, 0.0), map_id="m2"),
        ])
        self.assertEqual([r["seg"] for r in task._records], [0, 1])
        self.assertEqual(task._map_id, "m2")

    def test_no_record_without_valid_odometry_sample(self):
        task = _make_task()
        self._run(task, [
            self._state(ws=(0.0, 0.0), odom_ok=False),
            self._state(ws=(1.0, 0.0), odom_ok=True),
        ])
        self.assertEqual(len(task._records), 1)
        self.assertEqual(task._records[0]["x"], 1.0)

    def test_repeated_ws_sample_is_not_recorded_twice(self):
        task = _make_task()
        self._run(task, [
            self._state(ws=(0.0, 0.0)),
            self._state(ws=(0.0, 0.0)),
            self._state(ws=(3.0, 4.0)),
        ])
        self.assertEqual(len(task._records), 2)
        # 行程只按真正变化的 WS 样本累计：0 -> (3,4) 是 5 米
        self.assertAlmostEqual(task._travel_m, 5.0)


if __name__ == "__main__":
    unittest.main()
