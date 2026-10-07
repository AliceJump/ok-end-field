"""Confirmed final four-stat deltas and the unrounded attack attribute basis.

These do not implement native converted/non-converted formula domains or
arbitrary percentage buffs. Producers must establish those semantics first.
"""

import math
from dataclasses import dataclass

ATTRIBUTES = ("力量", "敏捷", "智识", "意志")


@dataclass(frozen=True)
class DamageAttributeBasis:
    primary: str
    secondary: str | None
    totals: tuple[tuple[str, float], ...]
    unverified_attack_dependencies: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data):
        if data["schema_version"] != 1 or data["domain"] != "final_panel":
            raise ValueError("Unsupported damage attribute domain")
        primary, secondary = data["primary"], data["secondary"]
        totals = data["totals"]
        dependencies = tuple(data["unverified_attack_dependencies"])
        if (primary not in ATTRIBUTES or secondary not in (*ATTRIBUTES, None) or primary == secondary
                or set(totals) != set(ATTRIBUTES) or not set(dependencies) <= set(ATTRIBUTES)
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in totals.values())):
            raise ValueError("Invalid damage attribute basis")
        return cls(primary, secondary, tuple((key, float(totals[key])) for key in ATTRIBUTES), dependencies)

    def factor(self):
        values = dict(self.totals)
        return 1 + .005 * values[self.primary] + (.002 * values[self.secondary] if self.secondary else 0)

    def factor_delta(self, deltas):
        if any(deltas.get(attribute, 0) != 0 for attribute in self.unverified_attack_dependencies):
            raise ValueError("Dynamic attribute-dependent attack conversion is unverified")
        return .005 * deltas.get(self.primary, 0) + (.002 * deltas.get(self.secondary, 0) if self.secondary else 0)


@dataclass(frozen=True)
class FinalAttributeDelta:
    actor: str
    key: str
    source: str
    attribute: str
    amount: float | None
    expires_at: float | None
