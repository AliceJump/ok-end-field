"""离线验证覆盖与时间对齐，禁止测试向游戏发送输入。"""

import io
import json
import sys
import tempfile
import threading
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
    ReferenceTimeline,
    align_reference,
    connected_region,
    coverage_targets,
    error_statistics,
    navigation_pose_ready,
    nearby_cells,
    temporary_obstacle_grid,
)
from src.localization.capture_position_source import CapturePositionSource
from src.localization.position_service import WorldPoseService
from src.nav.grid_io import DenseGrid, GridMeta


class TestVisualGridValidation(unittest.TestCase):
    def test_reference_identity_matches_capture_resolver_without_replacing_raw_fields(self):
        recorder = ReferenceRecorder.__new__(ReferenceRecorder)
        recorder.source = CapturePositionSource(8000)
        for raw, state, origin in (
            ({"mapId": None, "scene": 102}, {}, "manual_table"),
            ({"mapId": None, "scene": 999}, {"ws": {"lastPosition": {"mapId": "map02"}}}, "backend_ws"),
            ({"mapId": "map02", "levelId": "floor"}, {}, "scene_table"),
        ):
            with self.subTest(source=origin):
                row = recorder._with_identity({**raw, "time": 100, "x": 2, "z": 2}, state)
                self.assertEqual(row["mapId"], raw["mapId"])
                self.assertEqual(row["resolved_map_id"], "map02")
                self.assertEqual(row["map_id_source"], origin)
                self.assertIsNotNone(align_reference([row], 100, "map02"))

    def test_observer_uses_resolved_reference_identity(self):
        task, pose, reference = self._observation_task()
        reference.update(mapId=None, resolved_map_id="map02", map_id_source="backend_ws", x=2, z=2)
        with patch("scripts.nav.validation_task.time.time", return_value=100.1):
            GridValidationTask._observe(task, pose)
        self.assertFalse(task.stop_reason)
        self.assertTrue(task.covered)

    def test_late_left_reference_replays_provisional_coverage_and_error_streak(self):
        for bad in (False, True):
            with self.subTest(bad=bad):
                task, pose, reference = self._observation_task()
                task.covered = {(0, 0)}  # Preserve already verified coverage from a previous run.
                task.samples = [
                    {"pose": pose, "frame_t": stamp, "flow": 3, "stationary_reference": False}
                    for stamp in (100.0, 100.1, 100.2)
                ]
                reference.update(x=0 if bad else 2, z=0 if bad else 2)
                timeline = ReferenceTimeline([{**reference, "time": 100.3}])
                task._process_reference_samples(timeline)
                self.assertEqual(task._validation_cursor, 3)
                self.assertFalse(task.validation_failure)
                timeline.extend([{**reference, "time": 99.9}])
                task._process_reference_samples(timeline)
                self.assertEqual(bool(task.validation_failure), bad)
                self.assertEqual((2, 2) in task.covered, not bad)
                self.assertIn((0, 0), task.covered)

    def test_reference_recorder_start_failure_closes_log(self):
        handle = io.StringIO()
        with (
            patch("scripts.nav.validate_visual_grid.Path.open", return_value=handle),
            patch("scripts.nav.validate_visual_grid.threading.Thread.start", side_effect=RuntimeError("start failed")),
            self.assertRaisesRegex(RuntimeError, "start failed"),
        ):
            ReferenceRecorder(8000, Path("unused"))
        self.assertTrue(handle.closed)

    def test_reference_recorder_cursor_returns_only_new_records(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.nav.validate_visual_grid.threading.Thread.start"),
        ):
            recorder = ReferenceRecorder(8000, Path(directory))
            with recorder.handle:
                recorder._ordered_records.extend(self.record(10 + i) for i in range(1000))
                rows = recorder.snapshot()[2]
                self.assertEqual(len(rows), 1000)
                recorder._ordered_records.append(self.record(1010))
                self.assertEqual(recorder.snapshot(since=len(rows))[2], [self.record(1010)])
                self.assertEqual(recorder.snapshot(since=1001)[2], [])
                self.assertEqual(recorder.snapshot(since=None)[2], [])

    def _observation_task(self):
        grid = DenseGrid(np.ones((4, 4), np.uint8), GridMeta(map_name="map02"))
        reference = {"mapId": "map02", "time": 100, "x": 0, "z": 0, "speed": 1, "flow": 3}
        task = SimpleNamespace(
            stop_reason="",
            validation_failure="",
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
            _reference_cursor=0,
            _reference_timeline=ReferenceTimeline([]),
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
            reference["time"] = 100.8
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
        self.assertEqual(task.error_hits, 0)  # The revised reference implies a teleport and cannot be scored.
        self.assertFalse(task.covered)

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

    def test_absolute_match_stays_with_cached_frame_after_later_background_results(self):
        task, pose, reference = self._observation_task()
        reference.update(x=2, z=2)
        references = [reference, {**reference, "time": 100.2}]
        recorder = SimpleNamespace(snapshot=Mock(return_value=(reference, 100.4, references)), close=Mock())
        tracker = SimpleNamespace(
            update=Mock(
                side_effect=[
                    SimpleNamespace(position=SimpleNamespace(to_dict=lambda: {"world_x": 2, "world_z": 2})),
                    SimpleNamespace(position=SimpleNamespace(to_dict=lambda: {"world_x": 9, "world_z": 9})),
                    SimpleNamespace(position=None),
                ]
            )
        )
        original_update = tracker.update

        def locate(_frame, stamp, _timing):
            tracker.update()
            return {**pose, "frame_t": stamp}

        service = SimpleNamespace(
            config={},
            sample_world_pose=Mock(),
            _locate_visual_frame=locate,
            _visual_source=SimpleNamespace(tracker=tracker, odometry=SimpleNamespace(_sample_min_dt=0.1)),
            _visual_sampler=object(),
            executor=SimpleNamespace(method=object()),
        )
        task.get_runtime_position_service = Mock(return_value=service)
        task.guard_done, task._watch = threading.Event(), Mock()
        task.options.port = 8000
        guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
        timing = SimpleNamespace(frame_time=99.95, source="wgc_system_relative_time")
        with tempfile.TemporaryDirectory() as directory:
            task.directory = Path(directory)
            with (
                patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=recorder),
                patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                patch("scripts.nav.validation_task.time.sleep"),
                patch("scripts.nav.validation_task.time.time", return_value=100.4),
                patch("src.localization.position_service.timing_for_frame", return_value=timing),
                GridValidationTask._validation_resources(task),
            ):
                service._sampled_visual_state = service._locate_visual_frame(object(), 100, None)
                cached = WorldPoseService._sample_visual_pose(service, object())
                newer = service._locate_visual_frame(object(), 100.1, None)
                missed = service._locate_visual_frame(object(), 100.2, None)
                for state in (cached, newer, missed):
                    GridValidationTask._observe(task, state)
        self.assertTrue(cached["visual_duplicate_frame"])
        self.assertEqual(
            [row["absolute"] for row in task.samples],
            [
                {"world_x": 2, "world_z": 2},
                {"world_x": 9, "world_z": 9},
                None,
            ],
        )
        self.assertTrue(all("_validation_absolute" not in row["pose"] for row in task.samples))
        report = report_accuracy(task.samples[:1], references)
        self.assertEqual(report["absolute_time_aligned"]["median_m"], 0)
        self.assertIs(tracker.update, original_update)
        self.assertIs(service._locate_visual_frame, locate)

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

    def test_navigation_pose_does_not_count_unconfirmed_visual_tier_as_traversed(self):
        task, pose, _ = self._observation_task()
        task.source_policy = NavigationSourcePolicy(False)
        task.route_source, task.route_token, task.route_seq = "visual_map", None, 0
        task._observe, task._write_progress = Mock(), Mock()
        service = SimpleNamespace(runtime_state_hub=Mock())
        pose.update(tier_trusted=False, tier_evidence_required=True, sample_t=100)
        with patch("scripts.nav.validation_task.publish_world_pose"):
            GridValidationTask._navigation_pose(task, service, pose)
            self.assertFalse(task.traversed)
            GridValidationTask._navigation_pose(task, service, {**pose, "tier_trusted": True})
        self.assertTrue(task.traversed)

    def test_replay_uses_runtime_visual_width_and_preserves_timestamp(self):
        from scripts import replay_world_pose

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "samples.jsonl").write_text(
                "".join(json.dumps({"frame": "frame.png", "frame_t": t}) + "\n" for t in (123.5, 124.0, 124.5)),
                encoding="utf-8",
            )
            frame = np.zeros((1080, 1920, 3), np.uint8)
            source = SimpleNamespace(
                reset=Mock(), sample=Mock(return_value={"position_trusted": False, "trust_reason": "no_fix"})
            )
            store = SimpleNamespace(load=Mock(return_value=("map02", object())))
            with (
                patch.object(
                    replay_world_pose.cv2,
                    "imread",
                    side_effect=[frame, np.zeros((720, 1280, 3), np.uint8), np.zeros((720, 1280, 3), np.uint8)],
                ),
                patch.object(replay_world_pose, "VisualPositionSource", return_value=source),
                patch("builtins.print"),
            ):
                replay_world_pose.replay(root, store, root / "output")
            self.assertEqual(source.sample.call_args.args[0].shape, (720, 1280, 3))
            self.assertEqual([c.kwargs["now"] for c in source.sample.call_args_list], [123.5, 124.0, 124.5])
            source.reset.assert_called_once()
            self.assertTrue(all(c.args[0].shape == (720, 1280, 3) for c in source.sample.call_args_list))

    def test_reference_readiness_timeout_closes_resources_before_service_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            task, service = self._run_task(Path(directory))
            original_config = service.config
            reference = SimpleNamespace(
                wait_ready=Mock(return_value=False), close=Mock(), snapshot=Mock(return_value=(None, 0, []))
            )
            with (
                patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference),
                patch("scripts.nav.validation_task.time.sleep"),
            ):
                self.assertFalse(GridValidationTask.run.__wrapped__(task))
            reference.wait_ready.assert_called_once_with(2.0)
            reference.close.assert_called_once()
            self.assertTrue(task.sample_log.closed)
            self.assertTrue(task.capture_log.closed)
            self.assertIs(service.config, original_config)
            task.get_runtime_position_service.assert_not_called()
            self.assertIn("reference_initial_sample_timeout", task.report["reason"])

    def test_resume_revisits_traversed_cells_without_visual_coverage_once_per_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, _ = self._run_task(root)
            options, grid, region, _, _, _ = task.validation
            options.mode, options.grid = "run", root / "grid.npz"
            options.resume_traversed = list(map(tuple, np.argwhere(region).tolist()))
            task.world_pose = Mock(return_value={"position_trusted": True, "x": 0.5, "z": 0.5})
            from src.nav.grid_planner import GridPlanner

            task.navigation_grid, task.target_planner = grid, GridPlanner(grid, allow_unknown=False)
            reference = SimpleNamespace(close=Mock(), snapshot=Mock(return_value=(None, 0, [])))
            guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
            with (
                patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference),
                patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                patch("scripts.nav.validation_task.time.sleep"),
            ):
                GridValidationTask.run.__wrapped__(task)
            goals = [c.args[0] for c in task.navigate_grid_to.call_args_list]
            self.assertEqual(len(goals), len(set(goals)))
            self.assertEqual(len(goals), 4)
            self.assertFalse(task.covered)
            self.assertEqual(task.report["status"], "completed_with_visual_gaps")

    def test_transient_navigation_failure_retries_the_same_target_three_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task, _ = self._run_task(root)
            options, grid, _, _, _, _ = task.validation
            options.mode, options.grid, options.skip_blocked = "run", root / "grid.npz", True
            # One uncovered target remains; a transient failure must not mark it tested.
            options.resume_covered = [(0, 0), (0, 1), (1, 0)]
            task.world_pose = Mock(return_value={"position_trusted": True, "x": 0.5, "z": 0.5})
            task.navigate_grid_to.return_value = False
            task.sleep = Mock()
            from src.nav.grid_planner import GridPlanner

            task.navigation_grid, task.target_planner = grid, GridPlanner(grid, allow_unknown=False)
            reference = SimpleNamespace(close=Mock(), snapshot=Mock(return_value=(None, 0, [])))
            guard = SimpleNamespace(ident=None, start=Mock(), join=Mock())
            with (
                patch("scripts.nav.validate_visual_grid.ReferenceRecorder", return_value=reference),
                patch("scripts.nav.validation_task.threading.Thread", return_value=guard),
                patch("scripts.nav.validation_task.time.sleep"),
            ):
                GridValidationTask.run.__wrapped__(task)
            self.assertEqual(task.navigate_grid_to.call_count, 3)
            self.assertEqual({c.args[0] for c in task.navigate_grid_to.call_args_list}, {(1.5, 1.5)})
            self.assertEqual(len(task.navigation_failures), 3)
            self.assertIn("navigation_unavailable", task.report["reason"])

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
                    snapshot=lambda records=records, since=0: (records[-1], 100.4, records[since:]),
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
                        reference=SimpleNamespace(snapshot=Mock(return_value=({"mapId": reference_map}, 100, []))),
                        _stop_inputs=Mock(),
                        error_hits=0,
                        _validation_cursor=0,
                        _reference_cursor=0,
                        _reference_timeline=ReferenceTimeline([]),
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

    def test_expired_retained_frame_can_trigger_explicit_capture_fallback(self):
        policy = NavigationSourcePolicy(True)
        good = {"position_trusted": True, "frame_t": 100}
        for _ in range(3):
            self.assertEqual(policy.choose(good), "visual_map")
        self.assertEqual(policy.good, 1)
        stale = {**good, "position_trusted": False, "trust_reason": "visual_result_too_old"}
        self.assertEqual([policy.choose(stale) for _ in range(3)], ["visual_map", "visual_map", "capture"])
        for i in range(3):
            selected = policy.choose({**good, "frame_t": 101 + i})
        self.assertEqual(selected, "visual_map")

    def test_numeric_reference_strings_are_normalized_without_mutating_input(self):
        records = [
            {**self.record(10), "time": "10", "x": "3", "z": "4"},
            {**self.record(10.4), "time": "10.4", "x": "5", "z": "6"},
        ]
        timeline = ReferenceTimeline(records)
        exact = align_reference(timeline, 10, "map02")
        between = align_reference(timeline, 10.2, "map02")
        self.assertEqual((exact["x"], exact["z"]), (3, 4))
        self.assertAlmostEqual(between["x"], 4)
        self.assertAlmostEqual(between["z"], 5)
        self.assertEqual(records[0]["x"], "3")

    def test_reference_recorder_waits_for_the_first_sampling_attempt(self):
        entered, release = threading.Event(), threading.Event()
        source = Mock()

        def fetch():
            entered.set()
            release.wait(2)
            return {"lastPosition": self.record(100)}

        source.fetch_state.side_effect = fetch
        source._flow_liveness.return_value = (None, None, True)
        source._resolve_map_id.return_value = ("map02", "test")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.nav.validate_visual_grid.CapturePositionSource", return_value=source),
        ):
            recorder = ReferenceRecorder(8000, Path(directory))
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(recorder.wait_ready(0.01))
                release.set()
                self.assertTrue(recorder.wait_ready(1))
                self.assertIsNotNone(recorder.snapshot()[0])
            finally:
                release.set()
                recorder.close()

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

    def test_incremental_reference_index_validates_only_new_records_and_keeps_stable_order(self):
        records = [self.record(10), self.record(10.4, 0.8)]
        timeline = ReferenceTimeline(records)
        incoming = [self.record(10.2, 0.4), self.record(10.4, 1), self.record(10.6, 1.2), {"time": 0}]
        from scripts.nav.visual_validation import valid_reference

        with patch("scripts.nav.visual_validation.valid_reference", wraps=valid_reference) as check:
            timeline.extend(incoming)
        self.assertEqual(check.call_count, len(incoming))
        expected = ReferenceTimeline([*records, *incoming])
        self.assertEqual(timeline.samples, expected.samples)
        self.assertEqual(timeline.times, expected.times)

    def test_source_switch_counts_invalid_polls_but_requires_distinct_good_frames(self):
        policy = NavigationSourcePolicy(True)
        invalid = {"frame_t": 1, "position_trusted": False}
        self.assertEqual([policy.choose(invalid) for _ in range(3)], ["visual_map", "visual_map", "capture"])
        for index in range(3):
            pose = {"frame_t": index + 2, "position_trusted": True, "visual_duplicate_frame": True}
            for _ in range(4):
                self.assertEqual(policy.choose(pose), "visual_map" if index == 2 else "capture")

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
