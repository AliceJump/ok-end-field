"""Summarize effect commitment frames from the packaged, hash-verified timelines."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data.skill_timing import SNAPSHOT, SkillTimingStore, _effect_start_frame  # noqa: E402


def rebuild(path=SNAPSHOT):
    store = SkillTimingStore(path)
    records = store._all_records()
    for key, summary in store.index["skills"].items():
        summary["effect_start_frame"] = _effect_start_frame(records[key]["data"])
    (path / "index.json").write_text(json.dumps(store.index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(store.index["skills"])


if __name__ == "__main__":
    print(f"Indexed effect frames: {rebuild()}")
