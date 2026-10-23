"""Unit tests for the pure grid route follower."""

import unittest

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, CELL_UNKNOWN, DenseGrid, GridMeta
from src.nav.grid_planner import PlanResult
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
    GridRouteFollower,
    bearing_to_point,
    point_segment_distance,
)
from src.nav.zip_line_graph import ZipLineGraph, ZipLineLink, ZipLineNode


def _line_grid(size: int = 11) -> DenseGrid:
    cells = np.full((size, size), CELL_FREE, dtype=np.uint8)
    return DenseGrid(cells, GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0, map_name="test"))


class TestBearing(unittest.TestCase):
    """覆盖方位角与点到线段距离。"""

    def test_cardinal_directions(self):
        self.assertAlmostEqual(bearing_to_point(0.0, 0.0, 0.0, 1.0), 0.0)
        self.assertAlmostEqual(bearing_to_point(0.0, 0.0, 1.0, 0.0), 90.0)
        self.assertAlmostEqual(bearing_to_point(0.0, 0.0, 0.0, -1.0), 180.0)
        self.assertAlmostEqual(bearing_to_point(0.0, 0.0, -1.0, 0.0), 270.0)

    def test_point_segment_distance(self):
        distance, ratio = point_segment_distance((2.0, 3.0), (0.0, 0.0), (4.0, 0.0))
        self.assertAlmostEqual(distance, 3.0)
        self.assertAlmostEqual(ratio, 0.5)


