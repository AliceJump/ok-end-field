"""Unit tests for grid planning and game-facing route execution."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, DenseGrid, GridMeta, save_grid
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import (
    DONE,
    REPLAN,
    WALK,
    FollowerConfig,
    FollowerStep,
    GridRouteFollower,
)
from src.tasks.mixin.grid_navigation_mixin import (
    CONFIG_GRID_FILE,
    GridNavigationMixin,
)


class _FakeGridTask(GridNavigationMixin):
    """用可控位置、时间和输入事件驱动导航循环的测试任务。"""

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
        self.pressed_keys: list[str] = []
        self.key_down_events: list[str] = []
        self.key_up_events: list[str] = []
        self._init_grid_navigation_mixin()
        self._minimap_position_service = self

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
            "sync_seq": 0,
            "sync_needed": False,
            "distance_since_sync": 0.0,
            "map_id": "test",
            "position_trusted": bool(getattr(self, "position_trusted", True)),
            "trust_reason": "sync",
        }

    def minimap_rest_diag(self):
        return {"reason": "ok", "map_speed_m_s": 0.0, "ws_moved_m": 0.0}

    def send_key_down(self, key):
        self.key_down_events.append(key)
        self.w_down = key == "w"

    def send_key_up(self, key):
        self.key_up_events.append(key)
        if key == "w":
            self.w_down = False

    def press_key(self, key, **kwargs):
        self.pressed_keys.append(key)

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
    """覆盖规划、跟随、校准、偏航恢复和脱困的集成行为。"""

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
        self.assertEqual(self.task.stopped, 0)
        self.assertTrue(any("规划完成" in msg for msg in self.task.logs))
        self.assertTrue(any("路径点：" in msg and " -> " in msg for msg in self.task.logs))
        self.assertTrue(any("已到达目标" in msg for msg in self.task.logs))

    def test_waiting_for_position_releases_w_and_pauses_follower(self):
        paused = []
        self.task._grid_nav_follower = SimpleNamespace(pause=lambda: paused.append(True))
        self.task._set_grid_walking(True)

        self.task._wait_for_grid_position("测试等待")

        self.assertFalse(self.task.w_down)
        self.assertEqual(paused, [True])

    def test_wait_for_minimap_sync_pauses_and_waits_for_new_sequence(self):
        class _PositionService:
            def __init__(self):
                self.calls = 0

            def minimap_position(self, frame=None, **kwargs):
                self.calls += 1
                return {
                    "sync_seq": 1 if self.calls >= 2 else 0,
                    "sync_checked": self.calls >= 2,
                }

            def minimap_rest_diag(self):
                return {"reason": "ok"}

        position_service = _PositionService()
        self.task._set_grid_walking(True)

        synced = self.task._wait_for_minimap_sync(
            position_service,
            start_sync_seq=0,
            deadline=self.task.active_time() + 2.0,
            tick=0.2,
        )

        self.assertTrue(synced)
        self.assertFalse(self.task.w_down)
        self.assertEqual(position_service.calls, 2)

    def test_stuck_recovery_uses_back_side_then_jump(self):
        self.task._choose_grid_strafe_side = lambda position, heading: "d"
        deadline = self.task.active_time() + 10.0

        self.task._recover_grid_stuck(
            position=(0.0, 0.0),
            heading=0.0,
            target_bearing=90.0,
            frame=object(),
            attempt=1,
            deadline=deadline,
        )
        self.assertEqual(self.task.pressed_keys, ["s"])

        self.task.pressed_keys.clear()
        self.task.key_down_events.clear()
        self.task.key_up_events.clear()
        self.task._recover_grid_stuck(
            position=(0.0, 0.0),
            heading=0.0,
            target_bearing=90.0,
            frame=object(),
            attempt=2,
            deadline=deadline,
        )
        self.assertEqual(self.task.key_down_events, ["s", "d"])
        self.assertEqual(self.task.key_up_events, ["d", "s"])

        self.task.pressed_keys.clear()
        self.task.key_down_events.clear()
        self.task.key_up_events.clear()
        self.task._recover_grid_stuck(
            position=(0.0, 0.0),
            heading=0.0,
            target_bearing=90.0,
            frame=object(),
            attempt=3,
            deadline=deadline,
        )
        self.assertIn("w", self.task.key_down_events)
        self.assertIn("space", self.task.pressed_keys)
        self.assertFalse(self.task.w_down)
        self.assertTrue(self.task.turns)

    def test_stuck_recovery_chooses_open_strafe_side(self):
        cells = np.full((21, 21), CELL_FREE, dtype=np.uint8)
        cells[:, :10] = CELL_BLOCKED
        grid = DenseGrid(
            cells,
            GridMeta(origin=(-10.0, 0.0, -10.0), cell_size=1.0),
        )
        self.task._grid_nav_follower = GridRouteFollower(
            grid,
            FollowerConfig(margin=0),
        )

        side = self.task._choose_grid_strafe_side((0.0, 0.0), heading=0.0)

        self.assertEqual(side, "d")

    def test_navigation_calibrates_after_five_waypoints(self):
        calls = []

        class _WaypointFollower:
            def __init__(self):
                self.count = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count <= 5:
                    return FollowerStep(
                        WALK,
                        arrived_waypoint_index=self.count - 1,
                        waypoint_count=10,
                        distance_to_goal=float(100 - self.count),
                    )
                return FollowerStep(DONE, distance_to_goal=0.0)

        follower = _WaypointFollower()
        self.task._create_grid_route = lambda start, goal, **kwargs: (
            follower,
            PlanResult(ok=True, waypoints=[start, goal]),
        )
        self.task._wait_for_minimap_sync = (
            lambda service, start_seq, deadline, tick: calls.append(start_seq) or True
        )

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_known_cell_off_route_calibrates_before_replan(self):
        calls = []

        class _Follower:
            def __init__(self):
                self.count = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count == 1:
                    return FollowerStep(REPLAN, reason="测试偏航")
                return FollowerStep(DONE, distance_to_goal=0.0)

        follower = _Follower()
        self.task._create_grid_route = lambda start, goal, **kwargs: (
            follower,
            PlanResult(ok=True, waypoints=[start, goal]),
        )
        self.task._grid_position_is_known = lambda follower, position: True
        self.task._wait_for_minimap_sync = (
            lambda service, start_seq, deadline, tick: calls.append(start_seq) or True
        )

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_untrusted_position_calibrates_before_first_plan(self):
        calls = []
        self.task.position_trusted = False

        def sync(service, start_seq, deadline, tick):
            calls.append(start_seq)
            self.task.position_trusted = True
            return True

        self.task._wait_for_minimap_sync = sync

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_unknown_cell_off_route_returns_to_visited_cell_then_calibrates(self):
        calls = []

        class _Follower:
            def __init__(self):
                self.count = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count == 1:
                    return FollowerStep(REPLAN, reason="测试偏航")
                return FollowerStep(DONE, distance_to_goal=0.0)

        follower = _Follower()
        follower.grid = DenseGrid(
            np.full((10, 10), CELL_FREE, dtype=np.uint8),
            GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0),
        )
        self.task._create_grid_route = lambda start, goal, **kwargs: (
            follower,
            PlanResult(ok=True, waypoints=[start, goal]),
        )
        self.task._grid_position_is_known = lambda follower, position: False
        self.task._wait_for_minimap_sync = (
            lambda service, start_seq, deadline, tick: calls.append(start_seq) or True
        )

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_nearest_visited_grid_cell_uses_only_walked_cells(self):
        visited = {
            (0, 0): (0.5, 0.5),
            (3, 1): (1.5, 3.5),
        }

        target = self.task._nearest_visited_grid_cell((1.0, 3.0), visited)

        self.assertEqual(target, (1.5, 3.5))

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

    def test_each_run_reuses_shared_position_service(self):
        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(self.task.started, 2)
        self.assertEqual(self.task.stopped, 0)

    def test_done_does_not_require_extra_calibration(self):
        """静止校准由定位服务自动完成，导航只消费当前融合坐标。"""
        result = self.task.navigate_grid_to((4.5, 0.5), map_id="test")

        self.assertTrue(result)
        self.assertFalse(self.task.w_down)
        self.assertTrue(any("已到达目标" in msg for msg in self.task.logs))

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
            "sync_seq": 7,
        }

        self.task._log_grid_navigation_debug(state, map_id="test", tag="导航")

        line = next(msg for msg in self.task.logs if "[导航定位]" in msg)
        self.assertIn("推算=(10.250, -20.500)", line)
        self.assertIn("WS=(9.500, -21.000)", line)
        self.assertIn("误差=0.901m", line)
        self.assertIn("像素位移=(12.5, -4.0)", line)
        self.assertIn("世界位移=(8.000, 2.500)", line)
        self.assertIn("朝向=91.5", line)
        self.assertIn("校准序号=7", line)


if __name__ == "__main__":
    unittest.main()
