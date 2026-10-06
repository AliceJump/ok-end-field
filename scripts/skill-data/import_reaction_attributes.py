"""Import an externally decoded CharacterTable selection after byte proof checks."""

import argparse
import shutil
from pathlib import Path

from src.data.native_damage_scalars import SNAPSHOT, read_scalar_snapshot


def build(source: Path, out: Path):
    records = read_scalar_snapshot(source)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("index.json", "scalars.json.gz", "CharacterTable.bytes.gz"):
        shutil.copyfile(source / name, out / name)
    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    print(f"Imported reaction attributes for {build(args.source, args.out)} native character IDs")
