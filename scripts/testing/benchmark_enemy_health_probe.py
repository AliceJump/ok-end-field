"""Compare enemy-presence probe revisions on the same predecoded screenshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import types
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "src/image/enemy_health_probe.py"
sys.path.insert(0, str(ROOT))


class ProbeTask:
    """Supply a static frame with live overlays and debug capture disabled."""

    def __init__(self, frame):
        self.frame = frame
        self.config = {}


def git_text(*args, cwd=ROOT):
    return subprocess.check_output(["git", *args], cwd=cwd).decode("utf-8").strip()


def load_probe(label, ref):
    """Load independent module globals and record the exact measured source."""
    if ref is None:
        source = (ROOT / SOURCE).read_text(encoding="utf-8")
        commit = git_text("rev-parse", "HEAD")
    else:
        commit = git_text("rev-parse", "--verify", f"{ref}^{{commit}}")
        source = subprocess.check_output(["git", "show", f"{commit}:{SOURCE}"], cwd=ROOT).decode("utf-8")
    module = types.ModuleType(f"benchmark_enemy_hp_{label}")
    exec(compile(source, f"{label}/{SOURCE}", "exec"), module.__dict__)
    return module, {"commit": commit, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()}


def image_paths(templates, include_untracked):
    """Use tracked assets by default and explicitly identify local additions."""
    tracked = set(git_text("ls-files", "-z", cwd=templates).split("\0"))
    names = set(tracked)
    if include_untracked:
        names.update(path.relative_to(templates).as_posix() for path in templates.rglob("*") if path.is_file())
    for name in sorted(names):
        path = templates / name
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.is_file():
            yield path, name in tracked


def synthetic_frames():
    """Keep correctness/stress controls separate from the real-image corpus."""
    pink = (102, 68, 255)
    for scale in (1, 2):
        frame = np.zeros((1080 * scale, 1920 * scale, 3), dtype=np.uint8)
        frame[210 * scale : 215 * scale, 100 * scale : 150 * scale] = pink
        frame[180 * scale : 280 * scale, 104 * scale : 105 * scale] = pink
        yield f"connected_bar_{1080 * scale}p", frame
    for name in ("thin_rows", "tall_narrow", "solid_block", "residual_context", "middle_band_no_context"):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        if name == "thin_rows":
            for y in range(130, 190, 3):
                frame[y, 20:28] = pink
            frame[205:210, 100:150] = pink
        elif name == "tall_narrow":
            frame[130:178, 20:28] = pink
            frame[205:210, 100:150] = pink
        elif name == "solid_block":
            frame[151:181, 100:200] = pink
        elif name == "residual_context":
            frame[151:155, 100:108] = pink
            frame[160:163, 80:170] = (220, 220, 220)
        else:
            frame[151:156, 100:149] = pink
        yield name, frame


def region_hits(module, frame):
    """Inspect all slices once; this sweep is outside the timed probe calls."""
    height, width = frame.shape[:2]
    regions = [module.ENEMY_BOSS_HP_REGION, *(module._normal_slice_region(i) for i in range(4))]
    return [
        module._probe_region(
            frame, region, width, height, context_region=None if i == 0 else module.ENEMY_NORMAL_HP_REGION
        )[1]
        for i, region in enumerate(regions)
    ]


def measure_frame(frame, modules, iterations, repeats, warmup, frame_index):
    """Alternate revisions per block and time cold/steady calls separately."""
    results = {
        label: {"region_hits": region_hits(module, frame), "timings_us": {}} for label, module in modules.items()
    }
    labels = list(modules)
    for mode in ("cold", "steady"):
        tasks = {label: ProbeTask(frame) for label in labels}
        timings = {label: [] for label in labels}
        for label, module in modules.items():
            for _ in range(warmup):
                module.probe_enemy_presence_fast(tasks[label])
        for repeat in range(repeats):
            offset = (frame_index + repeat) % len(labels)
            for label in labels[offset:] + labels[:offset]:
                module = modules[label]
                task = tasks[label]
                reset = module.reset_enemy_presence_probe
                probe = module.probe_enemy_presence_fast
                start = perf_counter_ns()
                for _ in range(iterations):
                    if mode == "cold":
                        reset(task)
                    state = probe(task)
                timings[label].append((perf_counter_ns() - start) / (iterations * 1000))
                results[label][f"{mode}_state"] = state.value
        for label in labels:
            results[label]["timings_us"][mode] = {
                "blocks": timings[label],
                "median": float(np.median(timings[label])),
            }
    return results


def aggregate(rows, labels):
    """Summarize per-image block medians; each image has equal weight."""
    if not rows:
        return {"frames": 0}
    summary = {"frames": len(rows)}
    for mode in ("cold", "steady"):
        summary[mode] = {}
        for label in labels:
            samples = [row["versions"][label]["timings_us"][mode]["median"] for row in rows]
            summary[mode][label] = {
                "mean_us": float(np.mean(samples)),
                "median_us": float(np.median(samples)),
                "p95_us": float(np.percentile(samples, 95)),
                "max_us": float(max(samples)),
            }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, default=ROOT / "ok_templates")
    parser.add_argument("--baseline", required=True, help="Commit/ref before the PR")
    parser.add_argument("--incoming", help="Optional PR head before follow-up fixes")
    parser.add_argument("--include-untracked", action="store_true")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=12)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/enemy-health-benchmark/report.json")
    args = parser.parse_args()
    if min(args.iterations, args.repeats) < 1 or args.warmup < 8:
        parser.error("iterations/repeats must be positive; warmup must cover at least eight probes")
    templates = args.templates.resolve()
    versions = [("baseline", args.baseline)]
    if args.incoming:
        versions.append(("incoming", args.incoming))
    versions.append(("current", None))
    modules = {}
    metadata = {}
    for label, ref in versions:
        modules[label], metadata[label] = load_probe(label, ref)
    paths = list(image_paths(templates, args.include_untracked))
    if not paths:
        parser.error(f"No screenshots found in {templates}")
    # Keep OpenCV CPU parallelism identical across modules and across runs.
    cv2.setNumThreads(1)
    report = {
        "schema": "enemy-health-benchmark/v1",
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "processor": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
        "python": platform.python_version(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "opencv_threads": cv2.getNumThreads(),
        "asset_commit": git_text("rev-parse", "HEAD", cwd=templates),
        "versions": metadata,
        "method": {
            "iterations": args.iterations,
            "repeats": args.repeats,
            "warmup": args.warmup,
            "cold": "Reset probe state and make one probe call; reset overhead included equally.",
            "steady": "Probe a static screenshot after warmup; cached slice/absence state retained.",
            "timing": "Per-image median of block means in microseconds; aggregate images equally.",
            "excluded": "Image decoding, region sweep, overlays, debug artifacts and game capture.",
        },
        "images": [],
        "synthetic": [],
    }
    for index, (path, tracked) in enumerate(paths):
        encoded = np.fromfile(path, dtype=np.uint8)
        frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Could not decode {path}")
        report["images"].append(
            {
                "name": path.relative_to(templates).as_posix(),
                "tracked": tracked,
                "image_sha256": hashlib.sha256(encoded.tobytes()).hexdigest(),
                "width": frame.shape[1],
                "height": frame.shape[0],
                "versions": measure_frame(frame, modules, args.iterations, args.repeats, args.warmup, index),
            }
        )
        if (index + 1) % 20 == 0:
            print(f"Measured {index + 1}/{len(paths)} screenshots", flush=True)
    for index, (name, frame) in enumerate(synthetic_frames()):
        report["synthetic"].append(
            {
                "name": name,
                "width": frame.shape[1],
                "height": frame.shape[0],
                "versions": measure_frame(frame, modules, args.iterations, args.repeats, args.warmup, index),
            }
        )
    rows = report["images"]
    report["resolution_counts"] = dict(Counter(f"{row['width']}x{row['height']}" for row in rows))
    groups = {
        "all_real": rows,
        "tracked": [row for row in rows if row["tracked"]],
        "local_only": [row for row in rows if not row["tracked"]],
    }
    for resolution in report["resolution_counts"]:
        groups[resolution] = [row for row in rows if f"{row['width']}x{row['height']}" == resolution]
    for hit in (True, False):
        groups["any_hit" if hit else "no_hit"] = [
            row for row in rows if any(any(value["region_hits"]) for value in row["versions"].values()) == hit
        ]
    report["summary"] = {name: aggregate(group, modules) for name, group in groups.items()}
    report["detected_images"] = {
        label: sum(any(row["versions"][label]["region_hits"]) for row in rows) for label in modules
    }
    report["presence_differences"] = [
        row["name"] for row in rows if len({bool(any(value["region_hits"])) for value in row["versions"].values()}) > 1
    ]
    report["probe_state_differences"] = {
        mode: [row["name"] for row in rows if len({value[f"{mode}_state"] for value in row["versions"].values()}) > 1]
        for mode in ("cold", "steady")
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "detected_images": report["detected_images"],
                "presence_differences": report["presence_differences"],
                "probe_state_differences": report["probe_state_differences"],
                "summary": report["summary"]["all_real"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
