"""Reproducible synthetic benchmark; timings are observations, not CI assertions.

uv run --locked python -X utf8 scripts/benchmark_minimap_locator.py --iterations 20
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.localization import minimap_map_locator as current  # noqa: E402


def measure(callback, iterations):
    callback()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - start) * 1000)
    return {"p50_ms": float(np.percentile(samples, 50)), "p95_ms": float(np.percentile(samples, 95))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--baseline-ref", default="e8588b7e")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    source = subprocess.check_output(
        ["git", "show", f"{args.baseline_ref}:src/localization/minimap_map_locator.py"],
        cwd=ROOT,
    ).decode("utf-8")
    baseline = types.ModuleType("_minimap_benchmark_baseline")
    sys.modules[baseline.__name__] = baseline
    exec(compile(source, "<baseline>", "exec"), baseline.__dict__)
    cv2.setNumThreads(1)
    rng = np.random.default_rng(42)
    image = cv2.GaussianBlur(rng.integers(20, 230, (1600, 1800), dtype=np.uint8), (3, 3), 0)
    frame = np.full((1080, 1920, 3), 127, dtype=np.uint8)
    frame[57:144, 57:144] = cv2.cvtColor(image[757:844, 857:944], cv2.COLOR_GRAY2BGR)
    report = {
        "iterations": args.iterations,
        "baseline_ref": args.baseline_ref,
        "opencv": cv2.__version__,
        "opencv_threads": 1,
        "synthetic": True,
    }
    for name, module in [("baseline", baseline), ("optimized", current)]:
        calibration = module.MapCalibration(
            np.array([[0.25, 0, 0], [0, -0.25, 0]]),
            np.array([[4.0, 0, 0], [0, -4.0, 0]]),
        )
        locator = module.MinimapMapLocator(image, calibration, scale_override=1)
        geometry = module.MinimapGeometry(100, 100, 50)
        report[name + "_prepare"] = measure(
            lambda locator=locator, geometry=geometry: locator._prepare_template(frame, geometry), args.iterations
        )
        tracker = module.MinimapTracker(locator)

        def track(tracker=tracker, module=module, geometry=geometry):
            tracker._set_convinced(module.MapCandidate(900, 800, 225, -200, 0.9), 0)
            return tracker.update(frame, geometry, now=0.1)

        report[name + "_track"] = measure(track, args.iterations)
        result = track()
        report[name + "_track"]["accepted"] = result.accepted
        report[name + "_track"]["map_error_px"] = (
            float(
                np.hypot(
                    result.position.map_x - 900,
                    result.position.map_y - 800,
                )
            )
            if result.position
            else None
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
