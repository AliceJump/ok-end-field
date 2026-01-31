"""Weapon -> graduate essence requirements (loaded from CSV)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeaponRequirement:
    weapon: str
    star: str
    entries: tuple[str, ...]


def load_weapon_data(csv_path: str | Path) -> list[WeaponRequirement]:
    path = Path(csv_path)
    if not path.exists():
        return []

    requirements: list[WeaponRequirement] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue

            normalized = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in row.items() if k}
            weapon = normalized.get("武器", "")
            if not weapon:
                continue

            entries = tuple(
                e for e in (
                    normalized.get("毕业词条1", ""),
                    normalized.get("毕业词条2", ""),
                    normalized.get("毕业词条3", ""),
                )
                if e
            )

            requirements.append(
                WeaponRequirement(
                    weapon=weapon,
                    star=normalized.get("星级", ""),
                    entries=entries,
                )
            )
    return requirements


def match_weapon_requirements(
    requirements: list[WeaponRequirement],
    entry_names: tuple[str, ...] | list[str],
) -> list[WeaponRequirement]:
    entry_set = set(entry_names)
    matched: list[WeaponRequirement] = []
    for req in requirements:
        req_set = set(req.entries)
        if req_set and req_set == entry_set:
            matched.append(req)
    return matched
