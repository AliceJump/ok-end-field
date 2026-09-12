"""Unit tests for the pure grid route follower."""

import unittest

import numpy as np

from src.nav.grid_io import CELL_FREE, DenseGrid, GridMeta
from src.nav.grid_planner import PlanResult
from src.nav.route_follower import (
    DONE,
    STUCK,
    TURN,
    WAIT,
    WALK,
    FollowerConfig,
    GridRouteFollower,
    bearing_to_point,
    point_segment_distance,
)


def _line_grid(size: int = 11) -> DenseGrid:
    cells = np.full((size, size), CELL_FREE, dtype=np.uint8)
    return DenseGrid(cells, GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0, map_name="test"))


class TestBearing(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
