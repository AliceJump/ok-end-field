"""离线验证覆盖与时间对齐，禁止测试向游戏发送输入。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts.nav.path_safety import resolve_under
from scripts.nav.validate_visual_grid import report_accuracy
from scripts.nav.validation_task import pose_scope_status
from scripts.nav.visual_validation import (
    NavigationSourcePolicy,
    align_reference,
    connected_region,
    coverage_targets,
    error_statistics,
    navigation_pose_ready,
    nearby_cells,
    temporary_obstacle_grid,
)
from src.nav.grid_io import DenseGrid, GridMeta


class TestVisualGridValidation(unittest.TestCase):
    def test_unknown_tier_waits_but_confirmed_other_tier_stops(self):
        base = {"map_id": "map02", "tier_evidence_required": True, "position_trusted": True}
        self.assertEqual(pose_scope_status({**base, "tier_id": None, "tier_trusted": False}, "map02"), "tier_pending")
        self.assertEqual(
            pose_scope_status({**base, "tier_id": "__main__", "tier_trusted": True}, "map02"),
            "record",
        )
        self.assertEqual(
            pose_scope_status({**base, "tier_id": "floor1", "tier_trusted": True}, "map02"),
            "tier_changed",
        )
        self.assertEqual(
            pose_scope_status({**base, "map_id": "map01", "tier_id": "__main__", "tier_trusted": True}, "map02"),
            "map_changed",
        )

    def test_cli_paths_cannot_escape_repository_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            inside = root / "logs" / "report.json"
            inside.parent.mkdir()
            self.assertEqual(resolve_under(root, inside, label="output"), inside.resolve())
            self.assertEqual(
                resolve_under(root, "logs/report.json", label="patch", relative_to_root=True),
                inside.resolve(),
            )
            with self.assertRaisesRegex(ValueError, "output_escapes_allowed_root"):
                resolve_under(root, root.parent / "outside.json", label="output")

    def test_backend_failure_does_not_create_output_directory(self):
        from scripts.nav import validate_visual_grid

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grid = root / "assets/nav-grid-data/map02_4.grid.npz"
            grid.parent.mkdir(parents=True)
            grid.write_bytes(b"placeholder")
            output = root / "logs" / "validation"
            argv = ["validate_visual_grid.py", "--output", str(output)]
            with (
                patch.object(validate_visual_grid, "ROOT", root),
                patch.object(validate_visual_grid.CapturePositionSource, "poll", return_value=None),
                patch.object(sys, "argv", argv),
                self.assertRaisesRegex(RuntimeError, "reference_backend_has_no_live_position"),
            ):
                validate_visual_grid.main()
            self.assertFalse(output.exists())

    def test_capture_fallback_requires_explicit_enable_and_stable_recovery(self):
        good = {"position_trusted": True, "tier_evidence_required": True, "tier_trusted": True}
        bad = {**good, "position_trusted": False}
        disabled = NavigationSourcePolicy(False)
        self.assertEqual([disabled.choose(bad) for _ in range(5)], ["visual_map"] * 5)
        enabled = NavigationSourcePolicy(True)
        self.assertEqual([enabled.choose(bad) for _ in range(3)], ["visual_map", "visual_map", "capture"])
        self.assertEqual([enabled.choose(good) for _ in range(3)], ["capture", "capture", "visual_map"])
        self.assertEqual(enabled.choose(bad), "visual_map")

    def test_capture_coordinates_never_count_as_visual_accuracy(self):
        row = {
            "frame_t": 10.1,
            "flow": 3,
            "pose": {"source": "capture", "position_trusted": True, "map_id": "map02", "x": 0.2, "z": 0},
            "absolute": {"world_x": 0.2, "world_z": 0},
        }
        report = report_accuracy([row], [self.record(10), self.record(10.4, 0.8)])
        self.assertEqual(report["eligible_frames"], 0)
        self.assertEqual(report["absolute_time_aligned"], {"count": 0})

    def test_navigation_waits_for_tier_evidence_without_classifying_obstacles(self):
        pose = {"position_trusted": True, "tier_evidence_required": True, "tier_trusted": False}
        self.assertFalse(navigation_pose_ready(pose))
        self.assertTrue(navigation_pose_ready({**pose, "tier_trusted": True}))
        self.assertFalse(navigation_pose_ready({**pose, "tier_trusted": True, "position_trusted": False}))
        self.assertFalse(navigation_pose_ready(None))

    def test_temporary_obstacle_preserves_source_and_retreat_space(self):
        from src.nav.grid_planner import GridPlanner

        grid = DenseGrid(np.ones((12, 12), np.uint8), GridMeta())
        failure = {"position": (3.5, 5.5), "goal": (9.5, 5.5), "waypoint": (7.5, 5.5), "obstructed": True}
        temporary = temporary_obstacle_grid(grid, [failure])
        self.assertTrue(np.all(grid.cells == 1))
        self.assertTrue(temporary.is_free(*temporary.index_of_world(3.5, 5.5)))
        self.assertTrue(temporary.is_free(*temporary.index_of_world(2.5, 5.5)))
        self.assertTrue(temporary.is_blocked(*temporary.index_of_world(6.5, 5.5)))
        result = GridPlanner(temporary, allow_unknown=False).plan((3.5, 5.5), (9.5, 5.5))
        self.assertTrue(result.ok)
        self.assertTrue(all(temporary.is_free(*cell) for cell in result.cells))

    def test_unconfirmed_timeout_does_not_change_walkability(self):
        grid = DenseGrid(np.ones((12, 12), np.uint8), GridMeta())
        temporary = temporary_obstacle_grid(grid, [{"position": (3.5, 5.5), "goal": (7.5, 5.5)}])
        np.testing.assert_array_equal(temporary.cells, grid.cells)

    def record(self, stamp, x=0, **kwargs):
        return {"time": stamp, "x": x, "z": 0, "mapId": "map02", "flow": 3, "scene": 234, "speed": 2, **kwargs}

    def test_start_component_excludes_islands_unknowns_and_diagonal_corners(self):
        grid = DenseGrid(np.array([[1, 0, 1], [1, 2, 0], [0, 0, 1]], np.uint8), GridMeta())
        region, excluded = connected_region(grid, (0.5, 0.5))
        self.assertEqual(int(region.sum()), 2)
        self.assertEqual(excluded, 2)
        with self.assertRaisesRegex(ValueError, "start_not_on_free_grid"):
            connected_region(grid, (1.5, 0.5))

    def test_targets_cover_winding_corridors_without_counting_across_walls(self):
        cells = np.zeros((17, 17), np.uint8)
        cells[1::3, 1:-1] = 1
        cells[1:14, 1] = 1
        cells[1:14, -2] = 1
        grid = DenseGrid(cells, GridMeta())
        region, _ = connected_region(grid, (1.5, 1.5))
        targets = coverage_targets(grid, region, 5)
        covered = set()
        for target in targets:
            self.assertTrue(grid.is_free(*target))
            covered.update(nearby_cells(region, target, 5))
        self.assertEqual(covered, set(map(tuple, np.argwhere(region).tolist())))
        self.assertNotIn((4, 8), nearby_cells(region, (1, 8), 5))

    def test_fractional_cell_size_uses_world_spacing(self):
        grid = DenseGrid(np.ones((18, 18), np.uint8), GridMeta(cell_size=0.5))
        targets = coverage_targets(grid, grid.cells == 1, 2.5)
        covered = set()
        for target in targets:
            covered.update(nearby_cells(grid.cells == 1, target, 5))
        self.assertEqual(len(covered), 324)

    def test_time_interpolation_uses_frame_timestamp_not_result_arrival(self):
        records = [self.record(10, 0), self.record(10.4, 0.8)]
        aligned = align_reference(records, 10.1, "map02", flow=3)
        self.assertAlmostEqual(aligned["x"], 0.2)
        self.assertEqual(aligned["mode"], "interpolated")
        self.assertIsNone(align_reference(records, 10.6, "map02", flow=3))

    def test_never_interpolates_dead_session_map_switch_gap_or_teleport(self):
        for change in ({"flow": 4}, {"mapId": "map01"}, {"scene": 5}, {"time": 12}, {"x": 100}, {"replayed": True}):
            with self.subTest(change=change):
                records = [self.record(10), {**self.record(10.4, 0.8), **change}]
                self.assertIsNone(align_reference(records, 10.2, "map02", flow=3))

    def test_stale_stationary_value_is_explicit_and_never_used_while_moving(self):
        records = [self.record(10, 2, speed=0)]
        self.assertIsNone(align_reference(records, 90, "map02", flow=3))
        held = align_reference(records, 90, "map02", flow=3, stationary=True)
        self.assertEqual(held["mode"], "stationary_hold")
        self.assertEqual(held["gap_s"], 80)
        self.assertIsNone(align_reference([self.record(10, speed=2)], 90, "map02", flow=3, stationary=True))

    def test_statistics_report_bias_without_calibrating_it_away(self):
        result = error_statistics([(3, 4), (3, 4)])
        self.assertEqual((result["median_m"], result["rmse_m"], result["bias_x_m"], result["bias_z_m"]), (5, 5, 3, 4))
        self.assertEqual(error_statistics([]), {"count": 0})

    def test_later_movement_does_not_discard_earlier_confirmed_stationary_samples(self):
        records = [self.record(10, speed=0), self.record(100, x=1, speed=2)]
        held = align_reference(records, 90, "map02", flow=3, stationary=True)
        self.assertEqual(held["mode"], "stationary_hold")
        self.assertIsNone(align_reference(records, 99.8, "map02", flow=3, stationary=True))

    def test_untrusted_and_duplicate_frames_do_not_inflate_accuracy(self):
        pose = {"position_trusted": True, "map_id": "map02", "x": 0.2, "z": 0}
        good = {"frame_t": 10.1, "flow": 3, "pose": pose, "absolute": {"world_x": 0.2, "world_z": 0}}
        rows = [
            good,
            {**good, "pose": {**pose, "visual_duplicate_frame": True}},
            {**good, "pose": {**pose, "position_trusted": False}},
        ]
        report = report_accuracy(rows, [self.record(10), self.record(10.4, 0.8)])
        self.assertEqual(report["eligible_frames"], 1)
        self.assertEqual(report["unpaired_frames"], 0)
