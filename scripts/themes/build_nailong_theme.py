"""Rebuild the standalone Nailong theme ZIP without flattening assets/ paths."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "theme-packs" / "nailong"
MEMBERS = (
    "theme.json",
    "style.qss",
    "assets/background.webp",
    "assets/standing.webp",
    "assets/working.webp",
)


def build_theme_zip(output: Path, source: Path = SOURCE) -> None:
    """Create a deterministic ZIP preserving the relative member names."""
    output = Path(output)
    with zipfile.ZipFile(output, "w") as archive:
        for name in MEMBERS:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            archive.writestr(entry, (source / name).read_bytes())


def main() -> int:
    """Build the checked-in sample package, or an alternate output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE / "nailong.okef-theme.zip")
    args = parser.parse_args()
    build_theme_zip(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
