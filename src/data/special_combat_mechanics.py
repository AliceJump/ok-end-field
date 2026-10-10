from __future__ import annotations

from typing import Final

SpecialMechanicDetectorPair = tuple[str, str]

# Ordered by priority. Each mechanic is configured only by its start/end detector
# pair; the runtime action for that mechanic lives in the combat mechanics patch.
# Future mechanics can be added here without changing either combat scheduler.
SPECIAL_COMBAT_MECHANIC_DETECTOR_PAIRS: Final[
    tuple[tuple[str, SpecialMechanicDetectorPair], ...]
] = (
    ("shield_guard", ("shield_guard_start", "shield_guard_end")),
)
