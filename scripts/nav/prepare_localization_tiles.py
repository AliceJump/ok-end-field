"""Prepare exact global overviews and lossless WebP tiles before navigation."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.localization.tile_preparation import DEFAULT_WIDTHS, prepare_bundle
from src.localization.tiled_map_image import CACHE_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("assets/nav-grid-data/localization"))
    parser.add_argument("--cache-root", type=Path, default=CACHE_ROOT)
    parser.add_argument("--screen-widths", type=int, nargs="+", default=DEFAULT_WIDTHS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    for result in prepare_bundle(args.root, args.screen_widths, cache_root=args.cache_root, force=args.force):
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
