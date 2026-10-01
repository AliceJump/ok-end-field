"""Compact precomputed runtime timing bundle.

The external data repository can build one binary file from the full lossless
snapshot. Runtime consumers only need this file; they do not need to parse the
original SkillData/BuffData records.
"""

from __future__ import annotations

import json
import struct
import zlib
from functools import lru_cache
from pathlib import Path

from src.data.skill_timing import SNAPSHOT, BattleStateSpec, SkillTiming

MAGIC = b"OKETB1\0\0"
SCHEMA_VERSION = 1
DEFAULT_RUNTIME_BUNDLE = SNAPSHOT / "runtime_timing.bin"
_HEADER = struct.Struct("<8sBII")


def _profile_to_dict(profile: SkillTiming) -> dict:
    return {
        "i": profile.skill_id,
        "d": profile.duration,
        "x": profile.exclusive,
        "c": profile.cooldown,
        "p": profile.skill_points,
        "s": profile.sp_cost,
        "e": profile.effect_start,
        "a": [
            [start, end, list(allowed)]
            for start, end, allowed in profile.allow_next
        ],
    }


def _profile_from_dict(data: dict) -> SkillTiming:
    return SkillTiming(
        skill_id=data["i"],
        duration=data["d"],
        exclusive=data["x"],
        cooldown=data["c"],
        skill_points=data["p"],
        sp_cost=data.get("s"),
        effect_start=data.get("e"),
        allow_next=tuple(
            (start, end, tuple(allowed))
            for start, end, allowed in data.get("a", ())
        ),
    )


def _state_to_dict(spec: BattleStateSpec | None) -> dict | None:
    if spec is None:
        return None
    return {
        "b": spec.base_skill_id,
        "e": spec.end_skill_id,
        "d": spec.duration,
        "c": spec.end_cooldown,
        "v": list(spec.evidence),
    }


def _state_from_dict(data: dict | None) -> BattleStateSpec | None:
    if data is None:
        return None
    return BattleStateSpec(
        base_skill_id=data["b"],
        end_skill_id=data["e"],
        duration=data["d"],
        end_cooldown=data.get("c"),
        evidence=tuple(data.get("v", ())),
    )


def build_runtime_payload(store) -> dict:
    """Materialize only values needed by the timed-combat runtime."""

    characters = {}
    for cid, row in store.index["characters"].items():
        aliases = [cid, row["key"], row["name"], row["english_name"]]
        characters[cid] = {
            "n": row["name"],
            "k": row["key"],
            "en": row["english_name"],
            "al": aliases,
            "g": store.normal_attack_sp_gain(cid),
            "p": {
                kind: [_profile_to_dict(profile) for profile in store.profiles(cid, kind)]
                for kind in ("battle", "link", "ult")
            },
            "st": {
                "battle": _state_to_dict(store.battle_state(cid)),
                "ult": _state_to_dict(store.ultimate_state(cid)),
            },
        }

    return {
        "v": SCHEMA_VERSION,
        "fps": store.index["frames_per_second"],
        "src": store.index.get("records_sha256"),
        "g": store.global_normal_attack_sp_gain(),
        "c": characters,
    }


def dumps_runtime_payload(payload: dict, level: int = 9) -> bytes:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    compressed = zlib.compress(raw, level)
    return _HEADER.pack(
        MAGIC,
        SCHEMA_VERSION,
        len(raw),
        zlib.crc32(raw) & 0xFFFFFFFF,
    ) + compressed


def loads_runtime_payload(data: bytes) -> dict:
    if len(data) < _HEADER.size:
        raise ValueError("Timing runtime bundle is truncated")
    magic, version, raw_size, expected_crc = _HEADER.unpack_from(data)
    if magic != MAGIC or version != SCHEMA_VERSION:
        raise ValueError("Unsupported timing runtime bundle")
    raw = zlib.decompress(data[_HEADER.size :])
    if len(raw) != raw_size:
        raise ValueError("Timing runtime bundle size mismatch")
    if (zlib.crc32(raw) & 0xFFFFFFFF) != expected_crc:
        raise ValueError("Timing runtime bundle CRC mismatch")
    payload = json.loads(raw)
    if payload.get("v") != SCHEMA_VERSION:
        raise ValueError("Timing runtime payload schema mismatch")
    return payload


