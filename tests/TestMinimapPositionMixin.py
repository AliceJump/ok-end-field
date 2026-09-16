# -*- coding: utf-8 -*-
"""MinimapPositionMixin 单元测试。

用桩任务 + 合成小地图帧验证对外契约：
- 未锚定时仍返回朝向，坐标是 None（不猜）；
- 静止且 WS 不动时自动用 WS 锚定，坐标立即可用；
- 移动后坐标随里程计更新，且方向与坐标出自同一拍；
- 换地图时清空锚点，本拍必须返回"未锚定"而不是上一张地图的坐标；
- 没调 start 就取数时报错。

不需要游戏窗口，仅依赖 numpy/opencv。
"""
import cv2
import numpy as np
import time
import unittest

from src.tasks.mixin.minimap_position_mixin import (
    DEFAULT_MAP_TO_WORLD,
    MinimapPositionMixin,
    parse_map_to_world,
)

# 640x360：默认圆心/半径比例下环带外径约 28px，3px 的内容位移远小于 max_shift(~10px)
W, H = 640, 360


def _texture():
    rng = np.random.default_rng(0)
    base = rng.normal(128, 40, (H, W)).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    base += 30 * np.sin(xx / 12.0) + 25 * np.cos(yy / 9.0)
    return base


_BASE = _texture()


def _frame_at(tx, ty=0):
    m = np.float32([[1, 0, tx], [0, 1, ty]])
    g = np.clip(cv2.warpAffine(_BASE, m, (W, H), flags=cv2.INTER_LINEAR), 0, 255).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


class _FakeTask(MinimapPositionMixin):
    """只实现 mixin 需要的鸭子类型接口，并把位置源换成可控的 WS 桩。"""

    def __init__(self):
        self.width, self.height = W, H
        self.config = {
            "比例尺(米/像素)": 0.6703,
            "轴映射(逗号4值)": DEFAULT_MAP_TO_WORLD,
            "真值content": "",
            "真值地图账号": "",
            "WS等待稳定秒数": 0.2,
            "WS稳定最小位置数": 2,
            "朝向最低分数": 0.6,
        }
        self._t = 0.0
        self.logs = []
        self._payload = None
        self._init_minimap_position_mixin()
        # 测试里不起真实 WS 线程/端口
        self._ensure_ws_position_source = lambda cred: None

    # --- 任务侧接口 ---
    def active_time(self):
        return self._t

    def log_info(self, msg, notify=False):
        self.logs.append(str(msg))

    def log_warning(self, msg, notify=False):
        self.logs.append("WARN " + str(msg))

    def log_error(self, msg):
        self.logs.append("ERR " + str(msg))

    def get_arrow_angle(self, **kwargs):
        # 底层返回的是"屏幕上从正北逆时针"的箭头角 283.5°，
        # 换算成罗盘方位角应为 360-283.5 = 76.5°
        return 283.5, 0.9

    # --- WS 桩 ---
    def _recv_ws_position_payload(self, timeout=0.0):
        payload, self._payload = self._payload, None
        return payload

    def push_ws(self, x, z, map_id="map01"):
        self._payload = {"type": 1012, "data": {"pos": {"x": x, "y": 0.0, "z": z}, "mapId": map_id}}

    # --- 便捷 ---
    def tick(self, frame, dt=0.5, ws=None):
        self._t += dt
        if ws is not None:
            self.push_ws(*ws)
        return self.minimap_position(frame=frame)


class TestParseMapToWorld(unittest.TestCase):
    """覆盖轴映射字符串解析。"""

    def test_parses_and_rejects(self):
        self.assertEqual(parse_map_to_world("0.67,0,0,-0.67"), [[0.67, 0.0], [0.0, -0.67]])
        for bad in ("", "1,2,3", "a,b,c,d", None):
            self.assertIsNone(parse_map_to_world(bad))


