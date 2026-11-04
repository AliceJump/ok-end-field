"""Offline absolute minimap localization against a calibrated large map.

Example:

    uv run --locked python -X utf8 scripts/minimap_map_locate.py \
        --frame shot.png \
        --map nav-grid-editor/assets/tiles/maps/map02/4/map02_4.png \
        --mapping nav-grid-editor/assets/tiles/maps/map02/4/map02_4_mapping.json \
        --center-x 162.35 --center-y 211.52 --outer-radius 87.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.localization.minimap_map_locator import (  # noqa: E402
    MIN_ACCEPT_MARGIN,
    MIN_ACCEPT_NCC,
    MIN_VALID_NCC,
    RING_INNER_RATIO,
    RING_OUTER_RATIO,
    MinimapGeometry,
    MinimapMapLocator,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--center-x", type=float)
    parser.add_argument("--center-y", type=float)
    parser.add_argument("--outer-radius", type=float)
    parser.add_argument("--inner-ring-ratio", type=float, default=RING_INNER_RATIO)
    parser.add_argument("--outer-ring-ratio", type=float, default=RING_OUTER_RATIO)
    parser.add_argument("--previous-world", nargs=2, type=float)
    parser.add_argument("--search-radius-m", type=float, default=80.0)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    frame = cv2.imread(args.frame, cv2.IMREAD_UNCHANGED)
    if frame is None:
        raise SystemExit(f"Could not read frame: {args.frame}")
    explicit = (args.center_x is not None, args.center_y is not None, args.outer_radius is not None)
    if any(explicit) and not all(explicit):
        raise SystemExit("--center-x, --center-y and --outer-radius must be supplied together")
    if all(explicit):
        geometry = MinimapGeometry(
            center_x=args.center_x,
            center_y=args.center_y,
            outer_radius=args.outer_radius,
            ring_inner_ratio=args.inner_ring_ratio,
            ring_outer_ratio=args.outer_ring_ratio,
        )
    else:
        height, width = frame.shape[:2]
        geometry = MinimapGeometry.from_frame(
            width,
            height,
            ring_inner_ratio=args.inner_ring_ratio,
            ring_outer_ratio=args.outer_ring_ratio,
        )

    locator = MinimapMapLocator.from_files(
        args.map,
        args.mapping,
        top_k=args.top_k,
        min_valid_ncc=MIN_VALID_NCC,
        min_accept_ncc=MIN_ACCEPT_NCC,
        min_accept_margin=MIN_ACCEPT_MARGIN,
    )
    if args.previous_world:
        result = locator.locate_near(
            frame,
            tuple(args.previous_world),
            geometry,
            search_radius_m=args.search_radius_m,
        )
    else:
        result = locator.locate_global(frame, geometry)
    if result is None:
        if args.json:
            print(json.dumps({"status": "rejected", "reason": "invalid frame or geometry"}, ensure_ascii=False))
        else:
            print("rejected: invalid frame or minimap geometry")
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            f"{result.status}: world=({result.best.world_x:.2f}, {result.best.world_z:.2f}) "
            f"map_px=({result.best.map_x:.2f}, {result.best.map_y:.2f}) "
            f"NCC={result.confidence:.4f} full={result.best.full_ncc:.4f} "
            f"inlier={result.best.inlier_ratio:.2f} margin={result.margin:.4f} "
            f"elapsed={result.elapsed_s:.3f}s scale={result.scale:.6f}"
        )
        for index, candidate in enumerate(result.candidates, 1):
            print(
                f"  #{index} world=({candidate.world_x:.2f}, {candidate.world_z:.2f}) "
                f"map_px=({candidate.map_x:.2f}, {candidate.map_y:.2f}) "
                f"NCC={candidate.ncc:.4f} full={candidate.full_ncc:.4f} "
                f"inlier={candidate.inlier_ratio:.2f} coarse={candidate.coarse_score:.4f}"
            )
    return 0 if result.status != "rejected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
