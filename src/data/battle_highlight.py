"""Current button-condition observations from the packaged native records.

An observation replaces the previous one, including UNKNOWN. It describes the
whole authored predicate, preserving OR branches and stack thresholds rather
than inventing individual enemy statuses or exact resource counts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.data.highlight_conditions import HighlightInference, infer_highlight, parse_highlight
from src.data.highlight_producers import TeamProducerClosure


@dataclass(frozen=True)
class BattleHighlightObservation:
    skill_ids: tuple[str, ...]
    condition_json: str
    ready: bool | None
    observed_at: float
    inference: HighlightInference = HighlightInference()
    model_inference: HighlightInference = HighlightInference()

    @property
    def confirmed_condition(self):
        return json.loads(self.condition_json) if self.ready is True else None


class BattleHighlightState:
    def __init__(self, store, team=()):
        self.store = store
        self.conditions = {}
        self.observations: dict[str, BattleHighlightObservation] = {}
        self.closure = TeamProducerClosure.for_team(team) if team else None

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
            infer_highlight(parse_highlight(condition), value),
            infer_highlight(parse_highlight(condition), value,
                            possible=self.closure.possible if self.closure is not None else None),
        )
        self.observations[slot] = observation
        return observation