class TestGridRouteFollower(unittest.TestCase):
    """覆盖跟随器状态机和偏航迟滞。"""

    def setUp(self):
        self.follower = GridRouteFollower(
            _line_grid(),
            FollowerConfig(
                arrive_radius=0.5,
                goal_radius=0.5,
                heading_tolerance=4.0,
                stuck_window_s=2.0,
                stuck_min_distance=0.3,
                margin=0,
            ),
        )

    def test_walks_and_reaches_goal(self):
        result = self.follower.plan((0.5, 0.5), (4.5, 0.5))
        self.assertTrue(result.ok, result)

        first = self.follower.update((0.5, 0.5), heading=90.0, now=0.0)
        self.assertEqual(first.action, WALK)
        self.assertAlmostEqual(first.target_bearing, 90.0)

        second = self.follower.update((1.0, 0.5), heading=90.0, now=0.5)
        self.assertEqual(second.action, WALK)

        done = self.follower.update((4.2, 0.5), heading=90.0, now=1.0)
        self.assertEqual(done.action, DONE)
        self.assertAlmostEqual(done.distance_to_goal, 0.3)

    def test_turns_before_walking(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        step = self.follower.update((0.5, 0.5), heading=0.0, now=0.0)
        self.assertEqual(step.action, TURN)
        self.assertAlmostEqual(step.target_bearing, 90.0)
        self.assertAlmostEqual(step.heading_error, 90.0)

    def test_missing_heading_waits(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        step = self.follower.update((0.5, 0.5), heading=None, now=0.0)
        self.assertEqual(step.action, WAIT)
        self.assertEqual(step.reason, "朝向不可用")

    def test_detects_stuck_without_progress(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=90.0, now=0.0).action,
            WALK,
        )
        step = self.follower.update((0.5, 0.5), heading=90.0, now=2.1)
        self.assertEqual(step.action, STUCK)

    def test_pause_resets_stuck_window(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=90.0, now=0.0).action,
            WALK,
        )

        self.follower.pause()
        step = self.follower.update((0.5, 0.5), heading=90.0, now=2.1)

        self.assertEqual(step.action, WALK)

    def test_heading_tolerance_is_fixed_at_four_degrees(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))

        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=86.0, now=0.0).action,
            WALK,
        )
        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=85.9, now=0.1).action,
            TURN,
        )

    def test_off_route_requests_replan(self):
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 10), (10, 10)],
            waypoints=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
        )

        first = self.follower.update((0.0, 10.0), heading=0.0, now=0.0)
        self.assertNotEqual(first.action, REPLAN)

        step = self.follower.update((0.0, 10.0), heading=0.0, now=1.6)

        self.assertEqual(step.action, REPLAN)
        self.assertIn("偏离路径", step.reason)

    def test_initial_waypoint_advance_does_not_look_ahead_to_second_segment(self):
        """起点已靠近并跳过第一个航点时，不能拿后续长航段判定偏航。"""
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(-818, 41), (-822, 39), (-823, 37)],
            waypoints=[(-818.5, 41.5), (-822.5, 39.5), (-823.5, 37.5)],
        )
        self.follower.waypoint_index = 1

        step = self.follower.update((-818.739, 41.731), heading=239.3, now=0.0)

        self.assertNotEqual(step.action, REPLAN)

    @staticmethod
    def _corner_follower(blocking_state, *, allow_unknown=False):
        cells = np.full((6, 6), CELL_FREE, dtype=np.uint8)
        cells[2, 2] = blocking_state
        grid = DenseGrid(cells, GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0, map_name="test"))
        follower = GridRouteFollower(
            grid,
            FollowerConfig(
                arrive_radius=1.0, goal_radius=0.1, allow_unknown=allow_unknown, margin=0, narrow_calibration=False
            ),
        )
        follower.adopt(
            PlanResult(
                ok=True, cells=[(1, 1), (1, 2), (1, 3), (2, 3), (3, 3)], waypoints=[(1.5, 1.5), (1.5, 3.5), (3.5, 3.5)]
            ),
            goal=(3.5, 3.5),
            start=(1.5, 1.5),
        )
        return follower

    def test_near_corner_keeps_reachable_waypoint_without_replanning(self):
        for state, allow_unknown in ((CELL_BLOCKED, False), (CELL_UNKNOWN, False), (CELL_UNKNOWN, True)):
            with self.subTest(state=state, allow_unknown=allow_unknown):
                follower = self._corner_follower(state, allow_unknown=allow_unknown)
                step = follower.update((1.5, 2.5), heading=0.0, now=0.0)
                self.assertEqual(step.action, WALK)
                self.assertEqual(step.waypoint, (1.5, 3.5))
                self.assertEqual(follower.waypoint_index, 1)
                self.assertTrue(follower.planner.line_clear((1, 2), (1, 3)))
                step = follower.update((1.5, 3.5), heading=90.0, now=0.1)
                self.assertEqual(step.action, WALK)
                self.assertEqual(step.waypoint, (3.5, 3.5))
                self.assertEqual(follower.waypoint_index, 2)

    def test_initial_waypoint_advance_preserves_blocked_corner(self):
        for state in (CELL_BLOCKED, CELL_UNKNOWN):
            with self.subTest(state=state):
                follower = self._corner_follower(state)
                follower._advance_initial_waypoints((1.5, 2.5))
                self.assertEqual(follower.waypoint_index, 1)
                follower._advance_initial_waypoints((1.5, 3.5))
                self.assertEqual(follower.waypoint_index, 2)

    def test_clear_corner_still_advances_inside_arrival_radius(self):
        follower = self._corner_follower(CELL_FREE)
        pos, goal = (1.5, 2.5), (3.5, 3.5)
        step = follower.update(pos, heading=bearing_to_point(*pos, *goal), now=0.0)
        self.assertEqual(step.action, WALK)
        self.assertEqual(step.waypoint, goal)
        self.assertEqual(follower.waypoint_index, 2)

    def test_long_initial_segment_does_not_trigger_false_off_route(self):
        """复现日志中的 48m 误判：起点仍属于上一航点所在段落。"""
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(-822, 38), (-828, -9), (-844, -10)],
            waypoints=[(-822.5, 38.5), (-828.5, -9.5), (-844.5, -10.5)],
        )
        self.follower.waypoint_index = 1

        step = self.follower.update((-822.152, 38.426), heading=270.0, now=0.0)

        self.assertNotEqual(step.action, REPLAN)

    def test_snapped_route_start_does_not_trigger_false_off_route(self):
        """复现日志中的 4.97m 误判：规划器把起点吸附到 5 格外的自由格。"""
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(1821, 797), (1821, 796), (1822, 796)],
            waypoints=[(-828.5, 5.5), (-827.5, 3.5), (-827.5, -8.5)],
        )
        self.follower._route_start = (-833.464, 5.084)

        step = self.follower.update((-833.464, 5.084), heading=90.0, now=0.0)

        self.assertNotEqual(step.action, REPLAN)

    def test_snapped_goal_reports_failure_instead_of_arrival(self):
        """终点不可通行被吸附到 7m 外时，走到吸附点不等于到达请求目标。"""
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (3, 0)],
            waypoints=[(0.5, 0.5), (3.5, 0.5)],
        )
        self.follower.goal = (10.5, 0.5)

        step = self.follower.update((3.5, 0.5), heading=90.0, now=0.0)

        self.assertEqual(step.action, FAILED)
        self.assertIn("吸附", step.reason)
        self.assertAlmostEqual(step.distance_to_goal, 7.0)

    def test_snapped_goal_within_radius_still_reports_arrival(self):
        """吸附偏移仍在到达半径内时保持原行为：报到达而不是失败。"""
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (3, 0)],
            waypoints=[(0.5, 0.5), (3.5, 0.5)],
        )
        self.follower.goal = (3.9, 0.5)

        step = self.follower.update((3.5, 0.5), heading=90.0, now=0.0)

        self.assertEqual(step.action, DONE)

    def test_skips_current_waypoint_when_near_next_segment(self):
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 10), (10, 10)],
            waypoints=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
        )
        self.follower.waypoint_index = 1

        step = self.follower.update((9.5, 3.0), heading=0.0, now=0.0)

        self.assertEqual(step.skipped_waypoints, (1,))
        self.assertEqual(step.waypoint_index, 2)
        self.assertEqual(step.waypoint, (10.0, 10.0))
        self.assertAlmostEqual(step.shortcut_distance, 0.5)

    def test_does_not_skip_when_far_from_next_segment(self):
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 10), (10, 10)],
            waypoints=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
        )
        self.follower.waypoint_index = 1

        step = self.follower.update((8.0, 0.0), heading=90.0, now=0.0)

        self.assertEqual(step.skipped_waypoints, ())
        self.assertEqual(step.waypoint, (10.0, 0.0))

    def test_reports_intermediate_waypoint_arrival(self):
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 2), (0, 4)],
            waypoints=[(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)],
        )
        self.follower.waypoint_index = 1

        step = self.follower.update((2.0, 0.0), heading=90.0, now=0.0)

        self.assertEqual(step.arrived_waypoint_index, 1)
        self.assertEqual(step.waypoint, (4.0, 0.0))

    def test_step_reports_planned_leg_length_not_remaining_distance(self):
        """``leg_distance_m`` 是这一段**规划**的长度，不随接近航点而变小。

        任务层据此决定长航段要不要冲刺；如果它报的是剩余距离，冲刺就会在快到航点时
        被反复判定，和"这一段值不值得冲刺"完全不是一回事。
        """
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 2), (0, 6)],
            waypoints=[(0.0, 0.0), (2.0, 0.0), (6.0, 0.0)],
        )
        self.follower.waypoint_index = 2

        far = self.follower.update((2.0, 0.0), heading=90.0, now=0.0)
        near = self.follower.update((5.8, 0.0), heading=90.0, now=0.5)

        self.assertAlmostEqual(far.leg_distance_m, 4.0)
        self.assertAlmostEqual(near.leg_distance_m, 4.0)
        self.assertLess(near.distance_to_waypoint, far.distance_to_waypoint)

    def test_first_waypoint_falls_back_to_remaining_distance(self):
        """首航点没有"上一段"，退回剩余距离——与搬迁前任务层的取值一致。

        如果这里报 0，长首段就再也不会触发冲刺了，属于悄悄改行为。
        """
        self.follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 6)],
            waypoints=[(4.0, 0.0), (6.0, 0.0)],
        )
        self.follower.waypoint_index = 0

        step = self.follower.update((1.0, 0.0), heading=90.0, now=0.0)

        self.assertEqual(step.waypoint_index, 0)
        self.assertAlmostEqual(step.leg_distance_m, 3.0)
        self.assertAlmostEqual(step.leg_distance_m, step.distance_to_waypoint)


