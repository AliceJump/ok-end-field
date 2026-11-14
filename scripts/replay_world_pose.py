"""Replay native game captures through the independent production visual source.

Packet data is read only after prediction, for bracketed receive-time comparison.
No packet position/map ID seeds the visual search; no fitted delay is applied.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analyze_minimap_timing import Trajectory, speed_bin, stats  # noqa: E402
from src.localization.calibrated_map_store import CalibratedMapStore  # noqa: E402
from src.localization.visual_position_source import VisualPositionSource  # noqa: E402


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def trajectory_for(dataset):
    packets = {}
    path = dataset / "packet_trajectories.jsonl"
    if path.is_file():
        for row in read_rows(path):
            for packet in row["trajectory"]:
                packets[(packet["flow"], packet["time"])] = packet
    return Trajectory(list(packets.values()))


def replay(dataset, store, output):
    samples = read_rows(dataset / "samples.jsonl")
    map_id, locator = store.load()
    task = SimpleNamespace(width=0, height=0, active_time=lambda: 0)
    source = VisualPositionSource(task, locator, map_id, max_correction_m=40)
    rows = []
    for index, sample in enumerate(samples):
        frame = cv2.imread(str(dataset / sample["frame"]))
        if frame is None:
            raise ValueError(f"Unreadable replay frame: {sample['frame']}")
        task.height, task.width = frame.shape[:2]
        # Decode and truth IO are outside timing; all visual search/chain work is inside.
        begin = time.perf_counter()
        state = source.sample(frame, now=sample["frame_t"])
        elapsed_ms = (time.perf_counter() - begin) * 1000
        rows.append({"sample": sample, "pose": state, "elapsed_ms": elapsed_ms})
        if index % 25 == 0:
            print(f"{dataset.name}: {index + 1}/{len(samples)}", flush=True)
    # The truth reference is deliberately constructed only after all inference.
    trajectory = trajectory_for(dataset)
    paired = []
    for row in rows:
        sample, pose = row["sample"], row["pose"]
        truth = trajectory.at(sample["frame_t"], sample)
        if not pose["position_trusted"] or truth is None:
            continue
        error = float(np.linalg.norm(np.array([pose["x"], pose["z"]]) - truth[0]))
        row["error_m"] = error
        row["speed_bin"] = speed_bin(float(np.linalg.norm(truth[1])))
        row["wrong_map"] = pose["map_id"] != sample["map_id"]
        paired.append(row)
    trusted = [r for r in rows if r["pose"]["position_trusted"]]
    first_trusted = next((i for i, r in enumerate(rows) if r["pose"]["position_trusted"]), len(rows))
    report = {
        "frames": len(rows),
        "trusted": len(trusted),
        "reasons": dict(Counter(r["pose"]["trust_reason"] for r in rows)),
        "paired_frames": len(paired),
        "error_m": stats([r["error_m"] for r in paired]),
        "wrong_gt_2m": sum(r["error_m"] > 2 for r in paired),
        "wrong_map": sum(r["wrong_map"] for r in paired),
        "elapsed_ms": stats([r["elapsed_ms"] for r in rows]),
        "warm_elapsed_ms": stats([r["elapsed_ms"] for r in rows[first_trusted:]]),
        "first_sample_ms": rows[0]["elapsed_ms"] if rows else None,
        "acquisition_samples": first_trusted + 1 if trusted else None,
        "acquisition_work_ms": sum(r["elapsed_ms"] for r in rows[: first_trusted + 1]),
        "tiers": dict(Counter(r["pose"].get("tier_id") for r in trusted)),
        "bins": {
            group: {
                "error_m": stats([r["error_m"] for r in paired if r["speed_bin"] == group]),
                "frames": sum(r["speed_bin"] == group for r in paired),
            }
            for group in sorted({r["speed_bin"] for r in paired})
        },
        "truth_seeded": False,
        "limitations": "Packet receive time, <=0.5s interpolation brackets only; no extrapolation or delay correction. "
        "Residual includes map calibration and game/packet latency. Floor labels lack independent live truth. "
        "Replay tests the production visual algorithm; it does not emulate runtime frame-age rejection.",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "frames.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--map-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    store = CalibratedMapStore(args.map_root)
    reports = {p.name: replay(p, store, args.output / p.name) for p in args.dataset}
    (args.output / "summary.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
