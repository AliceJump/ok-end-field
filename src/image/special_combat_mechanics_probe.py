from __future__ import annotations

from collections.abc import Callable

SpecialMechanicProbe = Callable[[object, object], bool]


def probe_shield_guard_start(_task, _frame) -> bool:
    """Placeholder for the future shield-guard enter detector."""
    return False


def probe_shield_guard_end(_task, _frame) -> bool:
    """Placeholder for the future shield-guard exit detector."""
    return False


SPECIAL_COMBAT_MECHANIC_PROBES: dict[str, SpecialMechanicProbe] = {
    "shield_guard_start": probe_shield_guard_start,
    "shield_guard_end": probe_shield_guard_end,
}
