"""Replay recorded ring images; packet snapshots are read only for final scoring.

This validates relative visual evidence, not full HUD/NCC acquisition. Seed the
first image at an arbitrary visual origin; no logged coordinate/odometry is a
prediction input. Stop at the first lost certificate, without synthetic reanchors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.localization.minimap_odometry import phase_shift  # noqa: E402
from src.localization.minimap_sample_stitch import _load_patch_node  # noqa: E402
from src.localization.minimap_visual_anchor_chain import MinimapVisualChainEvidence  # noqa: E402


def replay(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scale = float(rows[0]["scale_m_per_px"])
    evidence = MinimapVisualChainEvidence(scale_m_per_px=scale)
    previous = None
    position = np.zeros(2)
    trace = []
    patch_hash = hashlib.sha256()
    for index, record in enumerate(rows):
        node = _load_patch_node(index, record, path.parent)
        if node is None:
            raise ValueError(f"Missing ring image: {path.parent / record['patch']}")
        patch_hash.update(node.path.read_bytes())
        predicted = position.copy()
        if previous is not None:
            dx, dy, _ = phase_shift(previous.gray, node.gray, np.minimum(previous.alpha, node.alpha))
            predicted -= (dx, dy)
        started = time.perf_counter()
        result = evidence.observe(
            time=float(record["t"]),
            gray=node.gray,
            alpha=node.alpha,
            position=predicted,
            anchor=(0, 0) if index == 0 else None,
        )
        trace.append(
            {
                "index": index,
                "t": record["t"],
                "trusted": result.trusted,
                "reason": result.reason,
                "visual_px": result.position.tolist() if result.trusted else None,
                "error_allowance_m": result.error_m if math.isfinite(result.error_m) else None,
                "keyframe_checks": result.keyframe_checks,
                "verification_ms": (time.perf_counter() - started) * 1000,
            }
        )
        if not result.trusted:
            break
        previous, position = node, result.position

    # Scoring boundary: packet snapshots never enter the replay loop above.
    reference = next((i for i, row in enumerate(trace) if row["trusted"] and rows[i].get("ws")), None)
    scores = []
    updated_scores = []
    if reference is not None:
        origin_truth = np.asarray(rows[reference]["ws"], dtype=float)
        origin_visual = np.asarray(trace[reference]["visual_px"])
        for i, row in enumerate(trace):
            truth = rows[i].get("ws")
            if row["trusted"] and truth:
                matrix = np.asarray(rows[i]["map_to_world_px"], dtype=float)
                visual_delta = matrix @ (np.asarray(row["visual_px"]) - origin_visual)
                error = float(np.linalg.norm(visual_delta - (np.asarray(truth) - origin_truth)))
                row["logged_snapshot_error_m"] = error
                scores.append(error)
                if i == reference or truth != rows[i - 1].get("ws"):
                    updated_scores.append(error)
    trusted = [row for row in trace if row["trusted"]]
    return {
        "recording": str(path),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "replayed_patch_sha256": patch_hash.hexdigest(),
        "available_frames": len(rows),
        "trusted_prefix_frames": len(trusted),
        "trusted_prefix_s": trusted[-1]["t"] - trusted[0]["t"] if trusted else 0,
        "failure": trace[-1]["reason"] if not trace[-1]["trusted"] else None,
        "max_logged_snapshot_error_m": max(scores) if scores else None,
        "max_changed_snapshot_error_m": max(updated_scores) if updated_scores else None,
        "changed_snapshot_score_count": len(updated_scores),
        "scored_frames": len(scores),
        "trace": trace,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [replay(path) for path in args.recordings]
    report = {
        "kind": "recorded relative-ring replay, not full HUD navigation",
        "limitations": [
            "Packet snapshots may have capture latency; errors are descriptive, not calibrated bounds.",
            "Held packet coordinates visibly lag moving images; unchanged snapshots are not frame truth.",
            "The first ring seeds an arbitrary origin, not a visual world-coordinate acquisition.",
            "The historical factory failure has no full consecutive HUD recording.",
        ],
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("src/localization/minimap_visual_anchor_chain.py", "scripts/nav/replay_visual_chain.py")
        },
        "recordings": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            [{key: value for key, value in row.items() if key != "trace"} for row in results],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
