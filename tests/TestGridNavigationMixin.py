"""Unit tests for grid planning and game-facing route execution."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.nav.grid_io import CELL_FREE, DenseGrid, GridMeta, save_grid
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import REPLAN, FollowerStep
from src.tasks.mixin.grid_navigation_mixin import (
    CONFIG_GRID_FILE,
    GridNavigationMixin,
)


class _FakeGridTask(GridNavigationMixin):
    def __init__(self, grid_path: Path):
        self.config = {
            **self.grid_navigation_default_config(),
            CONFIG_GRID_FILE: str(grid_path),
            "到达目标半径(米)": 0.5,
            "航点到达半径(米)": 0.5,
            "行走朝向容差(度)": 5.0,
            "控制周期(秒)": 0.2,
            "导航超时(秒)": 30.0,
        }
        self.logs: list[str] = []
        self.info = {}
        self.x = 0.5
        self.z = 0.5
        self.heading = 90.0
        self.t = 0.0
        self.w_down = False
        self.started = 0
        self.stopped = 0
        self.turns: list[float] = []
        self.rotations: list[int] = []
        self.rotation_w_held: list[bool] = []
        self._init_grid_navigation_mixin()

    def active_time(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))
        if self.w_down:
            self.x += 1.0

    def next_frame(self):
        return object()

    def start_minimap_position(self, *, wait_stable=True):
        self.started += 1
        self._minimap_fusion = object()
        return True

    def stop_minimap_position(self):
        self.stopped += 1
        self._minimap_fusion = None

    def minimap_position(self, frame=None, **kwargs):
        allow_sync = kwargs.get("allow_sync", True)
        return {
            "x": self.x,
            "z": self.z,
            "heading": self.heading,
            "heading_score": 0.9,
            "anchor_set": True,
            "rest": True,
            "odom_ok": True,
            "odom_reason": "ok",
            "just_synced": allow_sync,
            "sync_checked": allow_sync,
            "map_id": "test",
        }

    def minimap_rest_diag(self):
        return {"reason": "ok", "map_speed_m_s": 0.0, "ws_moved_m": 0.0}

    def send_key_down(self, key):
        self.w_down = key == "w"

    def send_key_up(self, key):
        if key == "w":
            self.w_down = False

    def press_key(self, key, **kwargs):
        pass

    def turn_to_bearing(self, target_deg, **kwargs):
        self.turns.append(float(target_deg))
        self.heading = float(target_deg)
        return {"ok": True, "heading": self.heading, "error": 0.0}

    def _send_rotation(self, dx: int):
        self.rotations.append(int(dx))
        self.rotation_w_held.append(self.w_down)
        self.heading = (self.heading + float(dx) * self.yaw_per_pixel()) % 360.0

    def info_set(self, key, value):
        self.info[key] = value

    def log_info(self, msg, notify=False):
        self.logs.append(str(msg))

    def log_warning(self, msg, notify=False):
        self.logs.append("WARN " + str(msg))

    def log_debug(self, msg):
        self.logs.append("DEBUG " + str(msg))


class TestGridNavigationMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test_4.grid.npz"
        cells = np.full((1, 6), CELL_FREE, dtype=np.uint8)
        save_grid(
            self.path,
            DenseGrid(cells, GridMeta(map_name="test", zoom="4", cell_size=1.0)),
        )
        self.task = _FakeGridTask(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_grid_path_returns_world_waypoints(self):
        result = self.task.plan_grid_path((0.5, 0.5), (4.5, 0.5), map_id="test")
        self.assertTrue(result.ok, result)
        self.assertEqual(result.waypoints[0], (0.5, 0.5))
        self.assertEqual(result.waypoints[-1], (4.5, 0.5))
        self.assertTrue(any("规划完成" in msg for msg in self.task.logs))
        self.assertTrue(any(
            "路径点：(0.500, 0.500) -> (4.500, 0.500)" in msg
            for msg in self.task.logs
        ))

    def test_navigate_holds_and_releases_w_until_goal(self):
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")
        self.assertTrue(result)
        self.assertGreaterEqual(self.task.x, 4.0)
        self.assertFalse(self.task.w_down)
        self.assertEqual(self.task.started, 1)
        self.assertEqual(self.task.stopped, 1)
        self.assertTrue(any("规划完成" in msg for msg in self.task.logs))
        self.assertTrue(any("路径点：" in msg and " -> " in msg for msg in self.task.logs))
        self.assertTrue(any("开始：停留 3.0s" in msg for msg in self.task.logs))
        self.assertTrue(any("目标 自动校准完成" in msg for msg in self.task.logs))
        self.assertTrue(any("校准后确认到达" in msg for msg in self.task.logs))

    def test_waiting_for_position_releases_w_and_pauses_follower(self):
        paused = []
        self.task._grid_nav_follower = SimpleNamespace(pause=lambda: paused.append(True))
        self.task._set_grid_walking(True)

        self.task._wait_for_grid_position("测试等待")

        self.assertFalse(self.task.w_down)
        self.assertEqual(paused, [True])

    def test_replan_action_obeys_hard_limit(self):
        followers = []

        class _ReplanningFollower:
            def pause(self):
                pass

            def update(self, position, heading, now):
                return FollowerStep(REPLAN, reason="测试偏航")

        def create_route(start, goal, **kwargs):
            follower = _ReplanningFollower()
            followers.append(follower)
            return follower, PlanResult(
                ok=True,
                cells=[],
                waypoints=[start, goal],
            )

        self.task._create_grid_route = create_route
        self.task.config["最大重规划次数"] = 2

        self.assertFalse(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(len(followers), 3)
        self.assertTrue(any("重规划次数已达上限" in msg for msg in self.task.logs))

    def test_navigate_turns_while_walking(self):
        self.task.config["移动转向最小目标距离(米)"] = 0.0
        self.task.heading = 60.0
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")
        self.assertTrue(result)
        self.assertTrue(self.task.rotations)
        self.assertTrue(all(self.task.rotation_w_held))
        self.assertFalse(self.task.turns)

    def test_large_angle_uses_stationary_turn(self):
        self.task.heading = 0.0
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")
        self.assertTrue(result)
        self.assertTrue(self.task.turns)
        self.assertFalse(self.task.rotations)

    def test_close_waypoint_uses_stationary_turn(self):
        step = SimpleNamespace(heading_error=20.0, distance_to_waypoint=2.0)
        self.assertTrue(self.task._should_turn_grid_in_place(step))

    def test_navigate_can_fall_back_to_stationary_turn(self):
        self.task.config["航点移动转向"] = False
        self.task.heading = 0.0
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")
        self.assertTrue(result)
        self.assertTrue(self.task.turns)
        self.assertFalse(self.task.rotations)

    def test_each_run_restarts_position_sources(self):
        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(self.task.started, 2)
        self.assertEqual(self.task.stopped, 2)

    def test_done_accepted_even_when_final_calibration_does_not_complete(self):
        """目标处没等到 WS 新坐标（静止校准没完成）时，不能丢弃已满足的到达。"""
        def position(frame=None, **kwargs):
            allow_sync = kwargs.get("allow_sync", True)
            # 起步位置能校准；走到目标后 WS 不再推新坐标 -> 校准始终不完成
            synced = bool(allow_sync and self.task.x < 4.0)
            return {
                "x": self.task.x,
                "z": self.task.z,
                "heading": self.task.heading,
                "heading_score": 0.9,
                "anchor_set": True,
                "rest": True,
                "odom_ok": True,
                "odom_reason": "ok",
                "just_synced": synced,
                "sync_checked": synced,
                "map_id": "test",
            }

        self.task.minimap_position = position
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")

        self.assertTrue(result)
        self.assertFalse(self.task.w_down)
        self.assertTrue(any(
            "未完成静止校准，按 follower 判定接受到达" in msg
            for msg in self.task.logs
        ))

    def test_max_replans_zero_still_allows_the_first_plan(self):
        """上限语义是"重规划次数"：设为 0 表示不重规划，而不是连首次规划都禁止。"""
        self.task.config["最大重规划次数"] = 0
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")
        self.assertTrue(result)
        self.assertFalse(any("重规划次数已达上限" in msg for msg in self.task.logs))

    def test_missing_grid_returns_planning_failure(self):
        self.task.config[CONFIG_GRID_FILE] = str(Path(self.tmp.name) / "missing.grid.npz")
        result = self.task.plan_grid_path((0.5, 0.5), (4.5, 0.5), map_id="test")
        self.assertFalse(result.ok)
        self.assertIn("导航网格", result.reason)

    def test_failure_diagnostics_include_depth_notes_and_switch_state(self):
        """失败日志必须带上"搜了多深 / 起终点是否被挪动 / 是不是关了穿越未知格"。"""
        self.task.config["允许穿越未知格"] = False
        result = PlanResult(
            ok=False,
            reason="找不到通路（起点可达区域已穷尽，共 1225 格）：……",
            expanded=1225,
            cap_exceeded=False,
            notes=["起点 (1019, 481) 是未知格，已挪到最近的可通行格 (1022, 478)（偏移 4 格）"],
        )

        self.task._log_grid_plan_failure(result)

        text = "\n".join(self.task.logs)
        self.assertIn("共 1225 格", text)
        self.assertIn("偏移 4 格", text)
        self.assertIn("允许穿越未知格", text)

    def test_failure_diagnostics_report_cap_hit(self):
        result = PlanResult(
            ok=False,
            reason="搜索规模超限：扩展节点超过 max_expand=400000 ……",
            expanded=400001,
            cap_exceeded=True,
        )

        self.task._log_grid_plan_failure(result)

        text = "\n".join(self.task.logs)
        self.assertIn("搜索节点上限", text)   # 指向可调的那个配置项
        self.assertNotIn("已穷尽", text)

    def test_failure_diagnostics_report_timeout(self):
        result = PlanResult(
            ok=False,
            reason="规划超时：本轮导航剩余时间不足以完成 A* 搜索",
            expanded=123,
            timed_out=True,
        )

        self.task._log_grid_plan_failure(result)

        text = "\n".join(self.task.logs)
        self.assertIn("规划超时", text)
        self.assertIn("剩余时间不足", text)
        self.assertNotIn("已穷尽", text)

    def test_waypoint_calibration_waits_full_duration_and_samples(self):
        calls = []

        def position(frame=None, **kwargs):
            allow_sync = kwargs.get("allow_sync", True)
            calls.append(allow_sync)
            return {
                "x": 10.5,
                "z": 19.5,
                "rest": True,
                "odom_ok": True,
                "just_synced": allow_sync,
                "sync_checked": allow_sync,
                "sync_residual": {
                    "map_x": 10.0,
                    "map_z": 20.0,
                    "ws_x": 10.5,
                    "ws_z": 19.5,
                    "dx": -0.5,
                    "dz": 0.5,
                    "dist": 0.707,
                } if allow_sync else None,
            }

        self.task.minimap_position = position
        started = self.task.t
        self.task._grid_nav_distance_since_calibration = 150.0

        self.task._calibrate_grid_waypoint(1, 3)

        self.assertGreaterEqual(len(calls), 10)
        self.assertFalse(calls[0])
        self.assertFalse(calls[1])
        self.assertTrue(any(calls))
        self.assertAlmostEqual(self.task.t - started, 3.0, delta=0.25)
        self.assertTrue(any("自动校准完成" in msg for msg in self.task.logs))
        self.assertTrue(any(
            "静止校准偏差：小地图推算=(10.000, 20.000) WS=(10.500, 19.500) "
            "偏差=(-0.500, +0.500) 距离=0.707m" in msg
            for msg in self.task.logs
        ))
        self.assertAlmostEqual(self.task._grid_nav_distance_since_calibration, 0.0)

    def test_calibration_respects_deadline(self):
        started = self.task.t

        self.task._calibrate_grid_position("截止时间测试", deadline=started + 0.5)

        self.assertLessEqual(self.task.t - started, 0.75)

    def test_calibration_distance_uses_accumulated_travel(self):
        self.task.config["航点校准最小距离(米)"] = 100.0

        self.task._update_grid_nav_travel((0.0, 0.0), "test")
        self.assertFalse(self.task._grid_calibration_due())
        for x in range(10, 91, 10):
            self.task._update_grid_nav_travel((float(x), 0.0), "test")
        self.assertFalse(self.task._grid_calibration_due())
        self.task._update_grid_nav_travel((100.0, 0.0), "test")
        self.assertTrue(self.task._grid_calibration_due())
        self.assertAlmostEqual(self.task._grid_nav_distance_since_calibration, 100.0)

    def test_map_change_does_not_count_as_travel(self):
        self.task._update_grid_nav_travel((0.0, 0.0), "map_a")
        self.task._update_grid_nav_travel((100.0, 0.0), "map_b")
        self.assertAlmostEqual(self.task._grid_nav_distance_since_calibration, 0.0)

    def test_debug_logs_full_position_diagnostics(self):
        self.task.debug = True
        state = {
            "map_id": "test",
            "x": 10.25,
            "z": -20.5,
            "ws": (9.5, -21.0),
            "error": 0.901,
            "dmap_px": (12.5, -4.0),
            "world_delta": (8.0, 2.5),
            "heading": 91.5,
            "heading_score": 0.88,
            "rest": False,
            "anchor_set": True,
            "odom_ok": True,
            "odom_reason": "ok",
            "sync_checked": False,
        }
        self.task._grid_nav_distance_since_calibration = 42.75

        self.task._log_grid_navigation_debug(state, map_id="test", tag="导航")

        line = next(msg for msg in self.task.logs if "[导航定位]" in msg)
        self.assertIn("推算=(10.250, -20.500)", line)
        self.assertIn("WS=(9.500, -21.000)", line)
        self.assertIn("误差=0.901m", line)
        self.assertIn("像素位移=(12.5, -4.0)", line)
        self.assertIn("世界位移=(8.000, 2.500)", line)
        self.assertIn("朝向=91.5", line)
        self.assertIn("距上次校准=42.75m", line)


if __name__ == "__main__":
    unittest.main()
