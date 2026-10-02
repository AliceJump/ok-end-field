"""Compact runtime timing table experiment.

This file intentionally contains no unpack/research logic. It is the runtime
consumer for a precomputed artifact produced outside this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAGIC = b"OKRTBIN1"
RUNTIME_TABLE = Path(__file__).resolve().parents[2] / "assets/data/skill_timings/runtime_v1.bin"
KINDS = ("battle", "link", "ult")


@dataclass(frozen=True)
class RuntimeProfile:
    duration_frame: int
    exclusive_frame: int
    cooldown: float
    sp_cost: float
    allow_next: tuple[tuple[int, int, tuple[str, ...]], ...]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size):
        value = self.data[self.pos:self.pos + size]
        if len(value) != size:
            raise ValueError("Truncated runtime timing table")
        self.pos += size
        return value

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), "little")

    def varint(self):
        value = shift = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 28:
                raise ValueError("Invalid runtime timing varint")

    def text(self):
        return self.take(self.varint()).decode("utf-8")


class RuntimeTimingTable:
    def __init__(self, path=RUNTIME_TABLE):
        reader = _Reader(Path(path).read_bytes())
        if reader.take(len(MAGIC)) != MAGIC:
            raise ValueError("Unsupported runtime timing table")
        self.version = reader.u8()
        self.fps = reader.u8()
        if self.version != 1 or self.fps != 30:
            raise ValueError("Unsupported runtime timing table version")
        self.characters = {}
        self.aliases = {}
        for _ in range(reader.varint()):
            cid = reader.text()
            names = (cid, reader.text(), reader.text(), reader.text())
            skills = {}
            for kind in KINDS:
                if not reader.u8():
                    continue
                duration = reader.u16()
                exclusive = reader.u16()
                cooldown = reader.u16() / 100
                sp_cost = reader.u16() / 10
                windows = []
                for _ in range(reader.varint()):
                    start, end = reader.u16(), reader.u16()
                    allowed = tuple(reader.text() for _ in range(reader.varint()))
                    windows.append((start, end, allowed))
                skills[kind] = RuntimeProfile(
                    duration, exclusive, cooldown, sp_cost, tuple(windows)
                )
            row = {"id": cid, "aliases": names, "skills": skills}
            self.characters[cid] = row
            for name in names:
                self.aliases.setdefault(name, []).append(cid)
        if reader.pos != len(reader.data):
            raise ValueError("Trailing bytes in runtime timing table")

    def resolve(self, character):
        return tuple(self.characters[cid] for cid in self.aliases.get(character, ()))

    def team(self, members):
        """Return per-slot data without requiring a complete/alive four-person team."""
        return tuple(None if member == "?" else self.resolve(member) for member in members)
