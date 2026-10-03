"""Observation contracts for combat-state hooks.

The concrete vision/OCR detectors intentionally live in BattleMixin.  Timing
logic consumes these enums so detector implementation can change later without
rewriting the scheduler.
"""

from __future__ import annotations

from enum import Enum


class EnemyPresence(str, Enum):
    """Whether a targetable enemy is visibly present in the combat scene."""

    UNKNOWN = "unknown"
    PRESENT = "present"
    ABSENT = "absent"


class ActionBlockReason(str, Enum):
    """On-screen reason explaining why a just-requested skill did not fire."""

    TOO_FAR = "too_far"
    DURING_SKILL = "during_skill"


def normalize_enemy_presence(value) -> EnemyPresence:
    if isinstance(value, EnemyPresence):
        return value
    if value is True:
        return EnemyPresence.PRESENT
    if value is False:
        return EnemyPresence.ABSENT
    try:
        return EnemyPresence(str(value))
    except (TypeError, ValueError):
        return EnemyPresence.UNKNOWN


def normalize_action_block_reason(value) -> ActionBlockReason | None:
    if value is None or isinstance(value, ActionBlockReason):
        return value
    try:
        return ActionBlockReason(str(value))
    except (TypeError, ValueError):
        return None
