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

from scripts.nav.path_safety import resolve_under  # noqa: E402
from src.localization.minimap_odometry import phase_shift  # noqa: E402
from src.localization.minimap_sample_stitch import _load_patch_node  # noqa: E402
from src.localization.minimap_visual_anchor_chain import MinimapVisualChainEvidence  # noqa: E402


def _replay_trace(path, rows, *, max_error_m):
    """Replay visual edges without using packet coordinates as inputs."""

    scale = float(rows[0]["scale_m_per_px"])
    evidence = MinimapVisualChainEvidence(scale_m_per_px=scale, max_error_m=max_error_m)
    previous = None
    position = np.zeros(2)
    trace = []
    patch_hash = hashlib.sha256()
    for index, record in enumerate(rows):
        boundary = next((key for key in ("map_id", "segment") if record.get(key) != rows[0].get(key)), None)
        if boundary is not None:
            trace.append({"index": index, "t": record["t"], "trusted": False, "reason": f"{boundary}_changed"})
            break
        record = dict(record)
        record["patch"] = str(
            resolve_under(
                path.parent,
                record.get("patch") or "",
                label="patch",
                must_exist=True,
                relative_to_root=True,
            )
        )
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
    return trace, patch_hash


def _score_trusted_row(index, rows, trace, reference, origin_truth, origin_visual):
    row = trace[index]
    truth = rows[index].get("ws")
    if not row["trusted"] or not truth:
        return None
    matrix = np.asarray(rows[index]["map_to_world_px"], dtype=float)
    visual_delta = matrix @ (np.asarray(row["visual_px"]) - origin_visual)
    error = float(np.linalg.norm(visual_delta - (np.asarray(truth) - origin_truth)))
    row["logged_snapshot_error_m"] = error
    return error, index == reference or truth != rows[index - 1].get("ws")


def _score_trace(rows, trace):
    """Score only after replay ends so packet coordinates never affect it."""

    # Scoring boundary: packet snapshots never enter the replay loop above.
    reference = next((i for i, row in enumerate(trace) if row["trusted"] and rows[i].get("ws")), None)
    scores = []
    updated_scores = []
    if reference is not None:
        origin_truth = np.asarray(rows[reference]["ws"], dtype=float)
        origin_visual = np.asarray(trace[reference]["visual_px"])
        for i in range(len(trace)):
            scored = _score_trusted_row(i, rows, trace, reference, origin_truth, origin_visual)
            if scored is not None:
                error, changed = scored
                scores.append(error)
                if changed:
                    updated_scores.append(error)
    trusted = [row for row in trace if row["trusted"]]
    return {
        "available_frames": len(rows),
        "trusted_prefix_frames": len(trusted),
        "trusted_prefix_s": trusted[-1]["t"] - trusted[0]["t"] if trusted else 0,
        "failure": trace[-1]["reason"] if not trace[-1]["trusted"] else None,
        "max_logged_snapshot_error_m": max(scores) if scores else None,
        "max_changed_snapshot_error_m": max(updated_scores) if updated_scores else None,
        "changed_snapshot_score_count": len(updated_scores),
        "scored_frames": len(scores),
    }


def replay(path: Path, *, root: Path = ROOT, max_error_m=40.0) -> dict:
    path = resolve_under(root, path, label="recording", must_exist=True)
    max_error_m = float(max_error_m)
    if not math.isfinite(max_error_m) or max_error_m <= 0:
        raise ValueError("max_error_m must be finite and positive")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    trace, patch_hash = _replay_trace(path, rows, max_error_m=max_error_m)
    return {
        "recording": str(path),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "replayed_patch_sha256": patch_hash.hexdigest(),
        **_score_trace(rows, trace),
        "max_error_m": max_error_m,
        "trace": trace,
    }


def _recorded_patches(recording):
    """Yield every referenced input image, including frames beyond a failed prefix."""
    for line in recording.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            yield resolve_under(
                recording.parent,
                row.get("patch") or "",
                label="patch",
                relative_to_root=True,
            )


def _validate_output_not_input(recordings, output):
    """Reject path aliases and hard links to recordings or referenced images."""
    output_exists = output.exists()
    for recording in recordings:
        if output == recording or (output_exists and output.samefile(recording)):
            raise ValueError("output_must_not_overwrite_recording")
        for patch in _recorded_patches(recording):
            if output == patch or (output_exists and patch.exists() and output.samefile(patch)):
                raise ValueError("output_must_not_overwrite_patch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-error-m", type=float, default=40.0)
    args = parser.parse_args()
    recordings = [resolve_under(ROOT, path, label="recording", must_exist=True) for path in args.recordings]
    output = resolve_under(ROOT, args.output, label="output")
    _validate_output_not_input(recordings, output)
    results = [replay(path, max_error_m=args.max_error_m) for path in recordings]
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            [{key: value for key, value in row.items() if key != "trace"} for row in results],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
