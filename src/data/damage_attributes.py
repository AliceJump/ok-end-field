"""Confirmed final four-stat deltas and native floored attack conversion.

These do not implement native converted/non-converted formula domains or
arbitrary percentage buffs. Producers must establish those semantics first.
"""

import math
from dataclasses import dataclass

ATTRIBUTES = ("力量", "敏捷", "智识", "意志")
# Reviewed BattleConst float32 values, promoted to double by the native
# main/sub coefficient helpers. See damage-attribute-release-audit.md.
MAIN_ATTACK_RATE = 0.004999999888241291
SECONDARY_ATTACK_RATE = 0.0020000000949949026


@dataclass(frozen=True)
class DamageAttributeBasis:
    primary: str
    secondary: str | None
    totals: tuple[tuple[str, float], ...]
    unverified_attack_dependencies: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data):
        if (data["schema_version"] != 2 or data["domain"] != "final_panel"
                or data["attack_conversion"] != "floor_final_main_sub"):
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
        return (1 + MAIN_ATTACK_RATE * math.floor(values[self.primary])
                + (SECONDARY_ATTACK_RATE * math.floor(values[self.secondary]) if self.secondary else 0))

    def factor_delta(self, deltas):
        if any(deltas.get(attribute, 0) != 0 for attribute in self.unverified_attack_dependencies):
            raise ValueError("Dynamic attribute-dependent attack conversion is unverified")
        values = dict(self.totals)
        return sum(rate * (math.floor(values[attribute] + deltas.get(attribute, 0))
                           - math.floor(values[attribute]))
                   for attribute, rate in ((self.primary, MAIN_ATTACK_RATE),
                                           (self.secondary, SECONDARY_ATTACK_RATE)) if attribute)


@dataclass(frozen=True)
class FinalAttributeDelta:
    actor: str
    key: str
    source: str
    attribute: str
    amount: float | None
    expires_at: float | None
