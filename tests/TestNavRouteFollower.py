"""Unit tests for the pure grid route follower."""

import unittest

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, DenseGrid, GridMeta
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import (
    DONE,
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
                heading_tolerance=5.0,
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

    def test_heading_hysteresis_avoids_turn_walk_flapping(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))

        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=85.5, now=0.0).action,
            WALK,
        )
        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=80.0, now=0.1).action,
            WALK,
        )
        self.assertEqual(
            self.follower.update((0.5, 0.5), heading=79.0, now=0.2).action,
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
