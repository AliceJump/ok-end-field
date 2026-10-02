"""Single final runtime binary for timed combat.

No character names, skill ids, descriptions, decoded actions, or extraction
intermediates are stored in the artifact. Character ids are stable positions in
the lexicographically sorted keys of assets/data/characters.json.

The file contains two final-runtime sections:
1. per-character numeric timing/state records;
2. one fixed-width decision record for every unordered 1..4 character set.
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from itertools import permutations
from pathlib import Path

from src.data.skill_timing import BattleStateSpec, SkillTiming

_ROOT = Path(__file__).resolve().parents[2]
_CHARACTERS_FILE = _ROOT / "assets" / "data" / "characters.json"
RUNTIME_TABLE = _ROOT / "assets" / "data" / "skill_timings" / "runtime.bin"

MAGIC = b"OKRUN2\0\0"
VERSION = 2
FPS = 30
MAX_TEAM = 4
COMBO_RECORD_SIZE = 3
_HEADER = struct.Struct("<8sBBBBIII")
_NO_AXIS = 0xFF
_NONE_U8 = 0xFF
_NONE_U16 = 0xFFFF
KINDS = ("battle", "link", "ult")
_KIND_BITS = {"battle": 1, "link": 2, "ult": 4}


def _character_payload(path: Path = _CHARACTERS_FILE):
    payload = json.loads(path.read_text(encoding="utf-8"))
    keys = tuple(sorted(payload))
    return payload, keys


def _mapping_crc(keys: tuple[str, ...]) -> int:
    return zlib.crc32("\0".join(keys).encode("utf-8")) & 0xFFFFFFFF


@lru_cache(maxsize=MAX_TEAM)
def _ordered_subsets(size: int) -> tuple[tuple[int, ...], ...]:
    return tuple(
        order
        for length in range(1, size + 1)
        for order in permutations(range(size), length)
    )


def encode_axis(local_order: tuple[int, ...], team_size: int) -> int:
    if not local_order:
        return _NO_AXIS
    try:
        return _ordered_subsets(team_size).index(tuple(local_order))
    except ValueError as exc:
        raise ValueError(f"Invalid axis {local_order} for team size {team_size}") from exc


def decode_axis(code: int, team_ids: tuple[int, ...]) -> tuple[int, ...]:
    if code == _NO_AXIS:
        return ()
    options = _ordered_subsets(len(team_ids))
    if code >= len(options):
        raise ValueError(f"Invalid runtime axis code {code}")
    return tuple(team_ids[index] for index in options[code])


def combination_rank(ids: tuple[int, ...]) -> int:
    return sum(math.comb(value, index + 1) for index, value in enumerate(ids))


def combination_offset(character_count: int, team_size: int) -> int:
    return sum(math.comb(character_count, size) for size in range(1, team_size))


def total_combinations(character_count: int) -> int:
    return sum(math.comb(character_count, size) for size in range(1, MAX_TEAM + 1))


class _Reader:
    def __init__(self, data: bytes, start=0, end=None):
        self.data = data
        self.pos = start
        self.end = len(data) if end is None else end

    def take(self, size):
        end = self.pos + size
        if end > self.end:
            raise ValueError("Runtime table is truncated")
        value = self.data[self.pos:end]
        self.pos = end
        return value

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), "little")


@dataclass(frozen=True)
class RuntimeAxisDecision:
    battle_ids: tuple[int, ...]
    ult_ids: tuple[int, ...]
    assume_success_sp_threshold: float


class RuntimeTimingTable:
    """Read-only runtime store plus precomputed unordered-team decisions."""

    def __init__(
        self,
        path: Path = RUNTIME_TABLE,
        characters_path: Path = _CHARACTERS_FILE,
    ):
        self.path = Path(path)
        self.characters_path = Path(characters_path)
        payload, self.keys = _character_payload(self.characters_path)
        self.aliases: dict[str, int] = {}
        for runtime_id, key in enumerate(self.keys):
            row = payload[key]
            for alias in (key, row.get("zh"), row.get("en")):
                if alias:
                    self.aliases[str(alias)] = runtime_id

        data = self.path.read_bytes()
        if len(data) < _HEADER.size:
            raise ValueError("Runtime timing table is truncated")
        (
            magic,
            version,
            count,
            fps,
            combo_record_size,
            mapping_crc,
            character_bytes,
            combo_count,
        ) = _HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION:
            raise ValueError("Unsupported runtime timing table")
        if count != len(self.keys) or fps != FPS or combo_record_size != COMBO_RECORD_SIZE:
            raise ValueError("Runtime timing table shape mismatch")
        if mapping_crc != _mapping_crc(self.keys):
            raise ValueError("Runtime timing character mapping mismatch")
        if combo_count != total_combinations(count):
            raise ValueError("Runtime timing combination count mismatch")

        self.data = data
        self.character_count = count
        self.combo_count = combo_count
        self.combo_start = _HEADER.size + character_bytes
        expected_size = self.combo_start + combo_count * COMBO_RECORD_SIZE
        if len(data) != expected_size:
            raise ValueError("Runtime timing table size mismatch")

        reader = _Reader(data, _HEADER.size, self.combo_start)
        self._gains = []
        self._profiles = []
        self._states = []
        for runtime_id in range(count):
            gain_raw = reader.u8()
            self._gains.append(None if gain_raw == _NONE_U8 else gain_raw / 2)

            synthetic_ids = {
                kind: f"r{runtime_id}:{kind}"
                for kind in KINDS
            }
            encoded_profiles = []
            raw_profiles = []
            for kind in KINDS:
                if not reader.u8():
                    raw_profiles.append(None)
                    continue
                raw_profiles.append(
                    {
                        "duration": reader.u16(),
                        "exclusive": reader.u16(),
                        "cooldown": reader.u16(),
                        "skill_points": reader.u8(),
                        "sp_cost": reader.u16(),
                        "effect_start": reader.u16(),
                        "windows": tuple(
                            (reader.u16(), reader.u16(), reader.u8())
                            for _ in range(reader.u8())
                        ),
                    }
                )

            for kind, raw in zip(KINDS, raw_profiles):
                if raw is None:
                    encoded_profiles.append(())
                    continue
                allow_next = []
                for start, end, mask in raw["windows"]:
                    allowed = tuple(
                        synthetic_ids[target]
                        for target in KINDS
                        if mask & _KIND_BITS[target]
                    )
                    allow_next.append((start / FPS, end / FPS, allowed))
                sp_cost = None if raw["sp_cost"] == _NONE_U16 else raw["sp_cost"] / 2
                effect_start = (
                    None
                    if raw["effect_start"] == _NONE_U16
                    else raw["effect_start"] / FPS
                )
                skill_points = (
                    None
                    if raw["skill_points"] == _NONE_U8
                    else raw["skill_points"]
                )
                encoded_profiles.append(
                    (
                        SkillTiming(
                            skill_id=synthetic_ids[kind],
                            duration=raw["duration"] / FPS,
                            exclusive=raw["exclusive"] / FPS,
                            cooldown=raw["cooldown"] / 100,
                            skill_points=skill_points,
                            sp_cost=sp_cost,
                            effect_start=effect_start,
                            allow_next=tuple(allow_next),
                        ),
                    )
                )
            self._profiles.append(tuple(encoded_profiles))

            states = []
            for source in ("battle", "ult"):
                if not reader.u8():
                    states.append(None)
                    continue
                duration_frame = reader.u16()
                end_cd_raw = reader.u16()
                states.append(
                    BattleStateSpec(
                        base_skill_id=synthetic_ids[source],
                        end_skill_id=f"r{runtime_id}:end",
                        duration=duration_frame / FPS,
                        end_cooldown=None if end_cd_raw == _NONE_U16 else end_cd_raw / 100,
                        evidence=("precomputed",),
                    )
                )
            self._states.append(tuple(states))

        if reader.pos != self.combo_start:
            raise ValueError("Runtime timing character section length mismatch")
        known_gains = [value for value in self._gains if value is not None]
        self._global_gain = max(known_gains) if known_gains else None

    def id_for_name(self, name: str) -> int | None:
        return self.aliases.get(name)

    def _ids_for(self, character) -> tuple[int, ...]:
        if isinstance(character, int):
            return (character,) if 0 <= character < self.character_count else ()
        runtime_id = self.id_for_name(str(character))
        return () if runtime_id is None else (runtime_id,)

    def profiles(self, character, kind: str) -> tuple[SkillTiming, ...]:
        if kind not in KINDS:
            return ()
        kind_index = KINDS.index(kind)
        result = []
        for runtime_id in self._ids_for(character):
            result.extend(self._profiles[runtime_id][kind_index])
        return tuple(result)

    def normal_attack_sp_gain(self, character) -> float | None:
        values = [
            self._gains[runtime_id]
            for runtime_id in self._ids_for(character)
            if self._gains[runtime_id] is not None
        ]
        return max(values) if values else None

    def global_normal_attack_sp_gain(self) -> float | None:
        return self._global_gain

    def team_normal_attack_sp_gains(self, team) -> dict[str, float | None]:
        return {
            name: self.normal_attack_sp_gain(name)
            for name in team
            if name not in (None, "", "?")
        }

    def state_skill(self, character, kind: str = "battle") -> BattleStateSpec | None:
        state_index = 0 if kind == "battle" else 1 if kind == "ult" else None
        if state_index is None:
            return None
        specs = [
            self._states[runtime_id][state_index]
            for runtime_id in self._ids_for(character)
            if self._states[runtime_id][state_index] is not None
        ]
        return specs[0] if len(specs) == 1 else None

    def battle_state(self, character) -> BattleStateSpec | None:
        return self.state_skill(character, "battle")

    def ultimate_state(self, character) -> BattleStateSpec | None:
        return self.state_skill(character, "ult")

    def _combo_record_offset(self, ids: tuple[int, ...]) -> int:
        return self.combo_start + (
            combination_offset(self.character_count, len(ids)) + combination_rank(ids)
        ) * COMBO_RECORD_SIZE

    def lookup_ids(self, members) -> RuntimeAxisDecision:
        ids = tuple(sorted(int(value) for value in members))
        if not 1 <= len(ids) <= MAX_TEAM:
            raise ValueError("Runtime lookup requires 1..4 characters")
        if len(set(ids)) != len(ids):
            raise ValueError("Runtime lookup does not allow duplicate characters")
        if ids[0] < 0 or ids[-1] >= self.character_count:
            raise ValueError("Runtime character id out of range")
        offset = self._combo_record_offset(ids)
        battle_code, ult_code, threshold_half = self.data[offset:offset + COMBO_RECORD_SIZE]
        return RuntimeAxisDecision(
            battle_ids=decode_axis(battle_code, ids),
            ult_ids=decode_axis(ult_code, ids),
            assume_success_sp_threshold=threshold_half / 2,
        )

    def lookup_names(self, members) -> RuntimeAxisDecision:
        ids = []
        for name in members:
            if name in (None, "", "?"):
                continue
            runtime_id = self.id_for_name(str(name))
            if runtime_id is None:
                raise KeyError(f"Unknown runtime character: {name}")
            ids.append(runtime_id)
        return self.lookup_ids(ids)


@lru_cache(maxsize=1)
def load_runtime_timing_table(path: Path = RUNTIME_TABLE) -> RuntimeTimingTable:
    return RuntimeTimingTable(path=path)