class TestNarrowPassageCalibration(unittest.TestCase):
    """一格宽通路入口必须先在进入前停车校准，再对准航点前进。"""

    def _follower(self) -> GridRouteFollower:
        follower = GridRouteFollower(
            _line_grid(),
            FollowerConfig(
                arrive_radius=0.5,
                goal_radius=0.5,
                narrow_calibration=True,
                narrow_stop_radius=3.0,
            ),
        )
        follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 5)],
            waypoints=[(0.5, 0.5), (0.5, 5.5)],
            narrow_entries=(0,),
        )
        return follower

    def test_stops_to_calibrate_before_entering_narrow_passage(self):
        step = self._follower().update((0.5, 2.8), 180.0, now=0.0)
        self.assertEqual(step.action, NARROW_CALIBRATE)
        self.assertAlmostEqual(step.target_bearing, 180.0)

    def test_confirmation_suppresses_repeat_calibration(self):
        follower = self._follower()
        self.assertEqual(follower.update((0.5, 2.8), 180.0, now=0.0).action, NARROW_CALIBRATE)
        self.assertTrue(follower.confirm_narrow_calibration())
        step = follower.update((0.5, 2.8), 180.0, now=0.2)
        self.assertNotEqual(step.action, NARROW_CALIBRATE)

    def test_far_from_entry_keeps_walking(self):
        step = self._follower().update((0.5, 4.5), 180.0, now=0.0)
        self.assertNotEqual(step.action, NARROW_CALIBRATE)

    def test_disabled_calibration_never_triggers(self):
        follower = self._follower()
        follower.config = FollowerConfig(
            arrive_radius=0.5,
            goal_radius=0.5,
            narrow_calibration=False,
        )
        step = follower.update((0.5, 2.8), 180.0, now=0.0)
        self.assertNotEqual(step.action, NARROW_CALIBRATE)

    def test_uncalibrated_entry_is_not_shortcut_away(self):
        follower = GridRouteFollower(
            _line_grid(),
            FollowerConfig(arrive_radius=0.5, goal_radius=0.5, shortcut_radius=1.0),
        )
        follower.plan_result = PlanResult(
            ok=True,
            cells=[(0, 0), (0, 5), (0, 10)],
            waypoints=[(0.5, 0.5), (0.5, 5.5), (0.5, 10.5)],
            narrow_entries=(1,),
        )
        follower.waypoint_index = 1
        step = follower.update((0.5, 9.5), 180.0, now=0.0)
        self.assertEqual(follower.waypoint_index, 1)
        self.assertEqual(step.waypoint, (0.5, 5.5))

    def test_failed_calibration_can_be_retried(self):
        follower = self._follower()
        self.assertEqual(follower.narrow_calibration_failed(), 1)
        self.assertEqual(follower.narrow_calibration_failed(), 2)
        # 未确认前仍会继续触发窄道校准
        self.assertEqual(follower.update((0.5, 2.8), 180.0, now=0.0).action, NARROW_CALIBRATE)
        self.assertTrue(follower.confirm_narrow_calibration())
        self.assertNotEqual(
            follower.update((0.5, 2.8), 180.0, now=0.2).action,
            NARROW_CALIBRATE,
        )


