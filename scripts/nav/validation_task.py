"""通过项目正式任务、定位服务与网格导航执行精度验证。"""

import copy
import json
import math
import threading
import time
from contextlib import ExitStack, contextmanager

import cv2
import numpy as np

from scripts.nav.visual_validation import (
    NavigationSourcePolicy,
    ReferenceTimeline,
    align_reference,
    connected_region,
    duplicate_observation,
    navigation_pose_ready,
    nearby_cells,
    reference_map_id,
    temporary_obstacle_grid,
)
from src.core.GridNavConfig import (
    CONFIG_GRID_ALLOW_UNKNOWN,
    CONFIG_GRID_FILE,
    CONFIG_GRID_USE_ZIP_LINES,
)
from src.core.PositionConfig import CONFIG_EXPERIMENTAL_PORT, CONFIG_USE_EXPERIMENTAL
from src.nav.grid_io import save_grid
from src.nav.route_follower import GridRouteFollower
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.navigation.MinimapNavigateToPoint import MinimapNavigateToPoint


def pose_scope_status(state, map_id):
    """Classify whether a visual pose is usable for this main-map validation."""

    if state.get("map_id") != map_id:
        return "map_changed"
    tier_id = state.get("tier_id")
    tier_trusted = bool(state.get("tier_trusted"))
    tier_required = bool(state.get("tier_evidence_required", True))
    if tier_required and (not tier_trusted or tier_id is None):
        return "tier_pending"
    if tier_trusted and tier_id not in (None, "__main__"):
        return "tier_changed"
    return "record"


def coverage_pose_ready(state, map_id):
    """Require an independent, timestamped visual observation in the validation scope."""
    return (
        state.get("position_trusted")
        and state.get("frame_time_source") == "wgc_system_relative_time"
        and pose_scope_status(state, map_id) == "record"
    )


def paired_visual_reference(row, timeline, map_id):
    """Apply the same eligibility and temporal pairing contract online and in the report."""
    if duplicate_observation(row) or not coverage_pose_ready(row["pose"], map_id):
        return None
    return align_reference(timeline, row["frame_t"], map_id, flow=row["flow"], stationary=row["stationary_reference"])


