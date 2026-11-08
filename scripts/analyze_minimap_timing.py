"""Analyze QPC-timed project captures, with a held-out lag diagnostic.

uv run --locked python -X utf8 scripts/analyze_minimap_timing.py --dataset PATH --output PATH
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
from functools import partial
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.localization.minimap_map_locator import MinimapGeometry, MinimapMapLocator, MinimapTracker  # noqa: E402


def stats(values):
    return (
        {
            "n": len(values),
            "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)),
            "max": float(max(values)),
        }
        if len(values)
        else None
    )


class Trajectory:
    def __init__(self, packets):
        self.packets = sorted(packets, key=lambda p: p["time"])
        self.times = [p["time"] for p in self.packets]

    def at(self, t, sample):
        i = bisect.bisect_right(self.times, t)
        if not 0 < i < len(self.times):
            return None
        a, b = self.packets[i - 1], self.packets[i]
        dt = b["time"] - a["time"]
        if not 0 < dt <= 0.5 or a["flow"] != b["flow"] or a["flow"] != sample["flow"]:
            return None
        if a["scene"] != b["scene"] or a["scene"] != sample["scene"]:
            return None
        w = (t - a["time"]) / dt
        left, right = np.array([a["x"], a["z"]]), np.array([b["x"], b["z"]])
        return left + w * (right - left), (right - left) / dt


def speed_bin(speed):
    if speed <= 0.1:
        return "stationary"
    if speed < 0.5:
        return "transition"
    if speed < 3:
        return "slow_0.5_to_3_m_s"
    if speed < 6:
        return "normal_3_to_6_m_s"
    return "fast_6_plus_m_s"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-frames", action="store_true", help="Reuse matching saved inference for analysis only")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    samples = [json.loads(x) for x in (args.dataset / "samples.jsonl").read_text().splitlines()]
    trajectory = _load_trajectory(args.dataset)
    first = samples[0]
    cv2.setNumThreads(1)
    locator = MinimapMapLocator.from_files(first["map_image"], first["mapping"])
    tracker = MinimapTracker(locator)
    rows = _inference_rows(args, samples, first, tracker)

    residual = partial(residual_for, trajectory)

    report = {
        "samples": len(samples),
        "capture_method": "WindowsGraphicsCaptureMethod",
        "timestamp_source": "wgc_system_relative_time",
        "visual_analysis_eligibility": "Per-frame robust NCC>=.85, full NCC>=.65, margin>=.08. Report tracker accepted count separately; pending confirmation is not a visual match failure.",
        "bins": {},
        "clock_mapping_uncertainty_us": stats([s["presentation_timing"]["clock_uncertainty_s"] * 1e6 for s in samples]),
        "callback_minus_frame_ms": stats(
            [(s["presentation_timing"]["callback_time"] - s["frame_t"]) * 1000 for s in samples]
        ),
        "capture_interval_ms": stats([s["capture_interval_s"] * 1000 for s in samples]),
        "limitations": "Packet time is local receive time, not simulation time. Residual includes map calibration, packet interpolation and game minimap update latency. It is not an isolated pure visual error. Static held references are labeled separately; no production delay correction is applied.",
    }
    moving = _analyze_bins(report, rows, residual)
    _fit_lag(report, moving, trajectory, residual)
    (args.output / "frames.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


def qualified(row):
    locate = row["result"]["locate"]
    return (
        locate is not None
        and locate["confidence"] >= 0.85
        and locate["candidates"][0]["full_ncc"] >= 0.65
        and locate["margin"] >= 0.08
    )


def residual_for(trajectory, row, offset=0, midpoint=False):
    s = row["sample"]
    truth = trajectory.at((s["call_midpoint_t"] if midpoint else s["frame_t"]) + offset, s)
    if not qualified(row) or truth is None:
        return None
    pred = row["result"]["locate"]
    return np.array([pred["world_x"], pred["world_z"]]) - truth[0]


def _load_trajectory(dataset):
    packets = {}
    for line in (dataset / "packet_trajectories.jsonl").read_text().splitlines():
        for p in json.loads(line)["trajectory"]:
            packets[(p["flow"], p["time"])] = p
    trajectory = Trajectory(list(packets.values()))
    return trajectory


def _inference_rows(args, samples, first, tracker):
    fingerprint = _inference_fingerprint(samples)
    if args.reuse_frames:
        rows = [json.loads(line) for line in (args.output / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
        _validate_reused_rows(rows, samples, fingerprint)
    else:
        rows = []
        for i, s in enumerate(samples):
            if s["map_image"] != first["map_image"] or s["map_id"] != first["map_id"]:
                raise ValueError("Split datasets on map changes before replay")
            frame = cv2.imread(str(args.dataset / s["frame"]))
            if frame is None:
                raise ValueError(s["frame"])
            begin = time.perf_counter()
            result = tracker.update(frame, MinimapGeometry.from_frame(frame.shape[1], frame.shape[0]), now=s["frame_t"])
            row = {
                "i": s["i"],
                "sample": s,
                "fingerprint": fingerprint,
                "result": result.to_dict(),
                "elapsed_ms": (time.perf_counter() - begin) * 1000,
                "speed_bin": speed_bin(s["speed"]),
            }
            rows.append(row)
            if i % 20 == 0:
                print(f"{i + 1}/{len(samples)}", flush=True)

    return rows


def _inference_fingerprint(samples):
    """Bind cached inference to source code and every map/mapping in the dataset."""
    paths = {
        Path(__file__),
        *(
            ROOT / path
            for path in (
                "src/localization/minimap_map_locator.py",
                "src/localization/minimap_odometry.py",
                "src/core/NavConfig.py",
            )
        ),
    }
    paths.update(Path(sample[key]) for sample in samples for key in ("map_image", "mapping"))
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def _validate_reused_rows(rows, samples, fingerprint):
    if len(rows) != len(samples) or any(r["sample"] != sample for r, sample in zip(rows, samples, strict=True)):
        raise ValueError("Saved inference does not match this dataset")
    if any(row.get("fingerprint") != fingerprint for row in rows):
        raise ValueError("Saved inference source or map fingerprint does not match")


def _analyze_bins(report, rows, residual):
    moving = []
    for group in sorted({r["speed_bin"] for r in rows}):
        selected = [r for r in rows if r["speed_bin"] == group]
        accepted = [r for r in selected if r["result"]["accepted"]]
        qualified_rows = [r for r in selected if qualified(r)]
        aligned = [r for r in qualified_rows if residual(r) is not None and residual(r, midpoint=True) is not None]
        qpc_errors = [float(np.linalg.norm(residual(r))) for r in aligned]
        entry = {
            "frames": len(selected),
            "accepted": len(accepted),
            "qualified_single_frames": len(qualified_rows),
            "paired_interpolated_frames": len(aligned),
            "speed_m_s": stats([r["sample"]["speed"] for r in selected]),
            "qpc_error_m": stats(qpc_errors),
            "call_midpoint_error_m_same_frames": stats(
                [float(np.linalg.norm(residual(r, midpoint=True))) for r in aligned]
            ),
            "wrong_gt_2m": sum(x > 2 for x in qpc_errors),
            "elapsed_ms": stats([r["elapsed_ms"] for r in selected]),
        }
        if group == "stationary":
            held = [r for r in accepted if r["sample"]["position_age_s"] <= 1]
            entry["stationary_reference_error_m_age_le_1s"] = stats(
                [
                    float(
                        np.hypot(
                            r["result"]["position"]["world_x"] - r["sample"]["x"],
                            r["result"]["position"]["world_z"] - r["sample"]["z"],
                        )
                    )
                    for r in held
                ]
            )
        report["bins"][group] = entry
        if group not in ("stationary", "transition"):
            moving.extend(aligned)
    return moving


def _cross_track_errors(rows, trajectory, residual, offset):
    cross = []
    for r in rows:
        velocity = trajectory.at(r["sample"]["frame_t"] + offset, r["sample"])[1]
        speed = np.linalg.norm(velocity)
        if speed > 0.5:
            e = residual(r, offset)
            cross.append(float(abs(e[0] * velocity[1] - e[1] * velocity[0]) / speed))
    return cross


def _lag_test_bins(report, test, trajectory, residual, offset):
    for group in report["bins"]:
        rs = [r for r in test if r["speed_bin"] == group]
        if not rs:
            continue
        cross = _cross_track_errors(rs, trajectory, residual, offset)
        report["lag_fit"]["test_bins"][group] = {
            "qpc_raw_error_m": stats([float(np.linalg.norm(residual(r))) for r in rs]),
            "lag_adjusted_error_m": stats([float(np.linalg.norm(residual(r, offset))) for r in rs]),
            "cross_track_error_m": stats(cross),
        }


def _fit_lag(report, moving, trajectory, residual):
    lags = np.round(np.arange(-0.15, 0.1501, 0.005), 6)
    common = [r for r in moving if all(residual(r, float(lag)) is not None for lag in lags)]
    train, test = [], []
    for group in report["bins"]:
        group_rows = sorted([r for r in common if r["speed_bin"] == group], key=lambda r: r["sample"]["frame_t"])
        split = len(group_rows) // 2
        train.extend(group_rows[:split])
        test.extend(group_rows[split:])
    if train and test:
        offset = float(min(lags, key=lambda lag: np.mean([np.linalg.norm(residual(r, float(lag))) for r in train])))
        report["lag_fit"] = {
            "offset_s": offset,
            "search_boundary_reached": abs(offset) >= 0.15,
            "train_frames": len(train),
            "test_frames": len(test),
            "method": "First temporal half within each speed bin trains one common offset; second half evaluates. No per-bin offset fitting.",
            "test_bins": {},
        }
        _lag_test_bins(report, test, trajectory, residual, offset)


if __name__ == "__main__":
    main()
