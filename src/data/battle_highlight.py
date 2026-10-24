"""Raw ring visibility bound to the current native button contract.

An observation replaces the previous one, including UNKNOWN. It describes the
current visual sample. Interpretation, producer closure and execution policy
belong to separate consumers; this module never determines skill readiness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BattleHighlightObservation:
    skill_ids: tuple[str, ...]
    condition_json: str
    visible: bool | None
    observed_at: float


class BattleHighlightState:
    def __init__(self, store):
        self.store = store
        self.conditions = {}
        self.observations: dict[str, BattleHighlightObservation] = {}

    def condition(self, profiles):
        """Only bind a single, nonempty predicate for the current button."""
        conditions = []
        for profile in profiles:
            skill_id = profile.skill_id
            if skill_id not in self.conditions:
                try:
                    data = self.store.record(skill_id)['data']['skillHighlightCondition']
                except KeyError:
                    data = None
                self.conditions[skill_id] = (
                    json.dumps(data, sort_keys=True, separators=(',', ':'))
                    if data and data.get('actionData') else None
                )
            conditions.append(self.conditions[skill_id])
        if not conditions or None in conditions or len(set(conditions)) != 1:
            return None
        return conditions[0]

    def observe(self, slot, profiles, value, now):
        profiles = tuple(profiles)
        condition = self.condition(profiles)
        if condition is None:
            self.observations.pop(slot, None)
            return None
        observation = BattleHighlightObservation(
            tuple(profile.skill_id for profile in profiles), condition,
            value if type(value) is bool else None, now,
        )
        self.observations[slot] = observation
        return observation