class GridValidationTask(MinimapNavigateToPoint):
    validation = None
    stop_reason = ""
    validation_failure = ""
    navigation_running = False

    def log_warning(self, message, *args, **kwargs):
        if self.navigation_running:
            self.last_navigation_warning = str(message)
        return super().log_warning(message, *args, **kwargs)

    def load_grid_for_map(self, map_id="", **kwargs):
        if hasattr(self, "navigation_grid") and map_id == self.grid.meta.map_name:
            return self.navigation_grid
        return super().load_grid_for_map(map_id, **kwargs)

    def _grid_nav_state_stuck(self, ctx):
        self._recovery_timed_out = False
        result = super()._grid_nav_state_stuck(ctx)
        if result is False:
            self.navigation_failure = {
                "position": ctx.position,
                "waypoint": ctx.step.waypoint,
                "reason": ctx.step.reason,
                "recoveries": ctx.session.recoveries,
                "obstructed": not self._recovery_timed_out,
                "failure_kind": "timeout" if self._recovery_timed_out else "recovery_exhausted",
            }
        return result

    def _grid_navigation_timed_out(self, deadline, limit):
        """Retain the production timeout decision instead of guessing from recovery count."""
        expired = super()._grid_navigation_timed_out(deadline, limit)
        self._recovery_timed_out = expired
        return expired

    def _save_obstacles(self):
        from scripts.nav.validate_visual_grid import write_json

        self.navigation_grid = temporary_obstacle_grid(self.grid, self.failures if self.options.skip_blocked else [])
        self.target_planner = GridRouteFollower(self.navigation_grid, self._grid_follower_config()).planner
        write_json(self.directory / "blocked_points.json", self.failures)
        write_json(self.directory / "skipped_cells.json", sorted(self.skipped))
        write_json(self.directory / "covered_cells.json", sorted(self.covered))
        write_json(self.directory / "traversed_cells.json", sorted(self.traversed))
        save_grid(self.directory / "temporary.grid.npz", self.navigation_grid)

    def _skip_failed_goal(self, point):
        self._stop_inputs()
        failure = {
            "position": point,
            "reason": "project_navigation_failed",
            "obstructed": False,
            **self.navigation_failure,
            "goal": self.goal,
            "time": time.time(),
            "source_run": str(self.directory.resolve()),
        }
        self.failures.append(failure)
        self.skipped.update(nearby_cells(self.region, self.grid.index_of_world(*self.goal), self.coverage_steps))
        if self.last_frame is not None:
            filename = f"blocked_{len(self.failures):04d}_hud.png"
            cv2.imwrite(str(self.directory / filename), self.last_frame[:280, :340])
            failure["hud"] = filename
        self._save_obstacles()
        self.log_warning("验证点暂时跳过：" + json.dumps(failure, ensure_ascii=False))

    def _grid_nav_config(self):
        config = dict(super()._grid_nav_config() or self.grid_navigation_default_config())
        if self.validation is not None:
            config.update(
                {
                    CONFIG_GRID_ALLOW_UNKNOWN: False,
                    CONFIG_GRID_USE_ZIP_LINES: False,
                    CONFIG_GRID_FILE: str(self.validation[0].grid.resolve()),
                }
            )
        return config

    def _stop_inputs(self):
        self._set_grid_walking(False)
        interaction = self.executor.interaction
        for key, count in list(getattr(interaction, "_pressed_keys", {}).items()):
            for _ in range(count):
                interaction.send_key_up(key)

    def _check_stop(self):
        if self.validation_failure or self.stop_reason:
            self._stop_inputs()
            raise RuntimeError(self.validation_failure or self.stop_reason)

    def sleep(self, timeout):
        self._check_stop()
        super().sleep(timeout)
        self._check_stop()

    def _watch(self):
        import win32api
        import win32gui

        while not self.guard_done.wait(0.05):
            if win32api.GetAsyncKeyState(0x77) & 0x8000 or (self.directory / "STOP").exists():
                self.stop_reason = "manual_stop"
            if (
                self.movement_started
                and win32gui.GetForegroundWindow() != self.executor.device_manager.hwnd_window.hwnd
            ):
                self.stop_reason = "game_not_foreground"
            if time.monotonic() > self.deadline:
                self.stop_reason = "time_limit"
            if self.stop_reason:
                self._stop_inputs()

    def _observe(self, state, navigation_source="visual_map"):
        self._check_stop()
        latest, checked, references = self.reference.snapshot(since=self._reference_cursor)
        self._reference_cursor += len(references)
        self._reference_timeline.extend(references)
        if latest is None or time.time() - checked > 1.5:
            self.stop_reason = "reference_unavailable_or_dead_flow"
            self._check_stop()
        stamp = float(state.get("frame_t") or 0)
        identifiable = (
            math.isfinite(stamp) and stamp > 0 and state.get("frame_time_source") == "wgc_system_relative_time"
        )
        duplicate = stamp in self._observed_frames if identifiable else bool(state.get("visual_duplicate_frame"))
        if identifiable:
            self._observed_frames.add(stamp)
        pose = dict(state)
        absolute = pose.pop("_validation_absolute", None)
        row = {
            "frame_t": stamp,
            "observed_at": time.time(),
            "frame_age_s": time.time() - stamp,
            "pose": pose,
            "validation_duplicate": duplicate,
            "absolute": absolute,
            "navigation_source": navigation_source,
            "flow": latest.get("flow"),
            "stationary_reference": not self._grid_nav_w_held
            and float(latest.get("speed") or 0) <= 0.1
            and float(latest.get("time") or 0) <= stamp,
        }
        self.samples.append(row)
        self.sample_log.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.sample_log.flush()
        if reference_map_id(latest) != self.grid.meta.map_name:
            # Reference identity is a run invariant, independent of visual readiness.
            self.stop_reason = "map_or_tier_changed"
            self._check_stop()
        if state.get("position_trusted"):
            if state.get("frame_time_source") != "wgc_system_relative_time":
                self.stop_reason = "missing_wgc_presentation_timestamp"
                self._check_stop()
            scope = pose_scope_status(state, self.grid.meta.map_name)
            if scope not in ("record", "tier_pending"):
                self.stop_reason = "map_or_tier_changed"
                self._check_stop()
            if scope == "record" and not duplicate:
                self.traversed.update(
                    nearby_cells(self.region, self.grid.index_of_world(state["x"], state["z"]), self.coverage_steps)
                )
        self._process_reference_samples(self._reference_timeline)
        self._check_stop()

    def _process_reference_samples(self, references, *, final=False):
        """Process append-only history incrementally; replay provisional results after late insertions."""
        if self.validation_failure:
            return
        timeline = references if isinstance(references, ReferenceTimeline) else ReferenceTimeline(references or [])
        if not hasattr(self, "_reference_covered_baseline"):
            self._reference_covered_baseline = set(self.covered)
        snapshot = (timeline, timeline.revision)
        previous = getattr(self, "_reference_validation_snapshot", None)
        if previous is not None and previous != snapshot:
            self._validation_cursor = 0
            self.error_hits = 0
            self.covered = set(self._reference_covered_baseline)
        self._reference_validation_snapshot = snapshot
        while self._validation_cursor < len(self.samples):
            row = self.samples[self._validation_cursor]
            if duplicate_observation(row):
                self._validation_cursor += 1
                continue
            pose, stamp = row["pose"], row["frame_t"]
            reference = paired_visual_reference(row, timeline, self.grid.meta.map_name)
            awaiting_right = not timeline.times or stamp > timeline.times[-1]
            if (
                reference is None
                and awaiting_right
                and not final
                and coverage_pose_ready(pose, self.grid.meta.map_name)
            ):
                break
            self._validation_cursor += 1
            if reference is None:
                self.error_hits = 0
                continue
            distance = math.dist((pose["x"], pose["z"]), (reference["x"], reference["z"]))
            self.error_hits = self.error_hits + 1 if distance > self.options.max_error else 0
            if distance <= self.options.max_error:
                self.covered.update(
                    nearby_cells(self.region, self.grid.index_of_world(pose["x"], pose["z"]), self.coverage_steps)
                )
            if self.error_hits >= 3:
                self.validation_failure = f"sustained_position_error: {distance:.2f}m"
                break

    def _write_progress(self, point):
        if time.monotonic() - self.last_progress_log >= 5:
            from scripts.nav.validate_visual_grid import write_json

            progress = {
                "status": "running",
                "covered_cells": len(self.covered),
                "total_cells": len(self.region_cells),
                "coverage_fraction": len(self.covered) / len(self.region_cells),
                "traversed_cells": len(self.traversed),
                "traversal_fraction": len(self.traversed) / len(self.region_cells),
                "visual_unavailable_cells": len(self.traversed - self.covered),
                "navigation_source": self.route_source,
                "capture_navigation_frames": self.capture_frames,
                "arrived_goals": len(self.arrived),
                "samples": len(self.samples),
                "position": point,
                "goal": self.goal,
                "blocked_events": len(self.failures),
                "skipped_uncovered_cells": len(self.skipped - self.covered),
                "deferred_disconnected_cells": len(self.deferred - self.covered),
            }
            write_json(self.directory / "progress.json", progress)
            self.log_info(json.dumps(progress))
            self.last_progress_log = time.monotonic()

    def _navigation_pose(self, service, visual):
        from scripts.nav.validate_visual_grid import write_json

        source = self.source_policy.choose(visual)
        selected = dict(service._capture_world_pose() if source == "capture" else visual)
        if source == "capture":
            selected.update(
                heading=visual.get("heading"), heading_score=visual.get("heading_score"), heading_source="visual_arrow"
            )
        if selected.get("position_trusted") and selected.get("map_id") != self.grid.meta.map_name:
            self.stop_reason = "navigation_map_changed"
            self._check_stop()
        if source != self.route_source:
            event = {
                "time": time.time(),
                "from": self.route_source,
                "to": source,
                "visual_reason": visual.get("trust_reason"),
                "visual_pose": visual,
                "navigation_position": [selected.get("x"), selected.get("z")],
            }
            self.source_events.append(event)
            if source == "capture" and self.last_frame is not None:
                event["hud"] = f"visual_fallback_{len(self.source_events):04d}.png"
                cv2.imwrite(str(self.directory / event["hud"]), self.last_frame[:280, :340])
            write_json(self.directory / "source_events.json", self.source_events)
            self.log_info("验证导航来源切换：" + json.dumps(event, ensure_ascii=False))
            self.route_source = source
        self._observe(visual, source)
        token = (source, selected.get("sync_seq", 0))
        if token != self.route_token:
            self.route_seq += 1
            self.route_token = token
        selected.update(sync_seq=self.route_seq, source=source, sample_t=visual["sample_t"])
        if selected.get("position_trusted"):
            point = (selected["x"], selected["z"])
            self.traversed.update(nearby_cells(self.region, self.grid.index_of_world(*point), self.coverage_steps))
        else:
            point = None
        if source == "capture":
            self.capture_frames += 1
            self.capture_log.write(json.dumps({"time": time.time(), "pose": selected}) + "\n")
            self.capture_log.flush()
        service._minimap_last_state = dict(selected)
        publish_world_pose(service.runtime_state_hub, selected, source=source, now=selected["sample_t"])
        self._write_progress(point)
        return selected

    @contextmanager
    def _validation_resources(self):
        """Own logs, reference sampling and temporary service hooks as one scope."""
        from scripts.nav.validate_visual_grid import ReferenceRecorder

        guard = None
        with ExitStack() as resources:
            try:
                self.sample_log = resources.enter_context((self.directory / "visual.jsonl").open("w", encoding="utf-8"))
                self.capture_log = resources.enter_context(
                    (self.directory / "capture_navigation.jsonl").open("w", encoding="utf-8")
                )
                self.reference = ReferenceRecorder(self.options.port, self.directory)
                resources.callback(self.reference.close)
                service = self.get_runtime_position_service()
                original_config = service.config
                resources.callback(setattr, service, "config", original_config)
                service.config = {
                    **dict(original_config),
                    CONFIG_USE_EXPERIMENTAL: False,
                    CONFIG_EXPERIMENTAL_PORT: self.options.port,
                }
                original_sample, original_locate = service.sample_world_pose, service._locate_visual_frame
                resources.callback(setattr, service, "sample_world_pose", original_sample)
                resources.callback(setattr, service, "_locate_visual_frame", original_locate)

                def observe_locate(frame, stamp, timing):
                    self.last_frame = frame
                    absolute = None
                    tracker = service._visual_source.tracker
                    original_update = tracker.update

                    def observe_update(*args, **kwargs):
                        nonlocal absolute
                        result = original_update(*args, **kwargs)
                        absolute = result.position.to_dict() if result.position is not None else None
                        return result

                    tracker.update = observe_update
                    try:
                        state = original_locate(frame, stamp, timing)
                        # Carry diagnostics with the sampled state, including any later cache hit.
                        return {**state, "_validation_absolute": absolute}
                    finally:
                        tracker.update = original_update

                def observe_sample(*args, **kwargs):
                    return self._navigation_pose(service, original_sample(*args, **kwargs))

                service._locate_visual_frame, service.sample_world_pose = observe_locate, observe_sample
                guard = threading.Thread(target=self._watch, daemon=True)
                guard.start()
                yield service
            finally:
                self.guard_done.set()
                try:
                    self._stop_inputs()
                finally:
                    if guard is not None and guard.ident is not None:
                        guard.join(timeout=1)
                    if self.reference is not None:
                        time.sleep(0.4)

    def _reference_verified_cells(self, references):
        """Count paired, accurate visual observations in the validation's map and floor."""
        timeline, verified = ReferenceTimeline(references), set()
        for row in self.samples:
            pose = row["pose"]
            ref = paired_visual_reference(row, timeline, self.grid.meta.map_name)
            if ref is not None and math.dist((pose["x"], pose["z"]), (ref["x"], ref["z"])) <= self.options.max_error:
                verified.update(
                    nearby_cells(self.region, self.grid.index_of_world(ref["x"], ref["z"]), self.coverage_steps)
                )
        return verified

    def run(self):
        from scripts.nav.validate_visual_grid import (
            report_accuracy,
            write_json,
        )

        self.options, self.grid, self.region, targets, self.directory, plan = self.validation
        self.samples, self.covered, self.arrived = [], set(), []
        self._validation_cursor = 0
        self._reference_cursor = 0
        self._reference_timeline = ReferenceTimeline([])
        self._observed_frames = set()
        self.validation_failure = ""
        self.covered.update(self.options.resume_covered)
        self._reference_covered_baseline = set(self.covered)
        self._reference_validation_snapshot = None
        self.traversed = set(self.options.resume_traversed) | self.covered
        self.source_policy = NavigationSourcePolicy(self.options.capture_fallback)
        self.source_events, self.route_source, self.route_token, self.route_seq, self.capture_frames = (
            [],
            None,
            None,
            0,
            0,
        )
        self.failures, self.skipped = (
            list(self.options.resume_failures),
            set(self.options.resume_skipped),
        )
        self.deferred, self.navigation_failure = set(), {}
        self.navigation_failures, unready_since, transient_failures = [], None, 0
        self.region_cells = set(map(tuple, np.argwhere(self.region).tolist()))
        self.coverage_steps = math.floor(self.options.spacing / self.grid.meta.cell_size)
        if self.options.skip_blocked:
            for failure in self.failures:
                self.skipped.update(
                    nearby_cells(self.region, self.grid.index_of_world(*failure["goal"]), self.coverage_steps)
                )
        self._save_obstacles()
        self.goal, self.last_frame = None, None
        self.last_progress_log, self.error_hits = 0.0, 0
        self.movement_started = False
        self.deadline = time.monotonic() + self.options.seconds
        started = time.monotonic()
        self.guard_done = threading.Event()
        self.reference = None
        resources = ExitStack()
        status, reason = "incomplete", "time_limit"
        try:
            resources.enter_context(self._validation_resources())
            if self.options.activate_game and self.options.mode == "run":
                self.ensure_in_front()
                self.wait_until(lambda: self.hwnd.is_foreground(), time_out=5)
            self.ensure_runtime_position_service(force_start=True)
            remaining = set(targets)
            while time.monotonic() < self.deadline:
                self._check_stop()
                pose = self.world_pose(frame=self.next_frame(), max_age=0.6)
                if self.options.mode == "record":
                    self.sleep(0.15)
                    continue
                if not navigation_pose_ready(pose):
                    unready_since = time.monotonic() if unready_since is None else unready_since
                    if time.monotonic() - unready_since > 30:
                        reason = "visual_navigation_unavailable: " + str((pose or {}).get("trust_reason", "no_pose"))
                        latest, _, _ = self.reference.snapshot()
                        write_json(
                            self.directory / "localization_failure.json",
                            {"reason": reason, "pose": pose, "reference_diagnostic_only": latest, "time": time.time()},
                        )
                        break
                    self.sleep(0.2)
                    continue
                unready_since = None
                if len(self.traversed) == len(self.region_cells):
                    status = "completed" if self.traversed == self.covered else "completed_with_visual_gaps"
                    reason = "connected_region_traversed"
                    break
                remaining.difference_update(self.traversed)
                point = (pose["x"], pose["z"])
                free = self.navigation_grid.nearest_free(*self.navigation_grid.index_of_world(*point), max_radius=2)
                if free is None:
                    reason = "no_free_start_after_temporary_obstacles"
                    break
                reachable_region, _ = connected_region(self.navigation_grid, self.navigation_grid.world_of_index(*free))
                available = self.region_cells.intersection(map(tuple, np.argwhere(reachable_region).tolist()))
                self.deferred = self.region_cells - available
                pending = available - self.traversed - (self.skipped if self.options.skip_blocked else set())
                choices = remaining.intersection(pending) or pending
                if not choices:
                    status, reason = "completed_with_skips", "remaining_reachable_region_tested"
                    break
                reachable = [p for p in choices if math.dist(point, self.grid.world_of_index(*p)) > 2.0]
                selection = self.target_planner.plan_multi([free], sorted(reachable or choices), time_budget_s=10)
                if not selection.ok:
                    reason = "target_selection_failed: " + selection.reason
                    break
                target = selection.goal_cell
                self.goal = self.grid.world_of_index(*target)
                self.movement_started = True
                self.navigation_failure = {}
                self.last_navigation_warning, self.navigation_running = "", True
                if not self.navigate_grid_to(
                    self.goal,
                    map_id=self.grid.meta.map_name,
                    grid_path=str(self.options.grid.resolve()),
                    goal_tier=self.grid.meta.tier,
                    timeout=min(180, max(1, self.deadline - time.monotonic())),
                    stop_distance=0.8,
                ):
                    reason = self.stop_reason or "project_navigation_failed"
                    if self.stop_reason or not self.options.skip_blocked:
                        break
                    self.navigation_running = False
                    if not self.navigation_failure.get("obstructed"):
                        event = {
                            "goal": self.goal,
                            "time": time.time(),
                            "reason": self.last_navigation_warning or reason,
                            "pose": self.samples[-1]["pose"] if self.samples else pose,
                        }
                        self.navigation_failures.append(event)
                        write_json(self.directory / "navigation_failures.json", self.navigation_failures)
                        transient_failures += 1
                        if transient_failures >= 3:
                            reason = "navigation_unavailable: " + event["reason"]
                            break
                        self.sleep(0.5)
                        continue
                    current = next(
                        (r["pose"] for r in reversed(self.samples) if r["pose"].get("position_trusted")), pose
                    )
                    self._skip_failed_goal((current["x"], current["z"]))
                    continue
                self.navigation_running, transient_failures = False, 0
                self.arrived.append(self.goal)
        except Exception as exc:
            status, reason = "stopped", self.stop_reason or f"{type(exc).__name__}: {exc}"
            self.log_warning(reason)
        finally:
            self.navigation_running = False
            try:
                resources.close()
            except Exception as exc:
                status, reason = "stopped", f"cleanup_failed: {type(exc).__name__}: {exc}"
                self.log_warning(reason)
            if self.last_frame is not None:
                cv2.imwrite(str(self.directory / "last_hud.png"), self.last_frame[:280, :340])
        references = self.reference.snapshot()[2] if self.reference is not None else []
        self._process_reference_samples(references, final=True)
        if self.validation_failure:
            status, reason = "stopped", self.validation_failure
        elif reason == "connected_region_traversed":
            status = "completed" if self.traversed == self.covered else "completed_with_visual_gaps"
        verified = self._reference_verified_cells(references)
        self.report = {
            **plan,
            "status": status,
            "reason": reason,
            "stop_request": self.stop_reason,
            "validation_failure": self.validation_failure,
            "elapsed_s": time.monotonic() - started,
            "covered_cells": len(self.covered),
            "reference_verified_cells": len(verified),
            "coverage_fraction": len(self.covered) / len(self.region_cells),
            "traversed_cells": len(self.traversed),
            "traversal_fraction": len(self.traversed) / len(self.region_cells),
            "visual_unavailable_cells": len(self.traversed - self.covered),
            "capture_navigation_frames": self.capture_frames,
            "arrived_goals": len(self.arrived),
            "blocked_events": len(self.failures),
            "navigation_failure_events": len(self.navigation_failures),
            "skipped_uncovered_cells": len(self.skipped - self.covered),
            "deferred_disconnected_cells": len(self.deferred - self.covered),
            "reference_verified_scope": "current_run_only",
            "last_goal": self.goal,
            "last_visual_position": next(
                ([r["pose"]["x"], r["pose"]["z"]] for r in reversed(self.samples) if r["pose"].get("position_trusted")),
                None,
            ),
            "frames": len(self.samples),
            "trusted_frames": sum(bool(r["pose"].get("position_trusted")) for r in self.samples),
            "accuracy": report_accuracy(self.samples, references),
            "navigation_implementation": "MinimapNavigateToPoint.navigate_grid_to / WorldPoseService / EfInteraction",
        }
        write_json(self.directory / "report.json", self.report)
        write_json(self.directory / "progress.json", self.report)
        write_json(self.directory / "covered_cells.json", sorted(self.covered))
        write_json(self.directory / "traversed_cells.json", sorted(self.traversed))
        write_json(self.directory / "visual_unavailable_cells.json", sorted(self.traversed - self.covered))
        self.log_info(json.dumps(self.report, ensure_ascii=False))
        return status == "completed"


def run_validation_task(args, grid, region, targets, directory, plan):
    import ok

    from src.config import config
    from src.localization.wgc_frame_timing import install_wgc_frame_timing
    from src.patches.position_service_patch import install_position_service_patch

    install_wgc_frame_timing()
    install_position_service_patch()
    settings = copy.deepcopy(config)
    settings.update(
        use_gui=False,
        check_mutex=False,
        onetime_tasks=[],
        trigger_tasks=[],
        config_folder=str(directory / "runtime-config"),
        screenshots_folder=str(directory / "screenshots"),
        log_upload_api="",
    )
    settings["windows"].update(start_exe=False, capture_method=["WGC"])
    runtime = ok.OK(settings)
    task = runtime.get_onetime_task(GridValidationTask)
    task.validation = (args, grid, region, targets, directory, plan)
    try:
        runtime.run_task(task, exit_after=False)
    finally:
        runtime.quit()
        runtime.task_executor.destroy()
    report = getattr(task, "report", {})
    if report.get("validation_failure"):
        return 1
    return (
        0
        if report.get("status") == "completed"
        or (args.mode == "record" and report.get("reason") == "time_limit" and report.get("frames", 0) > 0)
        else 1
    )
