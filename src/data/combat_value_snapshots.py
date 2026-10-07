"""Detach authored events from mutable canonical skill-data records."""

from collections.abc import Mapping
from dataclasses import dataclass, fields

from src.data.damage_modifiers import DamageModifierSpec
from src.data.effects import EffectType
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.skill_types import CombatResourceType, ResourceChangeKind


@dataclass(frozen=True)
class EffectValue(ImmutableCombatValue):
    effect_id: EffectType
    value: int | float | str | None = None
    duration: int | float | str | None = None
    target: str | None = None
    count: int | None = None
    subject_effect_id: EffectType | None = None
    damage_modifier: DamageModifierSpec | None = None
    consumes_all: bool = False


@dataclass(frozen=True)
class CountValues(ImmutableCombatValue, Mapping):
    entries: tuple[tuple[int, int | float], ...] = ()

    def __getitem__(self, key):
        for candidate, value in self.entries:
            if key == candidate:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _ in self.entries)

    def __len__(self):
        return len(self.entries)


@dataclass(frozen=True)
class ResourceValue(ImmutableCombatValue):
    resource: CombatResourceType
    target: str
    kind: ResourceChangeKind
    amount: int | float | None = None
    per_unit: int | float | None = None
    source_effect_id: EffectType | None = None
    max_units: int | None = None
    values_by_count: CountValues = CountValues()
    formula: str | None = None
    trigger: str | None = None
    count_basis: str | None = None
    affected_by_energy_gain: bool | None = None
    max_amount: int | float | None = None


def effect_value(effect):
    if isinstance(effect, EffectValue):
        return effect
    return EffectValue(**{f.name: getattr(effect, f.name) for f in fields(EffectValue)})


def resource_value(change):
    if isinstance(change, ResourceValue):
        return change
    values = {f.name: getattr(change, f.name) for f in fields(ResourceValue)}
    values["values_by_count"] = CountValues(tuple(sorted(change.values_by_count.items())))
    return ResourceValue(**values)