class TestZipLineRouteFollower(unittest.TestCase):
    """验证滑索航点会生成独立动作，而不是被当成普通步行线段。"""

    def test_returns_zip_line_action_and_advances_after_completion(self):
        cells = np.full((1, 6), CELL_FREE, dtype=np.uint8)
        cells[0, 2] = CELL_BLOCKED
        grid = DenseGrid(
            cells,
            GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0, map_name="test"),
        )
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=4.0, max_range_m=80.0)],
        )
        follower = GridRouteFollower(
            grid,
            FollowerConfig(
                arrive_radius=0.5,
                goal_radius=0.5,
                margin=0,
            ),
            zip_lines=graph,
        )

        result = follower.plan((0.5, 0.5), (4.5, 0.5))
        self.assertTrue(result.ok, result)

        step = follower.update((0.5, 0.5), heading=90.0, now=0.0)
        self.assertEqual(step.action, ZIP_LINE)
        self.assertIsNotNone(step.zip_line_step)
        self.assertEqual(step.zip_line_step.step.link_id, "a->b")

        self.assertTrue(follower.complete_zip_line())
        done = follower.update((4.5, 0.5), heading=90.0, now=1.0)
        self.assertEqual(done.action, DONE)


if __name__ == "__main__":
    unittest.main()
