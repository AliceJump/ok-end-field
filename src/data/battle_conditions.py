"""Scoped scene evidence projected along a candidate sequence.

An observed condition can survive a reviewed non-mutating action. Projection
does not assert a hit, exact remaining duration or a shared enemy identity.
Unknown transitions discard evidence rather than inventing negative facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.data.highlight_conditions import HighlightAtom, HighlightExpression, parse_highlight
from src.data.highlight_producers import read_producers

if TYPE_CHECKING:
    from src.data.battle_highlight import BattleHighlightObservation


@dataclass(frozen=True)
class ConditionRequirement:
    slot: str
    skill_ids: tuple[str, ...]
    condition_json: str

    @classmethod
    def from_observation(cls, slot, observation):
        return cls(slot, observation.skill_ids, observation.condition_json) if observation is not None else None


@dataclass(frozen=True)
class ScopedConditionFact:
    slot: str
    skill_ids: tuple[str, ...]
    observed_at: float
    atom: HighlightAtom
    truth: bool


@dataclass(frozen=True)
class ConditionTransition:
    preserves_recommended_tags: frozenset[str]

    @classmethod
    def for_actor(cls, row, store):
        if store is None:
            return {}
        result = {}
        for spec in read_producers()['condition_transitions'].get(row['key'], ()):
            for record_id, expected in spec['record_hashes'].items():
                try:
                    actual = store.record(record_id)['source']['sha256']
                except KeyError as error:
                    raise ValueError(f'Missing live condition transition record: {record_id}') from error
                if actual != expected:
                    raise ValueError(f'Unreviewed live condition transition record: {record_id}')
            result[spec['kind']] = cls(frozenset(spec['preserves_recommended_tags']))
        return result

    def preserves(self, expression: HighlightExpression):
        if expression.operation == 'atom':
            atom = expression.atom
            return (atom.kind == 'tag' and atom.recommended_target
                    and atom.identity in self.preserves_recommended_tags)
        children = expression.children
        if expression.operation == 'and':
            # This readiness gate is not an enemy condition. It remains in
            # the original button contract and is checked again by candidate
            # budget/action locks. Do not remove a gate that is an OR arm.
            children = tuple(c for c in children if not (c.operation == 'atom' and c.atom.kind == 'availability'))
        return (expression.operation in {'and', 'or', 'not'} and bool(children)
                and all(self.preserves(child) for child in children))


@dataclass(frozen=True)
class ForecastConditions:
    observations: tuple[tuple[str, BattleHighlightObservation], ...] = ()
    projected_actions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_observations(cls, observations):
        return cls(tuple(sorted((slot, obs) for slot, obs in observations.items() if obs.ready is True)))

    def supports(self, requirement):
        return any(slot == requirement.slot and obs.ready is True
                   and obs.skill_ids == requirement.skill_ids and obs.condition_json == requirement.condition_json
                   for slot, obs in self.observations)

    def facts(self):
        # Only actual observation entailment enters scene facts. Team-model OR
        # narrowing is not promoted to an observed enemy status.
        return frozenset(ScopedConditionFact(slot, obs.skill_ids, obs.observed_at, atom, truth)
                         for slot, obs in self.observations for atom, truth in obs.inference.facts
                         if atom.kind in {'tag', 'buff'})

    def after(self, slot, kind, transition):
        kept = () if transition is None else tuple(
            (actor, obs) for actor, obs in self.observations
            if transition.preserves(parse_highlight(obs.condition_json)))
        return ForecastConditions(kept, (*self.projected_actions, (slot, kind)))
