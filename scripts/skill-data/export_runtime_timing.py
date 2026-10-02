"""Export compact timed-combat runtime data.

This is intentionally separate from runtime. A data/mining repository can vendor
or reimplement this exporter, then copy only the resulting .bin into the app
repository after each game-data update.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.skill_timing import SNAPSHOT, SkillTimingStore
from src.data.timing_runtime_binary import (
    DEFAULT_RUNTIME_BUNDLE,
    RuntimeTimingBundle,
    export_runtime_bundle,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=DEFAULT_RUNTIME_BUNDLE,
    )
    args = parser.parse_args()

    store = SkillTimingStore(SNAPSHOT)
    raw_size, binary_size = export_runtime_bundle(store, args.output)

    # Force a complete read-back and a representative partial-team query.
    bundle = RuntimeTimingBundle(path=args.output)
    probe = bundle.team(["梨诺", "?", "诀"])
    if len(probe["slots"]) != 3 or probe["slots"][1]["character_id"] is not None:
        raise RuntimeError("runtime bundle slot-preserving verification failed")

    source_size = sum(
        path.stat().st_size
        for path in (SNAPSHOT / "index.json", SNAPSHOT / "records.json.gz")
        if path.exists()
    )
    ratio = binary_size / source_size if source_size else 0
    print(f"runtime payload JSON: {raw_size:,} bytes")
    print(f"runtime binary:       {binary_size:,} bytes")
    if source_size:
        print(f"index + records.gz:   {source_size:,} bytes")
        print(f"binary/source ratio:  {ratio:.2%}")
    print(f"wrote: {args.output}")


if __name__ == "__main__":
    main()
