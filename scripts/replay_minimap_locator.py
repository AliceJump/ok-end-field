"""Replay recorded game frames against baseline/current locators without truth seeding.

uv run --locked python -X utf8 scripts/replay_minimap_locator.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import types
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.localization import minimap_map_locator as current  # noqa: E402


def stats(values):
    if not values:
        return None
    return {
        "n": len(values),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "max": float(max(values)),
    }


def summarize(rows):
    accepted = [r for r in rows if r["accepted"]]
    reference = [r for r in accepted if not r["loading"]]
    fresh = [r for r in reference if r["position_age_s"] <= 0.5]
    return {
        "frames": len(rows),
        "accepted": len(accepted),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "reference_accepted": len(reference),
        "reference_error_m": stats([r["error_m"] for r in reference]),
        "reference_wrong_gt_2m": sum(r["error_m"] > 2 for r in reference),
        "reference_wrong_rate_gt_2m": sum(r["error_m"] > 2 for r in reference) / len(reference) if reference else None,
        "fresh_accepted": len(fresh),
        "fresh_error_m": stats([r["error_m"] for r in fresh]),
        "loading_frames": sum(r["loading"] for r in rows),
        "loading_false_accepts": sum(r["loading"] for r in accepted),
        "elapsed_ms": stats([r["elapsed_ms"] for r in rows]),
        "accepted_elapsed_ms": stats([r["elapsed_ms"] for r in accepted]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        nargs="+",
        type=Path,
        default=[
            ROOT / "logs/minimap_ground_truth/live_20261021_210846",
            ROOT / "logs/minimap_ground_truth/live_20261021_2112",
        ],
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "logs/minimap_replay" / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    parser.add_argument(
        "--exclude-loading",
        action="store_true",
        help="Diagnostic only: omit manually verified loading frames 1-2 in the first dataset",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "show", "e8588b7e:src/localization/minimap_map_locator.py"], cwd=ROOT
    ).decode("utf-8")
    baseline = types.ModuleType("_replay_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, "<baseline>", "exec"), baseline.__dict__)
    cv2.setNumThreads(1)
    report = {
        "baseline_ref": "e8588b7e",
        "current_source_sha256": hashlib.sha256(
            (ROOT / "src/localization/minimap_map_locator.py").read_bytes()
        ).hexdigest(),
        "opencv": cv2.__version__,
        "threads": 1,
        "truth_seeded": False,
        "exclude_manually_labeled_loading": args.exclude_loading,
        "wrong_threshold_m": 2,
        "fresh_age_limit_s": 0.5,
        "limitations": "Stationary packet references only; one coordinate per dataset. First dataset frames 1-2 manually labeled loading. Freshness <=0.5s reported separately. Timing excludes image decoding/map loading; includes all localization and tracker work.",
        "datasets": {},
    }
    all_rows = []
    for dataset in args.datasets:
        samples = [json.loads(line) for line in (dataset / "samples.jsonl").read_text().splitlines() if line.strip()]
        if not samples:
            continue
        first = samples[0]
        image = cv2.imread(first["map_image"], cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError(first["map_image"])
        mapping = json.loads(Path(first["mapping"]).read_text(encoding="utf-8"))
        if image.shape != (mapping["image_size"]["height"], mapping["image_size"]["width"]):
            raise ValueError("Map size mismatch")
        models = {}
        for label, module in [("baseline", baseline), ("optimized", current)]:
            calibration = module.MapCalibration.from_mapping_file(first["mapping"])
            locator = module.MinimapMapLocator(image, calibration)
            models[label] = (module, locator, module.MinimapTracker(locator))
        rows = []
        for index, sample in enumerate(samples):
            frame = cv2.imread(str(dataset / sample["frame"]))
            if frame is None:
                raise ValueError(sample["frame"])
            if args.exclude_loading and dataset.name == "live_20261021_210846" and sample["i"] in (1, 2):
                continue
            # Alternate evaluation order to reduce persistent order bias.
            for label in ("baseline", "optimized") if index % 2 == 0 else ("optimized", "baseline"):
                module, locator, tracker = models[label]
                geometry = module.MinimapGeometry.from_frame(frame.shape[1], frame.shape[0])
                start = time.perf_counter()
                result = tracker.update(frame, geometry, now=sample["frame_t"])
                elapsed = (time.perf_counter() - start) * 1000
                prediction = result.position
                row = {
                    "dataset": dataset.name,
                    "i": sample["i"],
                    "model": label,
                    "status": result.status,
                    "accepted": result.accepted,
                    "loading": dataset.name == "live_20261021_210846" and sample["i"] in (1, 2),
                    "position_age_s": sample["position_age_s"],
                    "elapsed_ms": elapsed,
                    "error_m": math.hypot(prediction.world_x - sample["x"], prediction.world_z - sample["z"])
                    if prediction
                    else None,
                    "result": result.to_dict(),
                }
                rows.append(row)
            if index % 10 == 0:
                print(f"{dataset.name}: {index + 1}/{len(samples)}", flush=True)
        # Independent cold global check for timestamp-fresh samples (no temporal promotion).
        cold = []
        for sample in samples:
            if sample["position_age_s"] > 0.5:
                continue
            frame = cv2.imread(str(dataset / sample["frame"]))
            for label, (module, locator, _) in models.items():
                start = time.perf_counter()
                result = locator.locate_global(frame, module.MinimapGeometry.from_frame(frame.shape[1], frame.shape[0]))
                cold.append(
                    {
                        "i": sample["i"],
                        "model": label,
                        "position_age_s": sample["position_age_s"],
                        "elapsed_ms": (time.perf_counter() - start) * 1000,
                        "error_m": math.hypot(result.best.world_x - sample["x"], result.best.world_z - sample["z"])
                        if result
                        else None,
                        "result": result.to_dict() if result else None,
                    }
                )
        report["datasets"][dataset.name] = {
            "samples": len(samples),
            "fresh_samples": sum(s["position_age_s"] <= 0.5 for s in samples),
            "unique_positions": len({(s["x"], s["z"]) for s in samples}),
            "truth_methods": dict(Counter(s.get("truth_method", "recorded_stationary_reference") for s in samples)),
            "capture_interval_s": stats([s["capture_interval_s"] for s in samples if "capture_interval_s" in s]),
            "capture_timing_uncertainty_m": stats(
                [s["timing_uncertainty_m"] for s in samples if "timing_uncertainty_m" in s]
            ),
            "map_image": first["map_image"],
            "mapping": first["mapping"],
            "age_s": stats([s["position_age_s"] for s in samples]),
            "baseline": summarize([r for r in rows if r["model"] == "baseline"]),
            "optimized": summarize([r for r in rows if r["model"] == "optimized"]),
            "fresh_cold_global": cold,
        }
        all_rows.extend(rows)
    (args.output / "frames.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n", encoding="utf-8"
    )
    (args.output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("REPORT", args.output, flush=True)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