class TestMinimapPositionMixin(unittest.TestCase):
    """覆盖生命周期、融合状态和对外字段契约。"""

    def setUp(self):
        self.task = _FakeTask()
        self.frame = _frame_at(0)

    def test_requires_start(self):
        with self.assertRaises(RuntimeError):
            self.task.minimap_position(frame=self.frame)

    def test_unanchored_has_heading_but_no_coords(self):
        self.task.start_minimap_position()
        st = self.task.tick(self.frame)
        self.assertIsNone(st["x"])
        self.assertIsNone(st["z"])
        self.assertFalse(st["anchor_set"])
        self.assertFalse(st["position_trusted"])
        # 位置还不知道，但方向是有的；且已换算成罗盘方位角（正东 90°）
        self.assertAlmostEqual(st["heading"], 76.5, delta=1e-6)
        # 刚建锚帧这一拍没有位移数据：odom_ok=False（别当成"在动"，也别当成"静止"）
        self.assertFalse(st["odom_ok"])
        self.assertEqual(st["odom_reason"], "anchor_init")

    def test_anchors_on_ws_when_rest(self):
        self.task.start_minimap_position()
        self.task.tick(self.frame)                              # 先建锚帧
        st = self.task.tick(self.frame, ws=(607.16, -136.13))    # 静止 + WS 不动 -> 首次校准
        self.assertTrue(st["just_synced"])
        self.assertTrue(st["anchor_set"])
        self.assertTrue(st["position_trusted"])
        self.assertAlmostEqual(st["x"], 607.16, delta=1e-6)
        self.assertAlmostEqual(st["z"], -136.13, delta=1e-6)
        self.assertAlmostEqual(st["dmap_px"][0], 0.0, delta=1e-6)

    def test_repeated_ws_while_still_skips_resync(self):
        """静止时 WS 每秒重复推同一坐标：第二次起是空操作，不再刷校准行、不再清零。"""
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        self.assertTrue(self.task.tick(self.frame, ws=(607.16, -136.13))["just_synced"])
        again = self.task.tick(self.frame, ws=(607.16, -136.13))
        self.assertFalse(again["just_synced"])
        self.assertIsNone(again["sync_residual"])
        self.assertTrue(self.task.minimap_fusion.last_sync_redundant)
        self.assertAlmostEqual(again["x"], 607.16, delta=1e-6)   # 位置不受影响

    def test_moving_updates_coords_and_heading_same_tick(self):
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        self.task.tick(self.frame, ws=(100.0, 200.0))
        self.task.tick(self.frame, ws=(100.0, 200.0))

        # 小地图内容向右移 3px -> 玩家地图系位移为负（x 减小）
        st = self.task.tick(_frame_at(3))
        self.assertLess(st["dmap_px"][0], 0.0, st["dmap_px"])
        self.assertGreater(abs(st["world_delta"][0]), 0.0)
        self.assertLess(st["x"], 100.0)                  # 世界 x 减小
        # 同一拍里方向与坐标都在；方向是罗盘方位角（与里程计无关，独立读取）
        self.assertAlmostEqual(st["heading"], 76.5, delta=1e-6)

    def test_map_change_clears_anchor_without_returning_old_coords(self):
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        self.task.tick(self.frame, ws=(100.0, 200.0))
        self.task.tick(self.frame, ws=(100.0, 200.0))
        self.assertIsNotNone(self.task.tick(self.frame)["x"])

        # 换地图那一拍必须返回"未锚定"，不能返回上一张地图的坐标
        st = self.task.tick(self.frame, ws=(0.0, 0.0, "map02"))
        self.assertIsNone(st["x"])
        self.assertIsNone(st["z"])
        self.assertFalse(st["anchor_set"])
        self.assertTrue(any("地图已切换" in msg for msg in self.task.logs))

        # 在新地图上静止 -> 重新锚定到新坐标
        st = self.task.tick(self.frame, ws=(0.0, 0.0, "map02"))
        self.assertTrue(st["anchor_set"])
        self.assertAlmostEqual(st["x"], 0.0, delta=1e-6)
        self.assertAlmostEqual(st["z"], 0.0, delta=1e-6)
        self.assertTrue(st["position_trusted"])

    def test_reanchor_requires_new_sync_before_position_is_trusted(self):
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        self.assertTrue(self.task.tick(self.frame, ws=(100.0, 200.0))["position_trusted"])

        # 人为把门限压到 0，制造一次重新锚定。
        self.task._minimap_od._sample_max_dt = 0.0
        rejected = self.task.tick(self.frame, dt=0.5)
        self.assertFalse(rejected["odom_ok"])
        self.assertFalse(rejected["position_trusted"])

        self.task._minimap_od._sample_max_dt = 5.0
        recovered = self.task.tick(self.frame, dt=0.5, ws=(100.0, 200.0))
        self.assertTrue(recovered["odom_ok"])
        self.assertTrue(recovered["position_trusted"])

    def test_start_reports_no_truth_without_content(self):
        """没配 content 时没有官方真值来源：返回 False，但仍建成里程计与融合。"""
        self.assertFalse(self.task.start_minimap_position())
        self.assertIsNotNone(self.task.minimap_odometry)
        self.assertIsNotNone(self.task.minimap_fusion)
        self.task.stop_minimap_position()

    def test_start_is_idempotent(self):
        self.task.start_minimap_position()
        odometry = self.task.minimap_odometry
        fusion = self.task.minimap_fusion

        self.task.start_minimap_position()

        self.assertIs(self.task.minimap_odometry, odometry)
        self.assertIs(self.task.minimap_fusion, fusion)

    def test_persistent_ws_ignores_idle_consumer_timeout(self):
        self.task._map_ws_consumer_idle_timeout = 0.0
        self.task._map_ws_last_consume_at = time.time() - 120.0

        self.assertFalse(self.task._map_ws_should_stop_for_idle_consumer())

    def test_start_restarts_stopped_map_ws_client(self):
        self.task.start_minimap_position()
        self.task._map_ws_auth_source = "cred"
        self.task._is_map_ws_client_enabled = lambda: False
        restarted = []
        self.task._start_map_ws_client = lambda cred: restarted.append(cred) or True

        self.task.start_minimap_position()

        self.assertEqual(restarted, ["cred"])

    def test_latest_state_tracks_freshness_and_sync_sequence(self):
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        synced = self.task.tick(self.frame, ws=(607.16, -136.13))

        latest = self.task.latest_minimap_state(now=self.task.active_time())
        self.assertEqual(latest["sync_seq"], 1)
        self.assertEqual(latest["x"], 607.16)
        self.assertEqual(latest["sample_t"], synced["sample_t"])

        self.task._t += 1.0
        self.assertIsNone(self.task.latest_minimap_state(max_age=0.5))
        self.assertEqual(
            self.task.latest_minimap_state()["sync_seq"],
            1,
        )

    def test_distance_since_sync_requests_calibration(self):
        self.task.config["航点校准最小距离(米)"] = 0.5
        self.task.start_minimap_position()
        self.task.tick(self.frame)
        self.task.tick(self.frame, ws=(100.0, 200.0))

        state = self.task.tick(_frame_at(3))

        self.assertGreater(state["distance_since_sync"], 0.5)
        self.assertTrue(state["sync_needed"])


if __name__ == "__main__":
    unittest.main()
