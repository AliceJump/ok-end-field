"""Unit tests for the pure grid route follower."""

import unittest

import numpy as np

from src.nav.coasting import retrace_step
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
from src.nav.walk_feedback import (
    ForwardInput,
    LegObservation,
    MeasuredTrail,
    RecoveryEpisode,
    WalkFeedback,
    boundary_probe,
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

    def _smooth_route(self, *, end=(9.5, 5.5), narrow_entries=()):
        follower = GridRouteFollower(
            _line_grid(), FollowerConfig(arrive_radius=0.5, goal_radius=0.5, margin=0, allow_unknown=True)
        )
        follower.plan_result = PlanResult(
            ok=True, waypoints=[(0.5, 5.5), (4.5, 5.5), end], narrow_entries=narrow_entries
        )
        follower.waypoint_index = 1
        return follower

    def test_smooth_visible_waypoint_is_looked_through_before_near_stop_zone(self):
        follower = self._smooth_route()
        step = follower.update((1.5, 5.5), 85, now=0)
        self.assertEqual(step.skipped_waypoints, (1,))
        self.assertEqual(step.waypoint, (9.5, 5.5))
        self.assertEqual(step.action, TURN)
        self.assertEqual(step.distance_to_waypoint, 8)

    def test_lookahead_preserves_sharp_corner(self):
        follower = self._smooth_route(end=(4.5, 9.5))
        step = follower.update((1.5, 5.5), 85, now=0)
        self.assertEqual(step.waypoint_index, 1)
        self.assertEqual(step.skipped_waypoints, ())

    def test_lookahead_does_not_approach_unconfirmed_narrow_entry_early(self):
        follower = self._smooth_route(narrow_entries=(2,))
        step = follower.update((1.5, 5.5), 85, now=0)
        self.assertEqual(step.waypoint_index, 1)

    def test_lookahead_respects_blocked_cells_and_unknown_risk(self):
        for state in (CELL_BLOCKED, CELL_UNKNOWN):
            with self.subTest(state=state):
                follower = self._smooth_route(end=(9.5, 6.5))
                planner = follower.planner
                start, anchor, end = (5, 1), (5, 4), (6, 9)
                only_direct = set(planner._line_cells(start, end)) - set(planner._line_cells(anchor, end))
                cell = next(c for c in only_direct if c != start)
                follower.grid.cells[cell] = state
                step = follower.update((1.5, 5.5), 85, now=0)
                self.assertEqual(step.waypoint_index, 1)

    def test_disabling_shortcuts_disables_smooth_lookahead(self):
        follower = self._smooth_route()
        follower.config = FollowerConfig(shortcut_radius=0)
        step = follower.update((1.5, 5.5), 85, now=0)
        self.assertEqual(step.waypoint_index, 1)

    def test_lookahead_retains_goal_arrival_radius(self):
        follower = self._smooth_route()
        follower.update((1.5, 5.5), 90, now=0)
        self.assertEqual(follower.update((8.8, 5.5), 90, now=1).action, WALK)
        self.assertEqual(follower.update((9.2, 5.5), 90, now=2).action, DONE)

    def _coasting_route(self, size=45, narrow_entries=()):
        points = [(5.5, 10.5), (12.5, 11.5), (30.5, 10.5)]
        follower = GridRouteFollower(_line_grid(size), FollowerConfig(goal_radius=0.5, margin=0))
        follower.adopt(
            PlanResult(ok=True, waypoints=points, narrow_entries=narrow_entries), start=points[0], goal=points[-1]
        )
        follower.coast_horizon = 12
        follower.moving_steer = (0.8, 25, 45)
        return follower

    def test_heading_can_coast_past_a_bend_without_a_small_correction(self):
        follower = self._coasting_route()
        step = follower.update((5.5, 10.5), 90, 0)
        self.assertEqual(step.action, WALK)
        self.assertEqual(step.coast_distance, 12)
        self.assertEqual(step.waypoint_index, 2)
        self.assertEqual(step.skipped_waypoints, (1,))
        self.assertEqual(follower.update((5.5, 10.5), 90, 3).action, STUCK)

    def test_coasting_cannot_cross_blocked_or_extra_unknown_cells(self):
        for cell in (CELL_BLOCKED, CELL_UNKNOWN):
            follower = self._coasting_route()
            # The projected straight line cuts below the planned bend.
            follower.grid.cells[10, 12] = cell
            step = follower.update((5.5, 10.5), 90, 0)
            self.assertEqual(step.coast_distance, 0)

    def test_coasting_stops_before_narrow_or_terminal_boundaries(self):
        follower = self._coasting_route(narrow_entries=(1,))
        self.assertEqual(follower.update((5.5, 10.5), 90, 0).coast_distance, 0)
        follower = self._coasting_route()
        follower.waypoint_index = 2
        self.assertEqual(follower.update((27.5, 10.5), 90, 0).coast_distance, 0)

    def test_moving_steer_requires_clearance_between_initial_and_target_headings(self):
        follower = self._coasting_route()
        step = follower.update((5.5, 10.5), 65, 0)
        self.assertEqual(step.action, TURN)
        self.assertEqual(step.coast_distance, 12)
        follower.grid.cells[12, 10] = CELL_BLOCKED
        self.assertEqual(follower.update((5.5, 10.5), 65, 1).coast_distance, 0)

    def test_passed_vertex_requires_forward_projection_and_visibility(self):
        follower = self._coasting_route()
        follower.plan_result.waypoints = [(5.5, 10.5), (12.5, 10.5), (12.5, 30.5)]
        step = follower.update((14.0, 13.5), 0, 0)
        self.assertEqual(step.skipped_waypoints, (1,))
        follower.waypoint_index = 1
        follower.grid.cells[19, 13] = CELL_BLOCKED
        step = follower.update((14.0, 13.5), 0, 1)
        self.assertEqual(step.waypoint_index, 1)

    def test_requested_subcell_position_controls_arrival(self):
        follower = self._coasting_route()
        follower.waypoint_index = 2
        follower.goal = (30.1, 10.5)
        step = follower.update((30.9, 10.5), 270, 0)
        self.assertEqual(step.action, WALK)
        self.assertEqual(step.waypoint, follower.goal)
        self.assertEqual(follower.update((30.4, 10.5), 270, 1).action, DONE)

    def test_visible_handoff_tracks_the_chord_after_skipping_vertices(self):
        follower = self._coasting_route()
        follower.plan_result.waypoints = [(5.5, 10.5), (12.5, 10.5), (12.5, 30.5)]
        pos = (11.0, 10.5)
        step = follower.update(pos, 0, 0)
        self.assertEqual(step.skipped_waypoints, (1,))
        self.assertEqual(follower._off_route_distance(pos), 0)
        self.assertIsNotNone(follower._handoff_leg)
        follower.adopt(follower.plan_result, start=pos, goal=(12.5, 30.5))
        self.assertIsNone(follower._handoff_leg)

    def test_landing_uses_verified_approach_only_before_ordinary_vertices(self):
        follower = self._coasting_route()
        follower.coast_horizon = 0
        follower.plan_result.waypoints = [(5.5, 10.5), (12.5, 10.5), (12.5, 30.5)]
        pos = (9.5, 10.5)
        step = follower.update(pos, 90, 0)
        self.assertEqual(step.action, WALK)
        self.assertAlmostEqual(follower.landing_distance(pos, 90, step), 2.5)
        follower.grid.cells[10, 11] = CELL_BLOCKED
        self.assertIsNone(follower.landing_distance(pos, 90, step))
        follower.grid.cells[10, 11] = CELL_FREE
        follower.plan_result.narrow_entries = (1,)
        self.assertIsNone(follower.landing_distance(pos, 90, step))

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

    def test_stall_uses_forward_input_and_along_leg_progress(self):
        w = ForwardInput()
        w.edge(True, 10.0)
        w.edge(False, 10.3)
        w.intervals.append((11.2, 11.4))
        self.assertAlmostEqual(w.seconds(10.1, 11.3), 0.3)
        w.edge(True, 12)
        self.assertAlmostEqual(w.seconds(11.3, 14.5), 2.6)
        feedback = WalkFeedback()

        def observe(t, x, z, seconds):
            sample = LegObservation.measure((0, 0), (0, 20), (x, z))
            feedback.observe(sample, t, seconds, 0.35, 2.5)

        observe(10, 0, 3, 0)
        observe(11, 1, 3, 0.3)
        observe(11, 1, 3, 0.3)
        self.assertEqual(feedback.exposure, 0.3)
        observe(13, 3, 3, 2)
        self.assertAlmostEqual(feedback.exposure, 2.3)
        observe(13.5, 3, 4, 0.5)
        self.assertEqual(feedback.exposure, 0)

    def test_converging_turn_can_wait_for_a_certified_position_frame(self):
        f = GridRouteFollower(_line_grid(50), FollowerConfig(margin=0))
        f.plan((20.5, 3.5), (20.5, 45.5))
        f.coast_horizon, f.moving_steer = 10, (0.8, 25, 45)
        f.input_sample = (10, 0.3, 4.0)
        f.update((19.3, 12.5), 13, 10)
        f.feedback.command = (10, 10, 14)
        f.input_sample = (10.4, 0.4, 4.0)
        step = f.update((19.6, 14.5), 13, 10.4)
        self.assertEqual(step.action, WALK)
        self.assertGreaterEqual(step.coast_distance, 4)
        self.assertEqual(step.target_bearing, 13)
        f.pause()
        self.assertIsNone(f.feedback.command)

    def test_recovery_requires_two_new_frames_past_the_same_directed_site(self):
        e = RecoveryEpisode("map", "floor", (10, 10), (0, 1), after=12)
        self.assertTrue(e.matches("map", "floor", (12, 11), (0, 1)))
        self.assertFalse(e.matches("map", "floor", (12, 11), (0, -1)))
        self.assertFalse(e.matches("map", "other", (10, 10), (0, 1)))
        self.assertFalse(e.passed((10, 13), 11))
        self.assertFalse(e.passed((13, 10), 12.1))
        self.assertFalse(e.passed((10, 13), 12.4))
        self.assertFalse(e.passed((10, 13), 12.4))
        self.assertTrue(e.passed((10, 13), 12.8))

    def test_short_starts_require_separate_post_release_frames_and_reset_on_progress(self):
        f = WalkFeedback()

        def observe(stamp, x):
            f.observe(LegObservation.measure((0, 0), (10, 0), (x, 0)), stamp, 0.12, 0.35, 2.5)

        observe(10, 1)
        for n in range(3):
            stamp = 10.5 + n * 0.5
            f.short_start = (1, 0), stamp
            observe(stamp - 0.05, 1)
            self.assertEqual(f.short_misses, n)
            observe(stamp + 0.2, 1.03)
        self.assertEqual(f.short_misses, 3)
        observe(12.5, 2)
        self.assertEqual(f.short_misses, 0)

    def test_retrace_follows_measured_positions_across_multiple_rejected_cells(self):
        grid = _line_grid(20)
        grid.cells[5, 5:14] = CELL_BLOCKED
        trail = MeasuredTrail()
        for i, x in enumerate((4.7, 5.6, 6.4, 7.1, 8.2, 9.4, 10.3, 11.1, 12.2)):
            trail.record(("map", "floor"), (x, 5.5), 10 + i * 0.4, 1)
        path = trail.path_to_known(grid)
        self.assertEqual(path[-1], (4.7, 5.5))
        keys, distance = retrace_step((12.2, 5.5), 90, path, 1, 1.5)
        self.assertEqual(keys, ("s",))
        self.assertAlmostEqual(distance, 1.5)
        trail.record(("map", "other"), (12.2, 5.5), 14, 1)
        self.assertFalse(trail.path_to_known(grid))
        self.assertFalse(trail.return_path(grid, (12.2, 5.5)))
        trail.record(("map", "other"), (11.2, 5.5), 14.4, 1)
        self.assertEqual(trail.return_path(grid, (11.2, 5.5)), [(11.2, 5.5), (12.2, 5.5)])

    def test_cold_boundary_probe_requires_a_reachable_adjacent_known_cell(self):
        grid = _line_grid()
        grid.cells[4:7, 4:7] = CELL_BLOCKED
        f = GridRouteFollower(grid, FollowerConfig(margin=0))
        episode = RecoveryEpisode("map", "floor", (5.1, 5.5), (1, 0))
        self.assertIsNone(boundary_probe(f.planner, (5.1, 5.5), 90, episode, 1.5, {}))
        grid.cells[5, 4] = CELL_FREE
        keys, distance = boundary_probe(f.planner, (5.1, 5.5), 90, episode, 1.5, {})
        self.assertEqual(keys, ("s",))
        self.assertAlmostEqual(distance, 0.6)
        self.assertIn((5, 4), episode.tried_exits)

    def test_corner_handoff_needs_clearance_on_both_legs(self):
        grid = _line_grid(20)
        grid.cells[7, 6] = CELL_BLOCKED
        f = GridRouteFollower(grid, FollowerConfig(margin=0, waypoint_tolerance=2))
        points = [(1.5, 5.5), (5.5, 5.5), (9.5, 9.5), (13.5, 9.5)]
        f.adopt(PlanResult(True, "ok", waypoints=points), goal=points[-1], start=points[0])
        f.waypoint_index = 2
        f.input_sample = 10, 0.2, 4
        f.coast_horizon = 10
        h = f._corner_exit_distance((5.6, 5.8), 90, 2, 3)
        self.assertGreater(h, 0)
        self.assertLessEqual(h, 2)
        self.assertEqual(f._corner_exit_distance((5.6, 5.8), 90, 2, 2), 0)
        grid.cells[5, 7] = CELL_BLOCKED
        self.assertEqual(f._corner_exit_distance((5.6, 5.8), 90, 2, 3), 0)

    def test_recovery_penalty_changes_the_route_but_keeps_cells_passable(self):
        f = GridRouteFollower(_line_grid(24), FollowerConfig(margin=0))
        a, b = (2.5, 10.5), (19.5, 10.5)
        initial = f.plan(a, b)
        f.planner.avoid_stalled_approach((10.5, 10.5), (1, 0))
        plan = f.plan(a, b)
        self.assertTrue(plan.ok)
        self.assertNotEqual(initial.cells, plan.cells)
        self.assertTrue(f.planner.passable(10, 10))
        self.assertFalse(any(cell in f.planner.recovery_cost for cell in plan.cells))
        self.assertGreater(f.planner._fast_line((10, 2), (10, 19))[1], 17)

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

    def test_repeated_turns_with_w_detect_obstacle_instead_of_resetting_stuck(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        pos = (0.5, 0.5)
        self.assertEqual(self.follower.update(pos, heading=80, now=0).action, TURN)
        self.follower.note_turn_movement(pos, now=0)
        self.assertEqual(self.follower.update(pos, heading=82, now=1).action, TURN)
        self.follower.note_turn_movement(pos, now=1)
        step = self.follower.update(pos, heading=84, now=2.1)
        self.assertEqual(step.action, STUCK)

    def test_slow_stopped_samples_do_not_spend_turn_stuck_window(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        pos = (0.5, 0.5)
        self.follower.update(pos, heading=80, now=0)
        self.follower.note_turn_movement(pos, now=0)
        self.follower.exclude_stopped_time(4)
        self.assertEqual(self.follower.update(pos, heading=80, now=4.1).action, TURN)
        self.follower.note_turn_movement(pos, now=4.1)
        self.assertEqual(self.follower.update(pos, heading=80, now=6.1).action, STUCK)

    def test_turns_without_movement_input_do_not_count_as_stuck(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        for now in (0, 1, 3):
            self.assertEqual(self.follower.update((0.5, 0.5), heading=80, now=now).action, TURN)

    def test_turn_movement_progress_and_pause_reset_stuck_detection(self):
        self.follower.plan((0.5, 0.5), (4.5, 0.5))
        self.follower.note_turn_movement((0.5, 0.5), now=0)
        self.assertEqual(self.follower.update((1, 0.5), heading=80, now=3).action, TURN)
        self.follower.pause()
        self.assertEqual(self.follower.update((1, 0.5), heading=80, now=6).action, TURN)

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
