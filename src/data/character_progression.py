"""Build-specific combat talents and potentials, with explicit baseline selection.

Native modifiers retain their conditions and numeric operation codes. They are
data for the future action interpreter, not unconditional effects on cast.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.skill_types import SkillResourceChange

SNAPSHOT = Path(__file__).resolve().parents[2] / "assets/data/character_progression/20261003"


@dataclass(frozen=True)
class CombatBaseline:
    potential: int
    talent_policy: str
    potential_basis: str
    character_level: int | None = None
    skill_rank: int | None = None
    weapon_id: str | None = None
    weapon_name: str | None = None


@dataclass(frozen=True)
class PassiveResourceModifier:
    """Modify an existing cost/recovery rule; never grant a second payout."""

    resource: str
    target: str
    scope: str  # cost / recovery
    operation: str  # multiply
    value: float | None
    skill_ids: tuple[str, ...]
    trigger: str
    native_bb_keys: tuple[str, ...] = ()
    formula: str | None = None


@dataclass(frozen=True)
class NativePassive:
    effect_id: str
    name: str | None
    level: int
    description: str | None
    description_template: str | None
    parameters: dict[str, float | str]
    modifiers: tuple[dict, ...]
    source: str
    slot: int | None = None
    resource_changes: tuple[SkillResourceChange, ...] = ()
    resource_modifiers: tuple[PassiveResourceModifier, ...] = ()


@dataclass(frozen=True)
class CharacterProgression:
    native_id: str
    baseline: CombatBaseline
    talent_ranks: tuple[NativePassive, ...]
    potentials: tuple[NativePassive, ...]
    attribute_nodes: tuple[dict, ...]

    @property
    def talents(self) -> tuple[NativePassive, ...]:
        """Highest unlocked rank per combat talent; lower ranks do not stack."""
        selected: dict[int, NativePassive] = {}
        for talent in self.talent_ranks:
            slot = talent.slot
            if slot is None:
                raise ValueError(f"Missing combat talent slot: {talent.effect_id}")
            if slot not in selected or talent.level > selected[slot].level:
                selected[slot] = talent
        return tuple(selected[slot] for slot in sorted(selected))

    @property
    def active_potentials(self) -> tuple[NativePassive, ...]:
        return tuple(p for p in self.potentials if p.level <= self.baseline.potential)


@lru_cache(maxsize=1)
def _records() -> dict:
    raw = (SNAPSHOT / "characters.json").read_bytes()
    manifest = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != manifest["characters_sha256"]:
        raise ValueError("Character progression snapshot hash mismatch")
    return json.loads(raw)


def load_character_progression(character_id: str) -> CharacterProgression | None:
    data = _records().get(character_id)
    if data is None:
        return None

    def passive(row: dict) -> NativePassive:
        from src.data.skill_types import SkillResourceChange

        return NativePassive(
            effect_id=row["effect_id"],
            name=row["name"],
            level=row["level"],
            description=row["description"],
            description_template=row["description_template"],
            parameters=dict(row["parameters"]),
            modifiers=tuple(deepcopy(row["modifiers"])),
            source=row["source"],
            slot=row.get("slot"),
            resource_changes=tuple(SkillResourceChange.from_dict(c) for c in row.get("resource_changes", [])),
            resource_modifiers=tuple(
                PassiveResourceModifier(
                    **{**m, "skill_ids": tuple(m["skill_ids"]), "native_bb_keys": tuple(m.get("native_bb_keys", []))}
                )
                for m in row.get("resource_modifiers", [])
            ),
        )

    return CharacterProgression(
        native_id=data["native_id"],
        baseline=CombatBaseline(**data["baseline"]),
        talent_ranks=tuple(passive(row) for row in data["talent_ranks"]),
        potentials=tuple(passive(row) for row in data["potentials"]),
        attribute_nodes=tuple(deepcopy(data["attribute_nodes"])),
    )
