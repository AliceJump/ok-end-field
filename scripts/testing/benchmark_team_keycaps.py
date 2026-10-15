"""Replay in_team before/after a Git ref against the screenshot submodule."""

import argparse
import ast
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ok.feature.FeatureSet import FeatureSet  # noqa: E402

from src.tasks.mixin.battle_mixin import BattleMixin  # noqa: E402


class ReplayTask:
    _battle_feature_boxes = BattleMixin._battle_feature_boxes

    def __init__(self, frame, features, team_count):
        self.frame, self.features = frame, features
        self.height, self.width = frame.shape[:2]
        self._battle_team = ["known"] * team_count
        self._battle_member_count = 0

    def get_box_by_name(self, name):
        return self.features.get_box_by_name(self.frame, name)

    def get_feature_by_name(self, name):
        return self.features.get_feature_by_name(self.frame, name)

    def find_one(self, name, box):
        matches = self.features.find_one_feature(self.frame, name, box=box, threshold=0.8, limit=1)
        return matches[0] if matches else None

    def log_debug(self, message):
        pass


def load_baseline(ref):
    source = subprocess.run(
        ["git", "show", f"{ref}:src/tasks/mixin/battle_mixin.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout
    tree = ast.parse(source)
    mixin = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BattleMixin")
    method = next(node for node in mixin.body if isinstance(node, ast.FunctionDef) and node.name == "in_team")
    # Only execute the selected local baseline method; imports/task startup are excluded.
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "baseline-in-team", "exec"), namespace)
    return namespace["in_team"]


def aggregate(rows):
    result = {"frames": len(rows)}
    for name in ("before", "after"):
        values = [row[f"{name}_ms"] for row in rows]
        result[f"{name}_mean_ms"] = float(np.mean(values)) if values else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True, help="Screenshot submodule directory")
    parser.add_argument("--baseline", default="b4470848", help="Local Git ref containing the original in_team")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    logging.disable(logging.CRITICAL)
    cv2.setNumThreads(1)
    baseline = load_baseline(args.baseline)
    features = FeatureSet(False, str(ROOT / "assets/coco_annotations.json"), 0.002, 0.002, default_threshold=0.8)
    paths = sorted(args.assets.glob("*.png"))
    if not paths:
        parser.error("--assets contains no PNG screenshots")
    rows = []
    for index, path in enumerate(paths):
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError(f"Cannot decode {path}")
        initial = ReplayTask(frame, features, 0)
        positive = baseline(initial)
        for profile, count in (("cold", 0), ("known_team", initial._battle_member_count if positive else 4)):
            task = ReplayTask(frame, features, count)
            before = baseline(task), task._battle_member_count
            after = BattleMixin.in_team(task), task._battle_member_count
            samples = {"before": [], "after": []}
            for repeat in range(args.repeats):
                operations = [("before", baseline), ("after", BattleMixin.in_team)]
                if repeat % 2:
                    operations.reverse()
                for label, operation in operations:
                    task._battle_member_count = 0
                    start = time.perf_counter_ns()
                    operation(task)
                    samples[label].append((time.perf_counter_ns() - start) / 1e6)
            rows.append(
                {
                    "name": path.name,
                    "profile": profile,
                    "template_positive": positive,
                    "before_result": before,
                    "after_result": after,
                    **{f"{label}_ms": float(np.median(values)) for label, values in samples.items()},
                }
            )
        if (index + 1) % 40 == 0:
            print(f"Replayed {index + 1}/{len(paths)}", flush=True)
    report = {
        "baseline": args.baseline,
        "method": f"{args.repeats} alternating warm repetitions per loaded frame; one OpenCV thread; no decoding/logging in timings",
        "note": "Template results are comparison data, not ground truth; known_team size comes from the baseline positive or defaults to four.",
        "profiles": {},
        "disagreements": [row for row in rows if row["before_result"] != row["after_result"]],
        "rows": rows,
    }
    for profile in ("cold", "known_team"):
        selected = [row for row in rows if row["profile"] == profile]
        report["profiles"][profile] = {
            "all": aggregate(selected),
            "positive": aggregate([row for row in selected if row["template_positive"]]),
            "negative": aggregate([row for row in selected if not row["template_positive"]]),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"profiles": report["profiles"], "disagreements": report["disagreements"]}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
