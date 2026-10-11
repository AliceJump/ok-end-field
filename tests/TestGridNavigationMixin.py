"""Unit tests for grid planning and game-facing route execution."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from src.data.FeatureList import FeatureList as fL
from src.localization.minimap_heading_mixin import CONFIG_MIN_SCORE
from src.localization.minimap_position_mixin import MinimapPositionMixin
from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, DenseGrid, GridMeta, save_grid
from src.nav.grid_planner import PlanResult, ZipLineRouteStep
from src.nav.nav_state import NavState, SyncReason
from src.nav.route_follower import (
    DONE,
    FAILED,
    NARROW_CALIBRATE,
    REPLAN,
    STUCK,
    TURN,
    WAIT,
    WALK,
    ZIP_LINE,
    FollowerConfig,
    FollowerStep,
    GridRouteFollower,
)
from src.nav.zip_line_graph import ZipLineGraph, ZipLineLink, ZipLineNode, ZipLineStep
from src.runtime_state.topics import RuntimeTopic
from src.tasks.navigation.mixin.grid_navigation_mixin import (
    CONFIG_GRID_ALLOW_UNKNOWN,
    CONFIG_GRID_FILE,
    CONFIG_GRID_USE_SHARED_ZIP_LINES,
    GridNavigationMixin,
    StuckRecovery,
)
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineReplanRequired


class _FakeGridTask(GridNavigationMixin):
    """用可控位置、时间和输入事件驱动导航循环的测试任务。"""

    def __init__(self, grid_path: Path):
        self.config = {
            **self.grid_navigation_default_config(),
            CONFIG_GRID_FILE: str(grid_path),
            "到达目标半径(米)": 0.5,
            "航点到达半径(米)": 0.5,
            "行走朝向容差(度)": 4.0,
            "控制周期(秒)": 0.2,
            "导航超时(秒)": 30.0,
        }
        self.logs: list[str] = []
        self.info = {}
        self.x = 0.5
        self.z = 0.5
        self.esc_visible = False
        self.board_template_visible = False
        self.heading = 90.0
        self.t = 0.0
        self.w_down = False
        self.started = 0
        self.stopped = 0
        self.aims: list[float] = []
        self.turns: list[float] = []
        self.rotations: list[int] = []
        self.rotation_w_held: list[bool] = []
        self.pressed_keys: list[str] = []
        self.key_down_events: list[str] = []
        self.key_up_events: list[str] = []
        self._init_grid_navigation_mixin()
        self._minimap_position_service = self
        self._minimap_ready = False

    def _grid_nav_config(self):
        return self.config

    def find_feature(self, feature_name=None, frame=None, **kwargs):
        if feature_name == fL.esc:
            return [object()] if self.esc_visible else []
        if feature_name == fL.climb_the_zip_line:
            return [object()] if self.board_template_visible else []
        return []

    def _zip_line_ws_position(self):
        return self.x, self.z

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
        self._minimap_started = True
        self._minimap_ready = True
        return True

    def stop_minimap_position(self):
        self.stopped += 1
        self._minimap_fusion = None
        self._minimap_ready = False

    @property
    def minimap_position_ready(self):
        return self._minimap_ready

    @minimap_position_ready.setter
    def minimap_position_ready(self, value):
        self._minimap_ready = bool(value)

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

    def sample_world_pose(self, frame=None, *, now=None):
        state = self.minimap_position(frame=frame)
        self.runtime_state_hub.publish(
            RuntimeTopic.WORLD_POSE,
            state,
            source="test",
            now=now,
            ttl=2.0,
        )
        return state

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

    def aim_view_to_bearing(self, target_deg, **kwargs):
        self.aims.append(float(target_deg))
        self.heading = float(target_deg)
        return {"ok": True, "heading": self.heading, "error": 0.0}

    def heading_min_score(self):
        return float(self.config.get(CONFIG_MIN_SCORE, 0.6))

    def yaw_per_pixel(self):
        return 0.07125

    def can_turn(self):
        return True

    def send_rotation(self, dx):
        self._send_rotation(int(dx))

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


def _record_sync(calls: list[int], reasons: list[str] | None = None):
    """替身：记录静止校准的起始序号，可选记录触发原因。"""

    def _wait(start_sync_seq, deadline, tick, *, reason=None):
        calls.append(start_sync_seq)
        if reasons is not None:
            reasons.append(reason)
        return True

    return _wait


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
        self.assertTrue(any("路径点：(0.500, 0.500) -> (4.500, 0.500)" in msg for msg in self.task.logs))

    def test_grid_config_does_not_duplicate_position_owner_controls(self):
        config = self.task.grid_navigation_default_config()
        descriptions = self.task.grid_navigation_config_description()

        for key in MinimapPositionMixin.minimap_position_default_config():
            self.assertNotIn(key, config)
            self.assertNotIn(key, descriptions)

    def test_grid_heading_threshold_reads_position_owner_config(self):
        service = SimpleNamespace(
            enabled=True,
            _minimap_started=True,
            minimap_position_ready=True,
            heading_min_score=lambda: 0.77,
        )
        self.task._runtime_position_service = service

        self.assertAlmostEqual(self.task._grid_heading_min_score(), 0.77)

    def test_allow_grid_unknown_defaults_to_false_when_key_is_missing(self):
        self.task.config.pop(CONFIG_GRID_ALLOW_UNKNOWN, None)

        self.assertFalse(self.task.allow_grid_unknown())

    def test_grid_option_prefers_global_nav_config_over_task_config(self):
        self.task.config[CONFIG_GRID_ALLOW_UNKNOWN] = False
        self.task._grid_nav_config = lambda: {CONFIG_GRID_ALLOW_UNKNOWN: True}

        self.assertTrue(self.task.allow_grid_unknown())

    def test_loads_user_zip_lines_from_position_service(self):
        payload = {
            "code": 0,
            "data": {
                "markTemplates": [
                    {"id": "normal", "name": "滑索架"},
                ],
                "saveMarks": [
                    {
                        "id": "a",
                        "templateId": "normal",
                        "mapId": "test",
                        "levelId": "lv1",
                        "pos": {"x": 0.5, "y": 0.0, "z": 0.5},
                    },
                    {
                        "id": "b",
                        "templateId": "normal",
                        "mapId": "test",
                        "levelId": "lv1",
                        "pos": {"x": 20.5, "y": 0.0, "z": 0.5},
                    },
                ],
            },
        }
        self.task._map_ws_account = {"roleId": "r", "serverId": "s"}
        self.task._map_api_get = lambda path, params=None: payload
        self.task.config[CONFIG_GRID_USE_SHARED_ZIP_LINES] = True
        self.task.zip_line_list_go = lambda distances, **kwargs: None

        with mock.patch("src.data.user_map_mark_store.persist_user_marks") as persist:
            graph = self.task._grid_zip_lines_for_map("test")
            self.assertEqual(persist.call_count, 1)

        self.assertIsNotNone(graph)
        self.assertEqual(len(graph), 2)
        self.assertEqual(len(graph.links), 1)

    def test_refresh_grid_zip_lines_clears_cached_snapshot(self):
        self.task._grid_nav_zip_line_cache["test"] = object()
        self.task._grid_nav_zip_line_empty.add("other")

        self.task._refresh_grid_zip_lines()

        self.assertEqual(self.task._grid_nav_zip_line_cache, {})
        self.assertEqual(self.task._grid_nav_zip_line_empty, set())

    def test_navigation_refreshes_zip_line_snapshot_at_start(self):
        refreshes = []
        self.task._refresh_grid_zip_lines = lambda map_id="": refreshes.append(map_id)

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))

        self.assertEqual(refreshes, [""])

    def test_navigation_can_stop_before_goal_for_final_approach(self):
        self.task.x = 1.0
        self.task.z = 0.5

        self.assertTrue(
            self.task.navigate_grid_to(
                (4.5, 0.5),
                map_id="test",
                stop_distance=5.0,
            )
        )

        self.assertTrue(any("进入终点接近范围" in msg for msg in self.task.logs))
        self.assertFalse(self.task.w_down)

    def test_execute_zip_line_uses_existing_zip_line_mixin(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 20.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=20.0),
            entry_cell=(0, 0),
            exit_cell=(0, 20),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=6.0,
        )
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append((distances, kwargs))
        self.task.board_zip_line = lambda **kwargs: calls.append(("board", kwargs)) or True
        self.task.zip_line_scroll_enabled = lambda: True

        self.assertTrue(self.task._execute_grid_zip_line(route_step))

        self.assertEqual(
            calls[0],
            (
                "board",
                {
                    "direct_wait": 5.0,
                    "total_time_out": 30.0,
                    "switch_walk_mode": True,
                },
            ),
        )
        self.assertEqual(calls[1][0], [20])
        self.assertTrue(calls[1][1]["need_scroll"])
        self.assertFalse(calls[1][1]["need_v"])
        self.assertGreaterEqual(calls[1][1]["distance_tolerance"][0], 2)
        self.assertEqual(calls[1][1]["target_positions"], [(20.5, 0.5)])
        self.assertEqual(calls[1][1]["source_positions_xyz"], [(0.5, 0.0, 0.5)])
        self.assertEqual(calls[1][1]["target_positions_xyz"], [(20.5, 0.0, 0.5)])
        self.assertAlmostEqual(calls[1][1]["target_bearing"][0], 90.0)

    def test_execute_zip_line_skips_board_when_already_on_rack(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 20.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=20.0),
            entry_cell=(0, 0),
            exit_cell=(0, 20),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=6.0,
        )
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))
        self.task.board_zip_line = lambda **kwargs: calls.append(("board", kwargs)) or True

        self.assertTrue(
            self.task._execute_grid_zip_line(
                route_step,
                already_on_rack=True,
            )
        )

        self.assertEqual(calls, [("ride", [20])])

    def test_execute_zip_line_retries_board_when_esc_remains(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 20.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=20.0),
            entry_cell=(0, 0),
            exit_cell=(0, 20),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=6.0,
        )

        def board(**kwargs):
            calls.append("board")
            self.task.esc_visible = len(calls) == 1
            return True

        self.task.x = 0.5
        self.task.z = 5.5
        self.task.board_zip_line = board
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))

        self.assertTrue(self.task._execute_grid_zip_line(route_step))

        self.assertEqual(calls, ["board", "board", ("ride", [20])])
        self.assertEqual(self.task.turns, [])
        self.assertAlmostEqual(self.task.aims[-1], 180.0)
        self.assertEqual((self.task.x, self.task.z), (0.5, 5.5))

    def test_execute_zip_line_fails_when_esc_remains_after_retry(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 20.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=20.0),
            entry_cell=(0, 0),
            exit_cell=(0, 20),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=6.0,
        )
        self.task.esc_visible = True
        self.task.board_zip_line = lambda **kwargs: calls.append("board") or True
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))

        self.assertFalse(self.task._execute_grid_zip_line(route_step))

        self.assertEqual(calls, ["board", "board"])

    def test_enter_rack_does_not_retry_when_button_missing(self):
        """找不到「登上滑索架」按钮时不重试：重新对准解决不了按钮不存在。"""
        self.task.board_zip_line = lambda **kwargs: False
        reaim_calls = []
        self.task._reaim_grid_zip_line_for_boarding = lambda route_step: reaim_calls.append(route_step) or True

        self.assertFalse(self.task._grid_zip_line_enter_rack(object(), self.task.board_zip_line, switch_walk_mode=True))

        self.assertEqual(reaim_calls, [])
        self.assertTrue(any("未找到「登上滑索架」按钮" in line for line in self.task.logs))

    def test_enter_rack_does_not_retry_when_board_unconfirmed(self):
        """点了按钮但 fL.esc 判定超时：无法确认就不重试，直接失败。"""
        self.task.board_zip_line = lambda **kwargs: True
        self.task._wait_grid_zip_line_esc_clear = lambda: None
        reaim_calls = []
        self.task._reaim_grid_zip_line_for_boarding = lambda route_step: reaim_calls.append(route_step) or True

        self.assertFalse(self.task._grid_zip_line_enter_rack(object(), self.task.board_zip_line, switch_walk_mode=True))

        self.assertEqual(reaim_calls, [])
        self.assertFalse(any("仍然检测到" in line for line in self.task.logs))

    def test_enter_rack_reaims_once_when_not_boarded(self):
        """确认没登上（esc 仍在）才重新对准；第二次成功后不再重试。"""
        board_calls = []
        self.task.board_zip_line = lambda **kwargs: board_calls.append(kwargs) or True
        esc_results = iter([True, False])
        self.task._wait_grid_zip_line_esc_clear = lambda: next(esc_results)
        reaim_calls = []
        self.task._reaim_grid_zip_line_for_boarding = lambda route_step: reaim_calls.append(route_step) or True

        self.assertTrue(self.task._grid_zip_line_enter_rack(object(), self.task.board_zip_line, switch_walk_mode=True))

        self.assertEqual(len(board_calls), 2)
        self.assertEqual(len(reaim_calls), 1)
        self.assertTrue(any("已成功登上滑索" in line for line in self.task.logs))

    def test_enter_rack_reports_retry_specific_failures(self):
        """重试轮失败要用"重新对正后…"的文案，与首轮区分开。"""
        self.task.board_zip_line = lambda **kwargs: True
        self.task._reaim_grid_zip_line_for_boarding = lambda route_step: True

        esc_results = iter([True, True])
        self.task._wait_grid_zip_line_esc_clear = lambda: next(esc_results)
        self.assertFalse(self.task._grid_zip_line_enter_rack(object(), self.task.board_zip_line, switch_walk_mode=True))
        self.assertTrue(any("重新对正后仍检测到 fL.esc，登上滑索失败" in line for line in self.task.logs))

        self.task.logs.clear()
        board_results = iter([True, False])
        self.task.board_zip_line = lambda **kwargs: next(board_results)
        esc_results = iter([True])
        self.task._wait_grid_zip_line_esc_clear = lambda: next(esc_results)
        self.assertFalse(self.task._grid_zip_line_enter_rack(object(), self.task.board_zip_line, switch_walk_mode=True))
        self.assertTrue(any("重新对正后仍未找到「登上滑索架」按钮" in line for line in self.task.logs))

    def test_navigation_executes_zip_line_action_before_walking_to_exit(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )

        class _ZipLineFollower:
            def __init__(self):
                self.count = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count == 1:
                    return FollowerStep(ZIP_LINE, zip_line_step=route_step)
                return FollowerStep(DONE, distance_to_goal=0.0)

            def complete_zip_line(self):
                calls.append("complete")
                return True

        self.task._create_grid_route = lambda start, goal, **kwargs: (
            _ZipLineFollower(),
            PlanResult(ok=True, waypoints=[start, goal], zip_line_steps=[route_step]),
        )
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))
        self.task.board_zip_line = lambda **kwargs: calls.append(("board", kwargs)) or True

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))

        self.assertEqual(calls[0][0], "board")
        self.assertEqual(calls[1], ("ride", [4]))
        self.assertEqual(calls[2], "complete")

    def test_navigation_approaches_zip_line_in_walk_mode_and_boards_with_f(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )

        class _ApproachFollower:
            def __init__(self):
                self.completed = False

            def pause(self):
                pass

            def _current_zip_line_step(self):
                return None if self.completed else route_step

            def update(self, position, heading, now):
                if self.completed:
                    return FollowerStep(DONE, distance_to_goal=0.0)
                return FollowerStep(
                    WALK,
                    waypoint=first.xz,
                    target_bearing=270.0,
                    heading_error=0.0,
                    distance_to_waypoint=3.0,
                    distance_to_goal=1.0,
                )

            def complete_zip_line(self):
                calls.append("complete")
                self.completed = True
                return True

        self.task._create_grid_route = lambda start, goal, **kwargs: (
            _ApproachFollower(),
            PlanResult(ok=True, waypoints=[start, goal], zip_line_steps=[route_step]),
        )
        self.task.x = 3.5
        self.task.heading = 270.0
        self.task.board_template_visible = True
        self.task.board_zip_line = lambda **kwargs: calls.append(("board", kwargs)) or True
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))

        self.assertEqual(calls, [("ride", [4]), "complete"])
        self.assertEqual(self.task.pressed_keys.count("ctrl"), 2)
        self.assertIn("f", self.task.pressed_keys)
        self.assertFalse(self.task._grid_nav_zip_line_walk_mode)

    def test_navigation_boards_at_zip_line_entry_with_ws_approach_without_board_fallback(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )

        class _ZipLineFollower:
            def __init__(self):
                self.completed = False

            def pause(self):
                pass

            def _current_zip_line_step(self):
                return None if self.completed else route_step

            def update(self, position, heading, now):
                if self.completed:
                    return FollowerStep(DONE, distance_to_goal=0.0)
                return FollowerStep(
                    ZIP_LINE,
                    zip_line_step=route_step,
                    distance_to_goal=1.0,
                )

            def complete_zip_line(self):
                calls.append("complete")
                self.completed = True
                return True

        self.task._create_grid_route = lambda start, goal, **kwargs: (
            _ZipLineFollower(),
            PlanResult(ok=True, waypoints=[start, goal], zip_line_steps=[route_step]),
        )
        self.task.x = 3.5
        self.task.esc_visible = False
        self.task.board_template_visible = True
        self.task._grid_nav_zip_line_walk_mode = True
        self.task.board_zip_line = lambda **kwargs: calls.append(("board", kwargs)) or True
        self.task.zip_line_list_go = lambda distances, **kwargs: calls.append(("ride", distances))

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))

        self.assertEqual(
            calls,
            [
                ("ride", [4]),
                "complete",
            ],
        )
        self.assertNotIn("s", self.task.key_down_events)
        self.assertFalse(self.task._grid_nav_zip_line_walk_mode)

    def test_navigation_ws_approach_keeps_searching_before_short_retreat(self):
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )
        self.task.x = 3.5
        self.task.z = 0.5
        self.task.heading = 270.0
        self.task.board_template_visible = False
        self.task._zip_line_ws_position = lambda: (100.0, 100.0)
        self.task.t = 0.0
        self.task.sleep = lambda seconds: setattr(
            self.task,
            "t",
            self.task.t + seconds,
        )

        result = self.task._approach_grid_zip_line_for_boarding(
            route_step=route_step,
            frame=object(),
            min_score=0.6,
            deadline=30.0,
            tick=0.2,
        )

        self.assertIsNone(result)
        self.assertNotIn("s", self.task.key_down_events)

        self.task.t = 20.1
        result = self.task._approach_grid_zip_line_for_boarding(
            route_step=route_step,
            frame=object(),
            min_score=0.6,
            deadline=30.0,
            tick=0.2,
        )

        self.assertIsNone(result)
        self.assertIn("w", self.task.key_down_events)
        self.assertIn("s", self.task.key_down_events)
        self.assertAlmostEqual(self.task.aims[-1], 225.0)
        self.assertTrue(any("不先后退" in message for message in self.task.logs))

    def test_navigation_ws_approach_waits_past_old_timeout_for_prompt(self):
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )
        self.task.x = 3.5
        self.task.z = 0.5
        self.task.board_template_visible = False
        self.task._zip_line_ws_position = lambda: (100.0, 100.0)
        self.task.t = 0.0
        self.task.sleep = lambda seconds: setattr(self.task, "t", self.task.t + seconds)

        self.assertIsNone(
            self.task._approach_grid_zip_line_for_boarding(
                route_step=route_step,
                frame=object(),
                min_score=0.6,
                deadline=30.0,
                tick=0.2,
            )
        )

        self.task.t = 12.1
        self.task.board_template_visible = True
        self.task.esc_visible = False
        result = self.task._approach_grid_zip_line_for_boarding(
            route_step=route_step,
            frame=object(),
            min_score=0.6,
            deadline=30.0,
            tick=0.2,
        )

        self.assertTrue(result)
        self.assertIn("f", self.task.pressed_keys)
        self.assertFalse(any("不先后退" in message for message in self.task.logs))

    def test_failed_f_boarding_uses_short_retreat(self):
        self.task.board_template_visible = True
        self.task.esc_visible = True
        self.task.t = 0.0
        self.task.sleep = lambda seconds: setattr(self.task, "t", self.task.t + seconds)

        result = self.task._press_f_for_grid_zip_line(10.0)

        self.assertIsNone(result)
        self.assertTrue(any("按 S 远离滑索 0.6s" in message for message in self.task.logs))

    def test_wait_zip_line_esc_clear_ignores_transition_frame(self):
        results = iter([True, True, False])
        self.task._grid_zip_line_esc_visible = lambda: next(results)
        self.task.t = 0.0

        result = self.task._wait_grid_zip_line_esc_clear(
            timeout=1.5,
            poll_interval=0.2,
        )

        self.assertFalse(result)
        self.assertGreaterEqual(self.task.t, 0.4)

    def test_navigation_replans_when_zip_line_lands_on_wrong_rack(self):
        calls = []
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        wrong = ZipLineNode("wrong", "test", "lv1", "滑索架", 3.5, 0.0, 0.5)
        zip_lines = ZipLineGraph([first, second, wrong], [])
        route_step = ZipLineRouteStep(
            step=ZipLineStep(first, second, distance_m=4.0),
            entry_cell=(0, 0),
            exit_cell=(0, 4),
            entry_waypoint_index=0,
            exit_waypoint_index=1,
            cost=2.8,
        )

        class _ZipLineFollower:
            def pause(self):
                pass

            def update(self, position, heading, now):
                return FollowerStep(ZIP_LINE, zip_line_step=route_step)

        class _DoneFollower:
            def pause(self):
                pass

            def update(self, position, heading, now):
                return FollowerStep(DONE, distance_to_goal=0.0)

        created = {"count": 0}
        starts = []
        required_starts = []

        def _create_grid_route(start, goal, **kwargs):
            starts.append(start)
            required_starts.append(kwargs.get("required_zip_line_start_id"))
            created["count"] += 1
            follower = _ZipLineFollower() if created["count"] == 1 else _DoneFollower()
            return follower, PlanResult(
                ok=True,
                waypoints=[start, goal],
                zip_line_steps=[route_step] if created["count"] == 1 else [],
            )

        def _execute_grid_zip_line(route_step_arg, **kwargs):
            calls.append("zip")
            raise ZipLineReplanRequired(
                "测试落点错误",
                current_position=(1.0, 0.5),
                failed_target_position=(4.5, 0.5),
            )

        self.task._grid_zip_lines_for_map = lambda map_id: zip_lines
        self.task._zip_line_ws_position = lambda: (3.45, 0.5)
        self.task._create_grid_route = _create_grid_route
        self.task._execute_grid_zip_line = _execute_grid_zip_line

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, ["zip"])
        self.assertEqual(created["count"], 2)
        self.assertEqual(starts, [(0.5, 0.5), (3.5, 0.5)])
        self.assertEqual(required_starts, [None, "wrong"])
        self.assertTrue(any("重新规划" in msg for msg in self.task.logs))

    def test_confirmed_blocked_zip_link_is_removed_from_replanning(self):
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 10.5, 0.0, 0.5)
        third = ZipLineNode("c", "test", "lv1", "滑索架", 20.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, second, third],
            [
                ZipLineLink("a", "b", 10.0, 80.0),
                ZipLineLink("a", "c", 20.0, 80.0),
                ZipLineLink("c", "b", 10.0, 80.0),
            ],
        )
        self.task._grid_zip_lines_for_map = lambda map_id: graph

        self.task._block_grid_zip_link_from_exception(
            ZipLineReplanRequired(
                "blocked",
                current_position=(0.5, 0.5),
                failed_target_position=(10.5, 0.5),
            ),
            "test",
        )
        filtered = self.task._filter_blocked_grid_zip_lines(graph)

        self.assertIsNotNone(filtered)
        self.assertEqual(len(filtered.links), 2)
        self.assertFalse(
            any(frozenset((link.first_id, link.second_id)) == frozenset(("a", "b")) for link in filtered.links)
        )
        logs = "\n".join(self.task.logs)
        self.assertIn(
            "滑索连接不可用：(0.50, 0.00, 0.50) <-> (10.50, 0.00, 0.50)",
            logs,
        )
        self.assertNotIn("a <-> b", logs)

    def test_blocked_zip_link_survives_next_navigation_with_new_node_ids(self):
        first = ZipLineNode("old-a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("old-b", "test", "lv1", "滑索架", 10.5, 0.0, 0.5)
        old_graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("old-a", "old-b", 10.0, 80.0)],
        )
        self.task._grid_zip_lines_for_map = lambda map_id: old_graph
        self.task._block_grid_zip_link_from_exception(
            ZipLineReplanRequired(
                "blocked",
                current_position=(0.5, 0.5),
                failed_target_position=(10.5, 0.5),
            ),
            "test",
        )

        refreshed_first = ZipLineNode("new-a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        refreshed_second = ZipLineNode("new-b", "test", "lv1", "滑索架", 10.5, 0.0, 0.5)
        refreshed_graph = ZipLineGraph(
            [refreshed_first, refreshed_second],
            [ZipLineLink("new-a", "new-b", 10.0, 80.0)],
        )

        self.task._reset_grid_navigation_run_state()
        filtered = self.task._filter_blocked_grid_zip_lines(refreshed_graph, "test")

        self.assertIsNotNone(filtered)
        self.assertEqual(filtered.links, ())

    def test_vertical_zip_line_failure_blocks_attempted_link_not_same_xz_projection(self):
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 10.5, 0.0, 0.5)
        third = ZipLineNode("c", "test", "lv1", "滑索架", 10.5, 100.0, 0.5)
        graph = ZipLineGraph(
            [first, second, third],
            [
                ZipLineLink("a", "b", 10.0, 80.0),
                ZipLineLink("b", "c", 100.0, 110.0),
            ],
        )
        self.task._grid_zip_lines_for_map = lambda map_id: graph

        self.task._block_grid_zip_link_from_exception(
            ZipLineReplanRequired(
                "blocked",
                current_position=second.xyz,
                failed_target_position=third.xyz,
            ),
            "test",
        )
        filtered = self.task._filter_blocked_grid_zip_lines(graph, "test")

        self.assertIsNotNone(filtered)
        self.assertEqual(len(filtered.links), 1)
        self.assertEqual(
            frozenset((filtered.links[0].first_id, filtered.links[0].second_id)),
            frozenset(("a", "b")),
        )
        self.assertTrue(any("(10.50, 0.00, 0.50) <-> (10.50, 100.00, 0.50)" in message for message in self.task.logs))

    def test_replan_blocks_failed_segment_but_starts_from_actual_landing_node(self):
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 10.5, 0.0, 0.5)
        third = ZipLineNode("c", "test", "lv1", "长距滑索架", 10.5, 100.0, 0.5)
        graph = ZipLineGraph(
            [first, second, third],
            [
                ZipLineLink("a", "b", 10.0, 80.0),
                ZipLineLink("b", "c", 100.0, 110.0),
            ],
        )
        self.task._grid_zip_lines_for_map = lambda map_id: graph
        self.task._grid_nav_zip_line_start_hint = second.xyz
        self.task._grid_nav_zip_line_failed_target_hint = third.xyz
        self.task._zip_line_ws_position_xyz = lambda: first.xyz

        start = self.task._resolve_grid_replan_start("test", (0.5, 0.5))
        filtered = self.task._filter_blocked_grid_zip_lines(graph, "test")

        self.assertEqual(start, first.xz)
        self.assertEqual(self.task._grid_nav_skip_board_node_id, "a")
        self.assertIsNotNone(filtered)
        self.assertEqual(len(filtered.links), 1)
        self.assertEqual(
            frozenset((filtered.links[0].first_id, filtered.links[0].second_id)),
            frozenset(("a", "b")),
        )

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

    def test_long_waypoint_segment_starts_sprint_once_per_w_hold(self):
        self.task._grid_nav_follower = SimpleNamespace(plan_result=SimpleNamespace(waypoints=[(0.0, 0.0), (20.0, 0.0)]))
        step = FollowerStep(
            WALK,
            waypoint=(20.0, 0.0),
            distance_to_waypoint=20.0,
            waypoint_index=1,
        )
        self.task._set_grid_walking(True)

        self.task._maybe_start_grid_sprint(step)
        self.task._maybe_start_grid_sprint(step)

        self.assertEqual(self.task.pressed_keys, ["shift"])
        self.assertTrue(self.task._grid_nav_sprint_active)

        self.task._set_grid_walking(False)
        self.task._set_grid_walking(True)
        self.task._maybe_start_grid_sprint(step)

        self.assertEqual(self.task.pressed_keys, ["shift", "shift"])

    def test_short_waypoint_segment_does_not_start_sprint(self):
        self.task._grid_nav_follower = SimpleNamespace(plan_result=SimpleNamespace(waypoints=[(0.0, 0.0), (10.0, 0.0)]))
        step = FollowerStep(
            WALK,
            waypoint=(10.0, 0.0),
            distance_to_waypoint=10.0,
            waypoint_index=1,
        )
        self.task._set_grid_walking(True)

        self.task._maybe_start_grid_sprint(step)

        self.assertEqual(self.task.pressed_keys, [])
        self.assertFalse(self.task._grid_nav_sprint_active)

    def test_waiting_for_position_releases_w_and_pauses_follower(self):
        paused = []
        self.task._grid_nav_follower = SimpleNamespace(pause=lambda: paused.append(True))
        self.task._set_grid_walking(True)

        self.task._wait_for_grid_position("测试等待")

        self.assertFalse(self.task.w_down)
        self.assertEqual(paused, [True])

    def test_waiting_for_position_logs_rest_diagnostics(self):
        self.task.t = 6.0
        self.task.minimap_rest_diag = lambda: {
            "reason": "map_moving",
            "map_speed_m_s": 1.25,
            "ws_moved_m": 0.0,
        }

        self.task._wait_for_grid_position("等待定位重新校准")

        line = next(msg for msg in self.task.logs if "等待定位重新校准" in msg and "rest_reason" in msg)
        self.assertIn("rest_reason=map_moving", line)
        self.assertIn("speed=1.25", line)
        self.assertIn("ws_moved=0.0", line)

    def test_wait_for_minimap_sync_pauses_and_waits_for_new_sequence(self):
        class _PositionService:
            def __init__(self):
                self.calls = 0

            def start_minimap_position(self, *, wait_stable=True):
                return True

            @property
            def minimap_position_ready(self):
                return True

            def sample_world_pose(self, frame=None, *, now=None):
                self.calls += 1
                state = {
                    "sync_seq": 1 if self.calls >= 2 else 0,
                    "sync_checked": self.calls >= 2,
                }
                self.task.runtime_state_hub.publish(
                    RuntimeTopic.WORLD_POSE,
                    state,
                    source="test",
                    now=now,
                    ttl=2.0,
                )
                return state

            def minimap_rest_diag(self):
                return {"reason": "ok"}

        position_service = _PositionService()
        position_service.task = self.task
        self.task._runtime_position_service = position_service
        self.task._set_grid_walking(True)

        synced = self.task._wait_for_minimap_sync(
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

    def test_stuck_recovery_sequence_repeats_last_step(self):
        """动作序列是显式数据：用完之后重复最后一步，不会越界也不会回到第一步。"""
        steps = [self.task._grid_stuck_recovery_step(attempt, 5)[0] for attempt in (1, 2, 3, 4, 5)]
        self.assertEqual(
            steps,
            [
                StuckRecovery.BACK,
                StuckRecovery.BACK_SIDE,
                StuckRecovery.AIM_JUMP,
                StuckRecovery.AIM_JUMP,
                StuckRecovery.AIM_JUMP,
            ],
        )
        self.assertEqual(self.task._grid_stuck_recovery_step(4, 5)[1], "脱困尝试 4/5")

    def test_stuck_recovery_logs_configured_total(self):
        """把「卡住脱困次数」调大后，日志要跟着写实际的第几次/共几次。"""
        self.task._choose_grid_strafe_side = lambda position, heading: "d"

        self.task._recover_grid_stuck(
            position=(0.0, 0.0),
            heading=0.0,
            target_bearing=90.0,
            frame=object(),
            attempt=4,
            deadline=self.task.active_time() + 10.0,
            max_attempts=5,
        )

        self.assertIn("脱困尝试 4/5：对准目标后按住 W + Space 跳跃", self.task.logs)

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

    def test_stuck_recovery_uses_compass_right_side(self):
        cells = np.full((21, 21), CELL_FREE, dtype=np.uint8)
        cells[:10, :] = CELL_BLOCKED
        grid = DenseGrid(
            cells,
            GridMeta(origin=(-10.0, 0.0, -10.0), cell_size=1.0),
        )
        self.task._grid_nav_follower = GridRouteFollower(
            grid,
            FollowerConfig(margin=0),
        )

        side = self.task._choose_grid_strafe_side((0.0, 0.0), heading=90.0)

        self.assertEqual(side, "a")

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
        self.task._wait_for_minimap_sync = _record_sync(calls)

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_narrow_entry_stops_calibrates_and_aligns_before_walking(self):
        """窄道入口：先停车校准，再对准入口航点，然后才继续前进。"""
        calls = []
        reasons = []
        confirmed = []

        class _NarrowFollower:
            def __init__(self):
                self.count = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count == 1:
                    return FollowerStep(
                        NARROW_CALIBRATE,
                        waypoint=(2.5, 0.5),
                        target_bearing=90.0,
                        distance_to_waypoint=1.0,
                        distance_to_goal=4.0,
                    )
                return FollowerStep(DONE, distance_to_goal=0.0)

            def confirm_narrow_calibration(self):
                confirmed.append(True)
                return True

        follower = _NarrowFollower()
        self.task._create_grid_route = lambda start, goal, **kwargs: (
            follower,
            PlanResult(ok=True, waypoints=[start, goal]),
        )
        self.task._wait_for_minimap_sync = _record_sync(calls, reasons)

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])
        self.assertEqual(confirmed, [True])
        self.assertIn(90.0, self.task.turns)
        self.assertEqual(reasons, [SyncReason.NARROW_ENTRY.label])

    def test_narrow_entry_recalibrates_when_alignment_fails(self):
        """对准失败时不能标记已校准，必须重新校准并对准。"""
        sync_calls = []
        confirmed = []

        class _NarrowFollower:
            def __init__(self):
                self.count = 0
                self.failures = 0

            def pause(self):
                pass

            def update(self, position, heading, now):
                self.count += 1
                if self.count <= 2:
                    return FollowerStep(
                        NARROW_CALIBRATE,
                        waypoint=(2.5, 0.5),
                        target_bearing=90.0,
                        distance_to_waypoint=1.0,
                        distance_to_goal=4.0,
                    )
                return FollowerStep(DONE, distance_to_goal=0.0)

            def confirm_narrow_calibration(self):
                confirmed.append(True)
                return True

            def narrow_calibration_failed(self):
                self.failures += 1
                return self.failures

        follower = _NarrowFollower()
        self.task._create_grid_route = lambda start, goal, **kwargs: (
            follower,
            PlanResult(ok=True, waypoints=[start, goal]),
        )
        self.task._wait_for_minimap_sync = _record_sync(sync_calls)
        turn_calls = {"n": 0}

        def turn(target, **kwargs):
            turn_calls["n"] += 1
            self.task.turns.append(float(target))
            if turn_calls["n"] == 1:
                return {"ok": False, "heading": 0.0, "error": 90.0}
            return {"ok": True, "heading": float(target), "error": 0.0}

        self.task.pose_turn_to_bearing = turn

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(len(sync_calls), 2)  # 对准失败后重新校准了一次
        self.assertEqual(confirmed, [True])  # 只在两次都成功后确认

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
        self.task._wait_for_minimap_sync = _record_sync(calls)

        self.assertTrue(self.task.navigate_grid_to((4.5, 0.5), map_id="test"))
        self.assertEqual(calls, [0])

    def test_untrusted_position_calibrates_before_first_plan(self):
        calls = []
        self.task.position_trusted = False

        def sync(start_sync_seq, deadline, tick, *, reason=None):
            calls.append(start_sync_seq)
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
        self.task._wait_for_minimap_sync = _record_sync(calls)

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

    def test_unavailable_position_service_fails_cleanly(self):
        self.task._runtime_position_service = None
        self.task._minimap_position_service = None
        self.task.get_task_by_class = lambda cls: None

        result = self.task.navigate_grid_to((1.0, 1.0), timeout=1.0)

        self.assertFalse(result)
        self.assertTrue(any("未注册「小地图定位」" in msg for msg in self.task.logs))

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
        self.assertIn("搜索节点上限", text)  # 指向可调的那个配置项
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


class TestGridNavigationStateTable(unittest.TestCase):
    """状态机的结构约束：每个状态都必须有处理器，动作表必须覆盖跟随器动作。"""

    def test_every_state_has_a_handler(self):
        from src.tasks.navigation.mixin import grid_navigation_mixin as module

        for state in NavState:
            with self.subTest(state=state):
                handler_name = module._GRID_NAV_HANDLERS.get(state)
                self.assertIsNotNone(handler_name, f"{state} 未登记处理器")
                self.assertTrue(
                    callable(getattr(GridNavigationMixin, handler_name, None)),
                    f"{state} 的处理器 {handler_name} 不存在",
                )

    def test_action_table_covers_every_follower_action(self):
        from src.tasks.navigation.mixin import grid_navigation_mixin as module

        for action in (DONE, FAILED, NARROW_CALIBRATE, STUCK, TURN, WAIT, WALK):
            with self.subTest(action=action):
                self.assertIn(action, module._GRID_NAV_ACTION_STATES)

    def test_tail_states_are_the_action_states_without_early_returns(self):
        from src.tasks.navigation.mixin import grid_navigation_mixin as module

        self.assertEqual(
            module._GRID_NAV_TAIL_STATES,
            frozenset(module._GRID_NAV_ACTION_STATES.values()) - {NavState.REPLAN, NavState.ZIP_LINE},
        )


if __name__ == "__main__":
    unittest.main()
