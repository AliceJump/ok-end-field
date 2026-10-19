"""Conditional entity quotations with explicit, immutable source histories.

Requests never become observed arrivals. A nominal quotation may assume a
previous request completed, but exposes that assumption and remains gated by
the current button evidence (or a producer in that forecast branch).
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, replace

from src.data.battle_conditions import BattleConditionAssessment


def validate_contract(spec):
    if spec is None:
        return
    if (spec['type'] not in {'sword_salvo', 'spear_recall'}
            or spec['scope'] != 'conditional_nominal_projection; not_observed_inventory_or_hit'
            or not spec['native_hashes'] or not spec['skills']
            or set(spec['condition_hashes']) != set(spec['skills'])):
        raise ValueError('Unreviewed mechanism quote contract')
    if spec['type'] == 'sword_salvo':
        if (any(type(spec[k]) is not int for k in ('count_cap', 'free_requests', 'conduct_requests_cap'))
                or set(spec['ordinary']) != {'atk_scale', 'atk_up_per_conduct', 'final_rate'}
                or set(spec['enhanced']) != set(spec['ordinary'])):
            raise ValueError('Invalid sword contract counts or parameters')
        values = [spec[k] for k in ('count_cap', 'duration', 'free_requests', 'conduct_requests_cap')]
        values += [v for p in ('ordinary', 'enhanced') for v in spec[p].values()]
    else:
        if (set(spec['productions']) != {'link', 'ult'}
                or any(type(p['count']) is not int for p in spec['productions'].values())):
            raise ValueError('Invalid spear production contract')
        values = [*spec['ordinary'].values(), spec['minimum_nominal_recall']]
        values += [p[k] for p in spec['productions'].values() for k in ('count', 'duration')]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Invalid mechanism quote parameters')


def matches(spec, assessment):
    return (isinstance(assessment, BattleConditionAssessment) and len(assessment.skill_ids) == 1
            and spec['condition_hashes'].get(assessment.skill_ids[0])
            == hashlib.sha256(assessment.condition_json.encode()).hexdigest())


@dataclass(frozen=True)
class EntityRequest:
    owner: str
    source: str
    kind: str
    nominal_count: int
    launch_at: float
    quote_until: float
    phase: str = 'requested'
    forecast_only: bool = False


@dataclass(frozen=True)
class MechanismState:
    requests: tuple[EntityRequest, ...] = ()
    minimum_spent: frozenset[str] = frozenset()
    known_free_until: tuple[tuple[str, float], ...] = ()

    def keep(self, owners):
        return replace(self, requests=tuple(r for r in self.requests if r.owner in owners),
                       minimum_spent=self.minimum_spent & set(owners),
                       known_free_until=tuple((owner, end) for owner, end in self.known_free_until if owner in owners))

    def observe(self, owner, assessment, spec):
        if matches(spec, assessment) and assessment.satisfied is False:
            return replace(self, minimum_spent=self.minimum_spent-{owner})
        return self  # Darkness does not destroy entity histories.

    def available(self, owner, now):
        return tuple(r for r in self.requests if r.owner == owner and r.phase == 'requested'
                     and r.launch_at <= now < r.quote_until)

    def request(self, owner, source, kind, count, launch_at, duration, *, cap=None, forecast=False):
        kept = tuple(r for r in self.requests if launch_at < r.quote_until)
        if any(r.source == source for r in kept):
            return self
        if cap is not None:
            used = sum(r.nominal_count for r in kept if r.owner == owner and r.kind == kind and r.phase == 'requested')
            count = min(count, max(0, cap-used))
        additions = (EntityRequest(owner, source, kind, count, launch_at, launch_at+duration,
                                   forecast_only=forecast),) if count > 0 else ()
        return replace(self, requests=kept+additions, minimum_spent=self.minimum_spent-{owner})


@dataclass(frozen=True)
class MechanismQuote:
    value: float
    branch: str
    conduct: int | None = None
    selected_swords: int | None = None
    normal_spears: int | None = None
    strong_spears: int | None = None
    sources: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    minimum_recall: bool = False


def _unit(row):
    p = row['panel']
    attack = p['attack_white']*(1+p['attack_percent'])+p['attack_flat']
    return (attack*p['attribute_factor']*(1+row['quotes']['battle']['bonus_pct']/100)
            *(1+p['amplification']['电磁'])*(1+p['crit_rate']*p['crit_damage']))


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def branch_quote(row, owner, now, assessment, state, *, stance=None, forecast=False):
    spec = row.get('mechanism')
    if spec is None:
        return None
    positive = matches(spec, assessment) and assessment.satisfied is True
    requests = state.available(owner, now)
    projected = forecast and any(r.forecast_only for r in requests)
    if spec['type'] == 'spear_recall':
        return _spear_quote(row, owner, spec, positive, projected, requests, state)
    known_end = dict(state.known_free_until).get(owner)
    free_excluded = ((stance is not None and not stance.free_battle)
                     or (known_end is not None and now >= known_end))
    # A direct, explicitly observed Conduct tag does not need OR narrowing.
    observed_conduct = (positive and assessment.inference.confirms('tag', spec['conduct_tag']))
    return _sword_quote(row, spec, positive, requests, stance, free_excluded or observed_conduct)


def _spear_quote(row, owner, spec, positive, projected, requests, state):
    p = spec['ordinary']
    base = row['quotes']['battle']['crit_expect']
    if not positive and not projected:
        return MechanismQuote(base, 'whirlwind; condition_unknown_or_dark')
    # Until coexistence/replacement caps are audited, only one batch of each
    # type contributes. Keep older histories without crediting all overlap.
    selected = {r.kind: r for r in sorted(requests, key=lambda r: r.launch_at)}
    normal = selected['normal_spear'].nominal_count if 'normal_spear' in selected else 0
    strong = selected['strong_spear'].nominal_count if 'strong_spear' in selected else 0
    if normal or strong:
        value = _unit(row)*(p['atk_scale']+normal*p['atk_scale_lance']+strong*p['atk_scale_lance_ult'])
        return MechanismQuote(value, 'nominal_recall_from_source_history', normal_spears=normal or None,
                              strong_spears=strong or None, sources=tuple(r.source for r in selected.values()),
                              assumptions=('arrival_unconfirmed', 'path_hit_unconfirmed', 'untracked_inventory_excluded',
                                           'overlapping_same_kind_not_accumulated'))
    if owner not in state.minimum_spent:
        extra = min(p['atk_scale_lance'], p['atk_scale_lance_ult'])*spec['minimum_nominal_recall']
        return MechanismQuote(_unit(row)*(p['atk_scale']+extra), 'minimum_untyped_nominal_recall',
                              minimum_recall=True, assumptions=('kind_and_count_unknown', 'path_hit_unconfirmed'))
    return MechanismQuote(base, 'whirlwind; previous_minimum_already_used')


def _sword_quote(row, spec, positive, requests, stance, free_excluded):
    enhanced = stance is not None
    free = enhanced and stance.free_battle
    # This excludes the free arm only in the combat model, never in observed
    # enemy facts. The raw OR observation remains intact.
    conduct = 0 if free else 1 if positive and free_excluded else None
    baseline = (row['stance']['quotes']['battle'] if enhanced else row['quotes']['battle'])['crit_expect']
    if conduct is None:
        return MechanismQuote(baseline, 'single_unit; conduct_or_free_unknown')
    swords = min(spec['count_cap'], sum(r.nominal_count for r in requests if r.kind == 'sword'))
    if not swords:
        return MechanismQuote(baseline, 'single_unit; sword_snapshot_unknown', conduct=conduct,
                              assumptions=('initial_inventory_unknown',))
    value = salvo_value(row, spec, conduct, swords, enhanced=enhanced)
    return MechanismQuote(value, 'nominal_salvo_from_previous_requests', conduct, swords,
                          sources=tuple(r.source for r in requests),
                          assumptions=('arrival_unconfirmed', 'owner_range_unmeasured', 'untracked_inventory_excluded',
                                       'model_excluded_free_arm' if not free else 'first_free_consumes_no_conduct'))


def salvo_value(row, spec, conduct, swords, *, enhanced=False):
    if (type(conduct) is not int or not 0 <= conduct <= 4
            or type(swords) is not int or not 1 <= swords <= spec['count_cap']):
        raise ValueError('Salvo requires explicit conduct and nonempty sword inputs')
    p = spec['enhanced' if enhanced else 'ordinary']
    regular = _f32(p['atk_scale']+_f32(conduct*p['atk_up_per_conduct']))
    return _unit(row)*((swords-1)*regular+_f32(regular*p['final_rate']))


def after_action(state, row, owner, kind, started, *, quote=None, stance=None, forecast=False):
    spec = row.get('mechanism')
    if spec is None:
        return state
    source = f'{owner}:{kind}:{float(started).hex()}'
    if spec['type'] == 'sword_salvo' and kind == 'ult':
        end = started+row['stance']['starts_after']+row['stance']['duration']
        return replace(state, known_free_until=tuple((key, value) for key, value in state.known_free_until
                                                     if key != owner)+((owner, end),))
    if spec['type'] == 'sword_salvo' and kind == 'battle':
        free = stance is not None and stance.free_battle
        count = spec['free_requests'] if free else (
            min(quote.conduct+1, spec['conduct_requests_cap']) if quote and quote.conduct else 0)
        phase = 'enhanced' if stance is not None else 'ordinary'
        return state.request(owner, source, 'sword', count, started+spec['launch_after'][phase],
                             spec['duration'], cap=spec['count_cap'], forecast=forecast)
    production = spec.get('productions', {}).get(kind)
    if production is not None:
        return state.request(owner, source, production['kind'], production['count'],
                             started+production['launch_after'], production['duration'], forecast=forecast)
    if spec['type'] == 'spear_recall' and kind == 'battle':
        selected = quote.sources if quote else ()
        requests = tuple(replace(r, phase='returning' if r.source in selected else 'recall_unknown')
                         if r.owner == owner and r.phase == 'requested' and r.launch_at <= started < r.quote_until
                         else r for r in state.requests)
        return replace(state, requests=requests, minimum_spent=state.minimum_spent | {owner})
    return state
