"""离线验证覆盖与时间对齐，禁止测试向游戏发送输入。"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from scripts.nav.path_safety import resolve_under
from scripts.nav.validate_visual_grid import ReferenceRecorder, report_accuracy
from scripts.nav.validation_task import GridValidationTask, pose_scope_status, run_validation_task
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
from src.localization.position_service import WorldPoseService
from src.nav.grid_io import DenseGrid, GridMeta


class TestVisualGridValidation(unittest.TestCase):
    def test_reference_recorder_start_failure_closes_log(self):
        handle = io.StringIO()
        with (
            patch("scripts.nav.validate_visual_grid.Path.open", return_value=handle),
            patch("scripts.nav.validate_visual_grid.threading.Thread.start", side_effect=RuntimeError("start failed")),
            self.assertRaisesRegex(RuntimeError, "start failed"),
        ):
            ReferenceRecorder(8000, Path("unused"))
        self.assertTrue(handle.closed)

    def _observation_task(self):
        grid = DenseGrid(np.ones((4, 4), np.uint8), GridMeta(map_name="map02"))
        reference = {"mapId": "map02", "time": 100, "x": 0, "z": 0, "speed": 1, "flow": 3}
        task = SimpleNamespace(
            stop_reason="",
            validation_failure="",
            absolute=None,
            samples=[],
            sample_log=io.StringIO(),
            _grid_nav_w_held=True,
            covered=set(),
            traversed=set(),
            grid=grid,
            region=grid.cells == 1,
            coverage_steps=0,
            reference=SimpleNamespace(snapshot=Mock(return_value=(reference, 100, [reference]))),
            _stop_inputs=Mock(),
            error_hits=0,
            _validation_cursor=0,
            _observed_frames=set(),
            options=SimpleNamespace(max_error=1),
        )
        task._check_stop = lambda: GridValidationTask._check_stop(task)
        task._process_reference_samples = MethodType(GridValidationTask._process_reference_samples, task)
        pose = {
            "frame_t": 100,
            "map_id": "map02",
            "position_trusted": True,
            "tier_id": "__main__",
            "tier_trusted": True,
            "x": 2,
            "z": 2,
            "frame_time_source": "wgc_system_relative_time",
        }
        return task, pose, reference

    def test_bad_or_unpaired_samples_do_not_validate_coverage(self):
        task, pose, reference = self._observation_task()
        with patch("scripts.nav.validation_task.time.time", return_value=100):
            GridValidationTask._observe(task, pose)
            self.assertEqual(task.error_hits, 1)
            self.assertFalse(task.covered)
            self.assertTrue(task.traversed)
            reference["time"] = 100.4
            GridValidationTask._observe(task, {**pose, "frame_t": 100.1})
            self.assertEqual(task.error_hits, 0)
            self.assertFalse(task.covered)
            reference.update(time=100.2, x=2, z=2)
            GridValidationTask._observe(task, {**pose, "frame_t": 100.2})
            self.assertEqual(task.covered, task.traversed)

    def test_coverage_requires_reference_bracketing_not_just_nearby_time(self):
        for times, valid in (((100.4,), False), ((99.6,), False), ((99.8, 100.2), True)):
            with self.subTest(times=times), patch("scripts.nav.validation_task.time.time", return_value=100.4):
                task, pose, reference = self._observation_task()
                rows = [{**reference, "time": stamp, "x": 2, "z": 2} for stamp in times]
                task.reference.snapshot.return_value = (rows[-1], 100.4, rows)
                GridValidationTask._observe(task, pose)
                self.assertEqual(bool(task.covered), valid)
                self.assertEqual(bool(GridValidationTask._reference_verified_cells(task, rows)), valid)

    def test_late_right_reference_validates_pending_frame_once(self):
        task, pose, reference = self._observation_task()
        reference.update(time=99.9, x=2, z=2)
        with patch("scripts.nav.validation_task.time.time", return_value=100.4):
            GridValidationTask._observe(task, pose)
        self.assertEqual(task._validation_cursor, 0)
        self.assertFalse(task.covered)
        task._process_reference_samples([reference, {**reference, "time": 100.2}])
        self.assertEqual(task.covered, task.traversed)
        self.assertEqual(task._validation_cursor, 1)
        task._process_reference_samples([reference, {**reference, "time": 100.2, "x": 30}])
        self.assertEqual(task.error_hits, 0)  # A consumed observation is never counted twice.

    def test_background_cached_observation_is_validated_once_per_consumer(self):
        task, pose, reference = self._observation_task()
        reference.update(x=2, z=2)
        source = SimpleNamespace(odometry=SimpleNamespace(_sample_min_dt=0.1))
        service = SimpleNamespace(
            _visual_source=source,
            _sampled_visual_state=pose,
            _visual_sampler=object(),
            executor=SimpleNamespace(method=object()),
        )
        timing = SimpleNamespace(frame_time=99.95, source="wgc_system_relative_time")
        with (
            patch("scripts.nav.validation_task.time.time", return_value=100.1),
            patch("src.localization.position_service.timing_for_frame", return_value=timing),
        ):
            cached = WorldPoseService._sample_visual_pose(service, object())
            self.assertTrue(cached["visual_duplicate_frame"])
            GridValidationTask._observe(task, cached)
            GridValidationTask._observe(task, cached)
        self.assertTrue(task.covered)
        self.assertEqual([row["validation_duplicate"] for row in task.samples], [False, True])
        self.assertTrue(all(row["pose"]["visual_duplicate_frame"] for row in task.samples))
        self.assertEqual(report_accuracy(task.samples, [reference])["eligible_frames"], 1)
        self.assertEqual(GridValidationTask._reference_verified_cells(task, [reference]), task.covered)

    def test_repeated_cached_frame_neither_increments_nor_breaks_error_streak(self):
        task, pose, reference = self._observation_task()
        with patch("scripts.nav.validation_task.time.time", return_value=100.4):
            for index in range(2):
                reference["time"] = 100 + index * 0.1
                cached = {**pose, "frame_t": reference["time"], "visual_duplicate_frame": True}
                for _ in range(3):
                    GridValidationTask._observe(task, cached)
                self.assertEqual(task.error_hits, index + 1)
            reference["time"] = 100.2
            with self.assertRaisesRegex(RuntimeError, "sustained_position_error"):
                GridValidationTask._observe(task, {**cached, "frame_t": 100.2})
        self.assertEqual(task.error_hits, 3)

    def test_late_reference_batch_can_supply_both_bracketing_points(self):
        task, pose, reference = self._observation_task()
        reference.update(time=98, x=2, z=2)
        with patch("scripts.nav.validation_task.time.time", return_value=100.4):
            GridValidationTask._observe(task, pose)
        self.assertEqual(task._validation_cursor, 0)
        task._process_reference_samples([{**reference, "time": 99.9}, {**reference, "time": 100.2}])
        self.assertEqual(task.covered, task.traversed)

    def test_late_reference_triggers_error_stop_in_original_frame_order(self):
        task, pose, reference = self._observation_task()
        reference["time"] = 99.9
        with patch("scripts.nav.validation_task.time.time", return_value=100.4):
            for stamp in (100, 100.1, 100.2):
                GridValidationTask._observe(task, {**pose, "frame_t": stamp})
            self.assertEqual(task.error_hits, 0)
            right = {**reference, "time": 100.3}
            task.reference.snapshot.return_value = (right, 100.4, [reference, right])
            with self.assertRaisesRegex(RuntimeError, "sustained_position_error"):
                GridValidationTask._observe(task, {**pose, "frame_t": 100.25})
        self.assertEqual(task.error_hits, 3)
        self.assertEqual(task._validation_cursor, 3)
        self.assertFalse(task.covered)
        task._stop_inputs.assert_called_once()

    def test_invalid_frame_breaks_error_streak_even_while_waiting_for_reference(self):
        task, pose, reference = self._observation_task()
        reference["time"] = 99.9
        with patch("scripts.nav.validation_task.time.time", return_value=100.4):
            for index in range(4):
                GridValidationTask._observe(
                    task, {**pose, "frame_t": 100 + index * 0.1, "position_trusted": index != 1}
                )
        task._process_reference_samples([reference, {**reference, "time": 100.4}])
        self.assertEqual(task.error_hits, 2)
        self.assertEqual(task._validation_cursor, 4)
        self.assertFalse(task.stop_reason)

    def test_unusable_samples_break_sustained_error_streak(self):
        for invalid in (
            {"position_trusted": False},
            {"tier_id": None, "tier_trusted": False},
        ):
            with self.subTest(invalid=invalid), patch("scripts.nav.validation_task.time.time", return_value=100):
                task, pose, reference = self._observation_task()

                def observe(index, overrides=None, reference=reference, task=task, pose=pose):
                    reference["time"] = 100 + index * 0.01
                    GridValidationTask._observe(task, {**pose, "frame_t": reference["time"], **(overrides or {})})

                observe(0)
                observe(1)
                observe(2, invalid)
                self.assertEqual(task.error_hits, 0)
                observe(3)
                observe(4)
                with self.assertRaisesRegex(RuntimeError, "sustained_position_error"):
                    observe(5)
                self.assertFalse(task.covered)

    def test_reference_coverage_requires_known_main_floor_and_accurate_visual_pose(self):
        task, pose, _ = self._observation_task()
        pose.update(x=0.2, z=0)
        row = {"frame_t": 10.1, "flow": 3, "stationary_reference": False, "pose": pose}
        references = [self.record(10), self.record(10.4, 0.8)]
        for invalid in (
            {"tier_id": None, "tier_trusted": False},
            {"tier_id": "floor1"},
            {"map_id": "map01"},
            {"visual_duplicate_frame": True},
            {"x": 20},
        ):
            with self.subTest(invalid=invalid):
                task.samples = [{**row, "pose": {**pose, **invalid}}]
                self.assertFalse(GridValidationTask._reference_verified_cells(task, references))
        task.samples = [row]
        self.assertTrue(GridValidationTask._reference_verified_cells(task, references))

    def test_recovery_timeout_at_max_attempt_is_not_an_obstacle(self):
        for attempts, now, obstructed in ((3, 0, True), (2, 20, False)):
            with self.subTest(attempts=attempts):
                task = object.__new__(GridValidationTask)
                task._grid_nav_follower = None
                task._set_grid_walking = Mock()
                task._grid_cfg_int = Mock(return_value=3)
                task.log_warning = Mock()
                task.active_time = Mock(return_value=now)
                task._recover_grid_stuck = Mock()
                ctx = SimpleNamespace(
                    session=SimpleNamespace(recoveries=attempts, deadline=10, limit=10),
                    step=SimpleNamespace(reason="stuck", waypoint=(1, 1), target_bearing=0),
                    position=(0, 0),
                    heading=0,
                    frame=None,
                )
                self.assertFalse(task._grid_nav_state_stuck(ctx))
                self.assertEqual(task.navigation_failure["recoveries"], 3)
                self.assertEqual(task.navigation_failure["obstructed"], obstructed)

    def _run_task(self, root):
        task = SimpleNamespace(
            stop_reason="",
            _watch=Mock(),
            _stop_inputs=Mock(),
            log_warning=Mock(),
            log_info=Mock(),
            _save_obstacles=Mock(),
            ensure_runtime_position_service=Mock(),
            next_frame=Mock(),
            navigate_grid_to=Mock(),
        )
        for name in ("_validation_resources", "_reference_verified_cells", "_process_reference_samples", "_check_stop"):
            setattr(task, name, MethodType(getattr(GridValidationTask, name), task))
        options = SimpleNamespace(
            resume_covered=[],
            resume_traversed=[],
            resume_failures=[],
            resume_skipped=[],
            capture_fallback=False,
            skip_blocked=False,
            spacing=1,
            seconds=60,
            port=8000,
            mode="record",
            activate_game=False,
            max_error=1,
        )
        grid = DenseGrid(np.ones((2, 2), np.uint8), GridMeta(map_name="map02"))
        task.validation = (options, grid, grid.cells == 1, [(0, 0)], root, {})
        service = SimpleNamespace(config={}, sample_world_pose=Mock(), _locate_visual_frame=Mock())
        task.get_runtime_position_service = Mock(return_value=service)
        return task, service

    def test_initialization_failures_close_acquired_resources_and_write_report(self):
        for stage in ("reference", "service", "guard"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                task, service = self._run_task(Path(directory))
                original_config, original_sample, original_locate = (
                    service.config,
                    service.sample_world_pose,
                    service._locate_visual_frame,
                )
                reference = SimpleNamespace(close=Mock(), snapshot=Mock(return_value=(None, 0, [])))
                guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
                if stage == "service":
                    task.get_runtime_position_service.side_effect = RuntimeError("service failed")
                if stage == "guard":
                    guard.start.side_effect = RuntimeError("guard failed")
                with (
                    patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference) as factory,
                    patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                    patch("scripts.nav.validation_task.time.sleep"),
                ):
                    if stage == "reference":
                        factory.side_effect = RuntimeError("reference failed")
                    self.assertFalse(GridValidationTask.run.__wrapped__(task))
                self.assertTrue(task.sample_log.closed)
                self.assertTrue(task.capture_log.closed)
                self.assertEqual(reference.close.call_count, int(stage != "reference"))
                self.assertIs(service.config, original_config)
                self.assertIs(service.sample_world_pose, original_sample)
                self.assertIs(service._locate_visual_frame, original_locate)
                report = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
                self.assertIn(f"{stage} failed", report["reason"])
                self.assertEqual(report["status"], "stopped")

    def test_record_mode_collects_failed_frames_beyond_navigation_ready_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            task, _service = self._run_task(Path(directory))
            clock = SimpleNamespace(now=0)
            task.sleep = lambda _: setattr(clock, "now", clock.now + 10)

            def observe(**kwargs):
                pose = {"position_trusted": False, "trust_reason": "no_fix"}
                task.samples.append({"frame_t": clock.now, "flow": 3, "stationary_reference": False, "pose": pose})
                return pose

            task.world_pose = observe
            reference = SimpleNamespace(close=Mock(), snapshot=Mock(return_value=(None, 0, [])))
            guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
            with (
                patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference),
                patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                patch("scripts.nav.validation_task.time.sleep"),
                patch("scripts.nav.validation_task.time.monotonic", side_effect=lambda: clock.now),
            ):
                GridValidationTask.run.__wrapped__(task)
            self.assertEqual(task.report["reason"], "time_limit")
            self.assertEqual(task.report["frames"], 6)
            self.assertEqual(task.report["elapsed_s"], 60)
            task.navigate_grid_to.assert_not_called()

    def test_final_reference_flush_updates_report_resume_and_error_protection(self):
        for coordinate, stopped, stop_request in (
            (0.5, False, ""),
            (1.5, True, ""),
            (0.5, False, "time_limit"),
            (1.5, True, "time_limit"),
        ):
            with (
                self.subTest(coordinate=coordinate, stop_request=stop_request),
                tempfile.TemporaryDirectory() as directory,
            ):
                task, _service = self._run_task(Path(directory))
                task._grid_nav_w_held = True
                clock = SimpleNamespace(now=0)
                task.sleep = lambda _, clock=clock: setattr(clock, "now", clock.now + 10)
                records = [{"mapId": "map02", "time": 99.9, "x": 0.5, "z": 0.5, "flow": 3, "speed": 1}]

                def observe(task=task, coordinate=coordinate, **kwargs):
                    pose = {
                        "frame_t": 100 + len(task.samples) * 0.05,
                        "frame_time_source": "wgc_system_relative_time",
                        "map_id": "map02",
                        "tier_id": "__main__",
                        "tier_trusted": True,
                        "position_trusted": True,
                        "x": coordinate,
                        "z": coordinate,
                    }
                    GridValidationTask._observe(task, pose)
                    return pose

                task.world_pose = observe

                def close_reference(records=records, task=task, stop_request=stop_request):
                    task.stop_reason = stop_request
                    records.append({**records[0], "time": 100.4})

                reference = SimpleNamespace(
                    snapshot=lambda records=records: (records[-1], 100.4, list(records)),
                    close=close_reference,
                )
                guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
                with (
                    patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference),
                    patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                    patch("scripts.nav.validation_task.time.sleep"),
                    patch("scripts.nav.validation_task.time.time", return_value=100.4),
                    patch("scripts.nav.validation_task.time.monotonic", side_effect=lambda clock=clock: clock.now),
                ):
                    GridValidationTask.run.__wrapped__(task)
                saved = json.loads((Path(directory) / "covered_cells.json").read_text(encoding="utf-8"))
                self.assertEqual(task.report["covered_cells"], len(saved))
                self.assertEqual(task.report["stop_request"], stop_request)
                self.assertEqual(bool(task.report["validation_failure"]), stopped)
                if stopped:
                    self.assertEqual(task.report["status"], "stopped")
                    self.assertIn("sustained_position_error", task.report["reason"])
                    self.assertFalse(saved)
                else:
                    self.assertTrue(saved)
                    self.assertEqual(task.report["visual_unavailable_cells"], 0)

    def test_record_exit_code_rejects_validation_failure_despite_time_limit(self):
        for failure, expected in (("", 0), ("sustained_position_error: 2.00m", 1)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                task = SimpleNamespace(
                    report={
                        "status": "stopped",
                        "reason": "time_limit",
                        "frames": 3,
                        "validation_failure": failure,
                    }
                )
                runtime = Mock()
                runtime.get_onetime_task.return_value = task
                with (
                    patch("ok.OK", return_value=runtime),
                    patch("src.localization.wgc_frame_timing.install_wgc_frame_timing"),
                    patch("src.patches.position_service_patch.install_position_service_patch"),
                ):
                    self.assertEqual(
                        run_validation_task(
                            SimpleNamespace(mode="record"),
                            None,
                            None,
                            [],
                            Path(directory),
                            {},
                        ),
                        expected,
                    )
                runtime.quit.assert_called_once()
                runtime.task_executor.destroy.assert_called_once()

    def test_reference_map_change_stops_even_without_usable_visual_pose(self):
        for pose in (
            {"position_trusted": True, "tier_id": None, "tier_trusted": False},
            {"position_trusted": False},
            {"position_trusted": True, "visual_duplicate_frame": True},
        ):
            for reference_map in ("map02", "map01"):
                with self.subTest(pose=pose, reference_map=reference_map):
                    task = SimpleNamespace(
                        stop_reason="",
                        absolute={},
                        samples=[],
                        sample_log=io.StringIO(),
                        _grid_nav_w_held=False,
                        covered=set(),
                        traversed=set(),
                        grid=SimpleNamespace(meta=SimpleNamespace(map_name="map02")),
                        reference=SimpleNamespace(snapshot=Mock(return_value=({"mapId": reference_map}, 100, None))),
                        _stop_inputs=Mock(),
                        error_hits=0,
                        _validation_cursor=0,
                        _observed_frames=set(),
                        validation_failure="",
                    )
                    task._check_stop = lambda task=task: GridValidationTask._check_stop(task)
                    task._process_reference_samples = MethodType(GridValidationTask._process_reference_samples, task)
                    state = {"frame_t": 100, "map_id": "map02", "frame_time_source": "wgc_system_relative_time", **pose}
                    with patch("scripts.nav.validation_task.time.time", return_value=100):
                        if reference_map == "map01":
                            with self.assertRaisesRegex(RuntimeError, "map_or_tier_changed"):
                                GridValidationTask._observe(task, state)
                            task._stop_inputs.assert_called_once()
                        else:
                            GridValidationTask._observe(task, state)
                            task._stop_inputs.assert_not_called()
                    self.assertEqual(len(task.samples), 1)
                    self.assertFalse(task.covered or task.traversed)

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
