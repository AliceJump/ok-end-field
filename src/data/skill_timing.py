"""Native skill timelines. Frame boundaries are scheduling hints, not hit proofs."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/skill_timings/20261002"
_SUFFIX = {"battle": "normal_skill", "link": "combo_skill", "ult": "ultimate_skill"}


@dataclass(frozen=True)
class SkillTiming:
    skill_id: str
    duration: float
    exclusive: float
    cooldown: float
    skill_points: int | None
    allow_next: tuple[tuple[float, float, tuple[str, ...]], ...]
    sp_cost: float | None = None

    def allows(self, elapsed: float, candidates: tuple[SkillTiming, ...]) -> bool:
        if elapsed < max(0.3, self.exclusive):
            return False
        if elapsed >= self.duration:
            return True
        return bool(candidates) and any(
            start <= elapsed <= end and all(candidate.skill_id in allowed for candidate in candidates)
            for start, end, allowed in self.allow_next
        )


class SkillTimingStore:
    def __init__(self, path: Path = SNAPSHOT):
        self.path = path
        self.index = json.loads((path / "index.json").read_text(encoding="utf-8"))
        if self.index["schema_version"] != 1 or self.index["frames_per_second"] != 30:
            raise ValueError("Unsupported timing snapshot")
        verification = self.index["verification"]
        if not verification["all_reached_eof"] or verification["binary_roundtrip"]["failures"]:
            raise ValueError("Unverified timing snapshot")
        self._records = None

    def profiles(self, character: str, kind: str) -> tuple[SkillTiming, ...]:
        """Keep both administrator variants; never guess gender from a shared portrait."""
        profiles = []
        fps = self.index["frames_per_second"]
        for cid, row in self.index["characters"].items():
            if character not in (row["name"], row["key"], row["english_name"], cid):
                continue
            skill_id = f"{cid}_{_SUFFIX[kind]}"
            data = self.index["skills"].get(skill_id)
            if data is None:
                continue
            casts = data["level_patches"] or [
                {
                    "coolDown": data["raw_cast_data"]["cooldownTime"],
                    **data["raw_cast_data"]["costData"],
                }
            ]
            # One complete HUD skill bar corresponds to 100 SP. Unknown cost types
            # are deliberately not translated into skill-point readiness.
            costs = [
                math.ceil(cast["costValue"] / 100) if cast["costType"] == 1 else 0 if cast["costValue"] == 0 else None
                for cast in casts
            ]
            profiles.append(
                SkillTiming(
                    skill_id=skill_id,
                    duration=max(0, data["duration_frame"]) / fps,
                    exclusive=max(0, data["exclusive_frame"]) / fps,
                    cooldown=max(cast["coolDown"] for cast in casts),
                    skill_points=None if None in costs else max(costs),
                    sp_cost=None if None in costs else max(cast["costValue"] for cast in casts),
                    allow_next=tuple(
                        (
                            window["start_frame"] / fps,
                            window["end_frame"] / fps,
                            tuple(window["allowed_skill_ids"] or ()),
                        )
                        for window in data["allow_next_windows"]
                    ),
                )
            )
        return tuple(profiles)

    def record(self, skill_id: str) -> dict:
        """Full lossless records are loaded only for inspection, outside the hot loop."""
        if self._records is None:
            compressed = (self.path / "records.json.gz").read_bytes()
            if hashlib.sha256(compressed).hexdigest() != self.index["records_sha256"]:
                raise ValueError("Timing records hash mismatch")
            self._records = json.loads(gzip.decompress(compressed))
        return self._records[skill_id]


@lru_cache(maxsize=1)
def load_skill_timings() -> SkillTimingStore:
    return SkillTimingStore()
