"""Select captured operator data without silently consuming partial captures."""

import json
from pathlib import Path


def resolve_operator_snapshot(root: Path, snapshot: str | None = None, *, allow_partial: bool = False) -> Path:
    root = root.resolve()
    if snapshot is None:
        latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
        snapshot = latest["snapshot"]
    if not isinstance(snapshot, str) or not snapshot:
        raise ValueError("Invalid snapshot name")
    path = (root / snapshot).resolve()
    if path.parent != root:
        raise ValueError("Snapshot must be a direct child of the capture directory")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or (manifest.get("complete") is not True and not allow_partial):
        raise ValueError(f"Incomplete operator snapshot: {path}")
    return path