def export_runtime_bundle(store, path: Path) -> tuple[int, int]:
    payload = build_runtime_payload(store)
    binary = dumps_runtime_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(binary)
    raw_size = len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return raw_size, len(binary)


class RuntimeTimingBundle:
    """Read-only drop-in timing data for team/runtime queries."""

    def __init__(self, path: Path | None = None, data: bytes | None = None):
        if (path is None) == (data is None):
            raise ValueError("Provide exactly one of path or data")
        self.path = path
        self.payload = loads_runtime_payload(path.read_bytes() if path is not None else data)
        self._characters = self.payload["c"]
        self._aliases = {}
        for cid, row in self._characters.items():
            for alias in row["al"]:
                self._aliases.setdefault(alias, []).append(cid)

    def _character_ids(self, character: str) -> tuple[str, ...]:
        return tuple(self._aliases.get(character, ()))

    def profiles(self, character: str, kind: str) -> tuple[SkillTiming, ...]:
        profiles = []
        for cid in self._character_ids(character):
            profiles.extend(_profile_from_dict(row) for row in self._characters[cid]["p"].get(kind, ()))
        return tuple(profiles)

    def normal_attack_sp_gain(self, character: str) -> float | None:
        values = [
            self._characters[cid]["g"]
            for cid in self._character_ids(character)
            if self._characters[cid]["g"] is not None
        ]
        return max(values) if values else None

    def global_normal_attack_sp_gain(self) -> float | None:
        return self.payload.get("g")

    def team_normal_attack_sp_gains(self, team) -> dict[str, float | None]:
        return {
            name: self.normal_attack_sp_gain(name)
            for name in team
            if name not in (None, "", "?")
        }

    def state_skill(self, character: str, kind: str = "battle") -> BattleStateSpec | None:
        specs = []
        for cid in self._character_ids(character):
            spec = _state_from_dict(self._characters[cid]["st"].get(kind))
            if spec is not None:
                specs.append(spec)
        return specs[0] if len(specs) == 1 else None

    def battle_state(self, character: str) -> BattleStateSpec | None:
        return self.state_skill(character, "battle")

    def ultimate_state(self, character: str) -> BattleStateSpec | None:
        return self.state_skill(character, "ult")

    def team(self, team) -> dict:
        """Return slot-preserving runtime data for any partial team.

        Examples:
            ["A", "B"] -> two populated slots
            ["A", "?", "C", None] -> slots 2/4 remain empty
        """

        slots = []
        gains = []
        for slot, name in enumerate(team, 1):
            if name in (None, "", "?"):
                slots.append({"slot": slot, "name": name, "character_id": None})
                continue

            ids = self._character_ids(name)
            if not ids:
                slots.append({"slot": slot, "name": name, "character_id": None, "character_ids": ()})
                continue

            gain = self.normal_attack_sp_gain(name)
            if gain is not None:
                gains.append(gain)
            slots.append(
                {
                    "slot": slot,
                    "name": name,
                    "character_id": ids[0] if len(ids) == 1 else None,
                    "character_ids": ids,
                    "normal_attack_sp_gain": gain,
                    "battle": self.profiles(name, "battle"),
                    "link": self.profiles(name, "link"),
                    "ult": self.profiles(name, "ult"),
                    "battle_state": self.battle_state(name),
                    "ult_state": self.ultimate_state(name),
                }
            )

        max_gain = max(gains) if gains else None
        return {
            "slots": slots,
            "max_normal_attack_sp_gain": max_gain,
            "assume_success_sp_threshold": None if max_gain is None else max_gain + 5,
        }


@lru_cache(maxsize=1)
def load_runtime_timing_bundle(path: Path = DEFAULT_RUNTIME_BUNDLE) -> RuntimeTimingBundle:
    return RuntimeTimingBundle(path=path)
