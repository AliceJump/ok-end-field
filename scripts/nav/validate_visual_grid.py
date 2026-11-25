"""按连通网格遍历并记录视觉定位与独立抓包坐标；默认仅规划。"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


from src.patches.pre_config_patch import install_pre_config_patch

install_pre_config_patch()

from scripts.nav.path_safety import resolve_under
from scripts.nav.visual_validation import (
    ReferenceTimeline,
    capture_on_main_tier,
    connected_region,
    coverage_targets,
    eligible_visual_observation,
    error_statistics,
    paired_visual_reference,
)
from src.localization.capture_position_source import CapturePositionSource
from src.nav.grid_io import load_grid_from_bytes
from src.nav.layered_map import load_layered_map


def _write_json_atomic(target, value):
    pending = target.with_name(target.name + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(target)


# Each writer builds its path from a literal, so diagnostic names only select a
# callable and never flow into a filesystem path.
_DIAGNOSTIC_WRITERS = {
    "blocked_points.json": lambda root, value: _write_json_atomic(root / "blocked_points.json", value),
    "covered_cells.json": lambda root, value: _write_json_atomic(root / "covered_cells.json", value),
    "inherited_unconfirmed_failures.json": lambda root, value: _write_json_atomic(
        root / "inherited_unconfirmed_failures.json", value
    ),
    "localization_failure.json": lambda root, value: _write_json_atomic(root / "localization_failure.json", value),
    "navigation_failures.json": lambda root, value: _write_json_atomic(root / "navigation_failures.json", value),
    "plan.json": lambda root, value: _write_json_atomic(root / "plan.json", value),
    "progress.json": lambda root, value: _write_json_atomic(root / "progress.json", value),
    "report.json": lambda root, value: _write_json_atomic(root / "report.json", value),
    "skipped_cells.json": lambda root, value: _write_json_atomic(root / "skipped_cells.json", value),
    "source_events.json": lambda root, value: _write_json_atomic(root / "source_events.json", value),
    "traversed_cells.json": lambda root, value: _write_json_atomic(root / "traversed_cells.json", value),
    "visual_unavailable_cells.json": lambda root, value: _write_json_atomic(
        root / "visual_unavailable_cells.json", value
    ),
}


def write_diagnostic(run_directory, name, value):
    """Atomically write one allow-listed diagnostic file inside ``run_directory``.

    The file name selects a writer only; paths are fixed literals inside it.
    """
    writer = _DIAGNOSTIC_WRITERS.get(name)
    if writer is None:
        raise ValueError(f"未登记的诊断产物: {name!r}")
    writer(Path(run_directory).resolve(), value)


class ReferenceRecorder:
    def __init__(self, port, directory):
        self.source = CapturePositionSource(port, timeout=0.5)
        self.records = {}
        self._ordered_records = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.error = ""
        self.latest = None
        self.checked_at = 0.0
        self.handle = (directory / "reference.jsonl").open("w", encoding="utf-8")
        try:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        except Exception:
            self.handle.close()
            raise

    def _run(self):
        try:
            self._record()
        except Exception as exc:
            self.error = f"reference_recording_failed: {type(exc).__name__}: {exc}"
            self.ready.set()

    def _record(self):
        fields = (
            "time",
            "captureAt",
            "sampleId",
            "flow",
            "scene",
            "levelId",
            "mapId",
            "x",
            "y",
            "z",
            "yaw",
            "speed",
            "replayed",
        )
        while not self.stop.is_set():
            state = self.source.fetch_state()
            if state is not None:
                last = state.get("lastPosition") or {}
                _, _, alive = self.source._flow_liveness(state, last, time.time())
                latest = self._with_identity(last, state) if alive else None
                checked_at = time.time()
                batch = {}
                for item in [*(state.get("trajectory") or []), last]:
                    if not isinstance(item, dict) or not item.get("time"):
                        continue
                    row = {key: item.get(key) for key in fields}
                    key = (row["flow"], row["time"], row["sampleId"])
                    if key not in self.records and key not in batch:
                        identity_state = state if all(item.get(k) == last.get(k) for k in ("flow", "scene")) else {}
                        batch[key] = self._with_identity(row, identity_state)
                for row in batch.values():
                    self.handle.write(json.dumps(row) + "\n")
                self.handle.flush()
                with self.lock:
                    self.latest = latest
                    self.checked_at = checked_at
                    self.records.update(batch)
                    self._ordered_records.extend(batch.values())
            if self.latest is not None:
                self.ready.set()
            self.stop.wait(0.1)

    def _with_identity(self, record, state):
        try:
            map_id, source = self.source._resolve_map_id(record, state, float(record["x"]), float(record["z"]))
        except (KeyError, TypeError, ValueError):
            map_id, source = "", "invalid_coordinates"
        return {**record, "resolved_map_id": map_id, "map_id_source": source}

    def snapshot(self, *, since=0):
        with self.lock:
            records = [] if since is None else self._ordered_records[since:]
            return self.latest, self.checked_at, records

    def wait_ready(self, timeout):
        return self.ready.wait(timeout)

    def close(self):
        self.stop.set()
        self.thread.join()
        try:
            self.handle.close()
        except OSError as exc:
            self.error = f"reference_recording_failed: {type(exc).__name__}: {exc}"


def report_accuracy(samples, references, *, map_id=None, layered=None):
    references = ReferenceTimeline(references)
    groups = {
        "absolute_time_aligned": [],
        "fused_time_aligned": [],
        "absolute_stationary": [],
        "fused_stationary": [],
    }
    eligible = missing = 0
    for row in samples:
        pose = row["pose"]
        expected_map = map_id or pose.get("map_id")
        if not eligible_visual_observation(row, expected_map):
            continue
        eligible += 1
        reference = paired_visual_reference(row, references, expected_map, layered=layered)
        if reference is None:
            missing += 1
            continue
        suffix = "stationary" if reference["mode"] == "stationary_hold" else "time_aligned"
        groups[f"fused_{suffix}"].append((pose["x"] - reference["x"], pose["z"] - reference["z"]))
        absolute = row.get("absolute")
        if absolute is not None:
            groups[f"absolute_{suffix}"].append(
                (absolute["world_x"] - reference["x"], absolute["world_z"] - reference["z"])
            )
    return {
        "eligible_frames": eligible,
        "unpaired_frames": missing,
        **{key: error_statistics(values) for key, values in groups.items()},
    }


def execute(args, grid, region, targets, directory, plan):
    from scripts.nav.validation_task import run_validation_task

    return run_validation_task(args, grid, region, targets, directory, plan)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Run from an Administrator terminal with the game and capture backend open. "
            "Plan mode writes plan.json. Press F8 or create OUTPUT/STOP to stop record/run mode, which also writes progress.json, "
            "visual.jsonl, reference.jsonl and report.json to a new output directory. "
            "Use --resume DIR --skip-blocked to continue coverage. "
            "--capture-fallback explicitly allows packet navigation while visual localization fails; "
            "packet traversal and visual validation are reported separately. "
            "--activate-game brings the game to the foreground at startup."
        ),
    )
    parser.add_argument("--mode", choices=("plan", "record", "run"), default="plan")
    parser.add_argument(
        "--grid",
        type=Path,
        default=ROOT / "assets/nav-grid-data/navigation/map02_4.grid.npz",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--spacing", type=float, default=5.0)
    parser.add_argument("--seconds", type=float, default=14400)
    parser.add_argument("--max-error", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--skip-blocked", action="store_true")
    parser.add_argument("--capture-fallback", action="store_true")
    parser.add_argument("--activate-game", action="store_true")
    args = parser.parse_args()
    args.grid = resolve_under(ROOT, args.grid, label="grid", must_exist=True)
    if args.output is not None:
        args.output = resolve_under(ROOT, args.output, label="output")
    if args.resume is not None:
        args.resume = resolve_under(ROOT, args.resume, label="resume", must_exist=True)
    if args.mode == "run" and not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("run mode requires an Administrator terminal")
    if not math.isfinite(args.seconds) or args.seconds <= 0 or not math.isfinite(args.max_error) or args.max_error <= 0:
        parser.error("seconds and max-error must be finite and positive")
    directory = resolve_under(
        ROOT,
        args.output or ROOT / "logs" / time.strftime("visual_grid_validation_%Y%m%d_%H%M%S"),
        label="output",
    )
    sample = CapturePositionSource(args.port).poll()
    if sample is None or not sample.flow_alive:
        raise RuntimeError("reference_backend_has_no_live_position")
    if not sample.map_id:
        raise RuntimeError("reference_map_identity_unresolved")
    grid_bytes = args.grid.read_bytes()
    grid = load_grid_from_bytes(grid_bytes, name=args.grid.name)
    if grid.meta.map_name != sample.map_id or grid.meta.tier:
        raise RuntimeError("grid_does_not_match_current_main_map")
    args.reference_layers = load_layered_map(args.grid.parent, sample.map_id, zoom=grid.meta.zoom)
    args.reference_layers.layers[(sample.map_id, "")] = grid
    if not capture_on_main_tier(vars(sample), args.reference_layers, sample.map_id):
        raise RuntimeError("reference_start_main_tier_unconfirmed")
    start_cell = grid.index_of_world(sample.x, sample.z)
    start_free = grid.nearest_free(*start_cell, max_radius=2)
    if start_free is None or grid.is_blocked(*start_cell):
        raise RuntimeError("start_not_near_free_grid")
    start_center = grid.world_of_index(*start_free)
    start_snap_m = math.dist((sample.x, sample.z), start_center)
    if start_snap_m > 2.0:
        raise RuntimeError(f"start_snap_too_far: {start_snap_m:.2f}m")
    region, excluded = connected_region(grid, start_center)
    targets = coverage_targets(grid, region, args.spacing)
    floor_evidence = {
        "declared_tiers": {
            level: tiers
            for level, tiers in args.reference_layers.declared_tiers.items()
            if level == sample.map_id or level.startswith(sample.map_id + "_")
        },
        "regions": sorted(
            (tier, layer.meta.floor_regions, layer.meta.floor_regions_error)
            for (name, tier), layer in args.reference_layers.layers.items()
            if name == sample.map_id
        ),
    }
    plan = {
        "floor_evidence_sha256": hashlib.sha256(json.dumps(floor_evidence, sort_keys=True).encode()).hexdigest(),
        "grid": str(args.grid.resolve()),
        "grid_sha256": hashlib.sha256(grid_bytes).hexdigest(),
        "map_id": sample.map_id,
        "tier_id": "__main__",
        "spacing_m": args.spacing,
        "max_error_m": args.max_error,
        "coverage_definition": "observed visual position within spacing_m along free four-connected cells; not every cell physically visited",
        "component_cells": int(region.sum()),
        "excluded_disconnected_cells": excluded,
        "planned_targets": len(targets),
        "reference_role": "independent validation only; never passed to visual localization or steering",
        "start_reference": [sample.x, sample.z],
        "start_grid_center": start_center,
        "start_snap_m": start_snap_m,
        "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()),
    }
    args.resume_covered, args.resume_failures, args.resume_skipped = set(), [], set()
    args.resume_traversed = set()
    unconfirmed = []
    if args.resume:

        def resume_artifact(name):
            return resolve_under(ROOT, args.resume / name, label="resume_artifact")

        previous = json.loads(resume_artifact("plan.json").read_text(encoding="utf-8"))
        for key in ("grid_sha256", "floor_evidence_sha256", "map_id", "tier_id", "spacing_m", "component_cells"):
            if previous.get(key) != plan[key]:
                raise ValueError(f"resume_mismatch: {key}")
        args.resume_covered = set(
            map(tuple, json.loads(resume_artifact("covered_cells.json").read_text(encoding="utf-8")))
        )
        traversed_file = resume_artifact("traversed_cells.json")
        args.resume_traversed = (
            set(map(tuple, json.loads(traversed_file.read_text(encoding="utf-8"))))
            if traversed_file.exists()
            else set(args.resume_covered)
        )
        # Old runs did not record their accuracy threshold. Keep their traversal
        # history but remeasure coverage whose error bound cannot be established.
        previous_error = previous.get("max_error_m")
        if previous_error != args.max_error:
            args.resume_covered.clear()
        if any(not grid.in_bounds(*p) or not region[p] for p in args.resume_covered):
            raise ValueError("resume_component_mismatch")
        if any(not grid.in_bounds(*p) or not region[p] for p in args.resume_traversed):
            raise ValueError("resume_traversal_component_mismatch")
        failure_file = resume_artifact("blocked_points.json")
        if failure_file.exists():
            args.resume_failures = json.loads(failure_file.read_text(encoding="utf-8"))
        elif resume_artifact("failure_analysis.json").exists():
            failure = json.loads(resume_artifact("failure_analysis.json").read_text(encoding="utf-8"))
            args.resume_failures = [
                {
                    "position": failure["last_visual_position"],
                    "goal": failure["last_goal"],
                    "reason": failure["navigation_failure"],
                    "obstructed": failure.get("obstructed") is True,
                    "source_run": str(args.resume.resolve()),
                }
            ]
        unconfirmed = [item for item in args.resume_failures if not item.get("obstructed")]
        args.resume_failures = [item for item in args.resume_failures if item.get("obstructed")]
        plan.update(resumed_from=str(args.resume.resolve()), inherited_covered_cells=len(args.resume_covered))
    plan["skip_blocked"] = args.skip_blocked
    plan["capture_fallback"] = args.capture_fallback
    plan["activate_game"] = args.activate_game
    if args.capture_fallback:
        plan["reference_role"] = "accuracy reference and explicit fallback navigation; never fed into visual matching"
    directory.mkdir(parents=True, exist_ok=False)
    if unconfirmed:
        write_diagnostic(directory, "inherited_unconfirmed_failures.json", unconfirmed)
    write_diagnostic(directory, "plan.json", {**plan, "targets": [grid.world_of_index(*point) for point in targets]})
    print(json.dumps(plan, ensure_ascii=False), flush=True)
    return 0 if args.mode == "plan" else execute(args, grid, region, targets, directory, plan)


if __name__ == "__main__":
    raise SystemExit(main())
