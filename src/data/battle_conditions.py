"""Scoped scene evidence projected along a candidate sequence.

An observed condition can survive a reviewed non-mutating action. Projection
does not assert a hit, exact remaining duration or a shared enemy identity.
Unknown transitions discard evidence rather than inventing negative facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.data.highlight_conditions import (
    HighlightAtom,
    HighlightExpression,
    HighlightInference,
    infer_highlight,
    parse_highlight,
)
from src.data.highlight_producers import TeamProducerClosure, read_producers

if TYPE_CHECKING:
    from src.data.battle_highlight import BattleHighlightObservation


@dataclass(frozen=True)
class BattleConditionAssessment:
    """Derived condition evidence, never an instruction to cast a skill."""

    skill_ids: tuple[str, ...]
    condition_json: str
    satisfied: bool | None
    observed_at: float
    inference: HighlightInference
    model_inference: HighlightInference


class BattleConditionEvaluator:
    """Interpret raw samples separately from visual sensing and action policy."""

    def __init__(self, team=()):
        self.closure = TeamProducerClosure.for_team(team) if team else None

    def assess(self, observation: BattleHighlightObservation | None):
        if observation is None:
            return None
        value = observation.visible if type(observation.visible) is bool else None
        expression = parse_highlight(observation.condition_json)
        return BattleConditionAssessment(
            observation.skill_ids, observation.condition_json, value, observation.observed_at,
            infer_highlight(expression, value),
            infer_highlight(expression, value, possible=self.closure.possible if self.closure is not None else None),
        )

    def forecast(self, observations):
        return ForecastConditions.from_assessments({slot: self.assess(obs) for slot, obs in observations.items()})


@dataclass(frozen=True)
class ConditionRequirement:
    slot: str
    skill_ids: tuple[str, ...]
    condition_json: str

    @classmethod
    def from_assessment(cls, slot, assessment):
        return (cls(slot, assessment.skill_ids, assessment.condition_json)
                if isinstance(assessment, BattleConditionAssessment) else None)


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
    observations: tuple[tuple[str, BattleConditionAssessment], ...] = ()
    projected_actions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_assessments(cls, assessments):
        return cls(tuple(sorted((slot, value) for slot, value in assessments.items()
                                if isinstance(value, BattleConditionAssessment) and value.satisfied is True)))

    def supports(self, requirement):
        return requirement is not None and any(slot == requirement.slot and obs.satisfied is True
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
