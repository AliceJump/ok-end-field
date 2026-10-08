"""Reviewed nominal damage branches driven by the current button condition.

The quote is a cast valuation, not hit confirmation. Native projectiles keep
their later target/status checks; no enemy status, consumption or payout is
written back from this prediction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from src.data.highlight_producers import read_producers


@dataclass(frozen=True)
class HighlightDamageComponent:
    element: str
    damage_tags: tuple[str, ...]
    value: float
    bonus_pct: float

    @classmethod
    def from_spec(cls, row):
        return cls(row['element'], tuple(row['damage_tags']), row['crit_expect'], row['bonus_pct'])


@dataclass(frozen=True)
class HighlightDamageBinding:
    skill_id: str
    base_value: float
    conditional_value: float
    base_components: tuple[HighlightDamageComponent, ...] = ()
    conditional_components: tuple[HighlightDamageComponent, ...] = ()

    @classmethod
    def for_actor(cls, row, store):
        if store is None:
            return None
        spec = read_producers()['damage_bindings'].get(row['key'])
        if spec is None:
            return None
        for skill_id, digest in spec['record_hashes'].items():
            if store.record(skill_id)['source']['sha256'] != digest:
                raise ValueError('Unreviewed highlight damage record')
        skill_id = spec['skill_id']
        if row['key'] == 'arclight':
            for key in ('panel', 'profile', 'build'):
                digest = hashlib.sha256(json.dumps(row[key], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                if digest != spec[key + '_sha256']:
                    raise ValueError('Unsupported highlight damage component basis')
            base = tuple(HighlightDamageComponent.from_spec(c) for c in spec['base_components'])
            extra = tuple(HighlightDamageComponent.from_spec(c) for c in spec['conditional_components'])
            return cls(skill_id, sum(c.value for c in base), sum(c.value for c in extra), base, extra)
        quote = row['quotes']['battle']
        if (quote['quote_basis']['element'] != spec['element'] or quote['quote_basis']['damage_tags'] != spec['damage_tags']
                or row['profile']['skill_rank'] != spec['rank'] or row['profile']['potential'] != spec['potential']):
            raise ValueError('Unsupported highlight damage quote basis')
        unit = quote['crit_expect'] / quote['quote_basis']['multiplier']
        # Two initial native projectiles plus exactly one of the third-shot
        # variants. The extra projectile uses atk_scale_plus on the valid
        # Burning/Conduct branch; its failure branch remains unforecast.
        base = spec['base_projectiles'] * spec['base_parameter']
        extra = spec['extra_projectiles'] * spec['extra_parameter']
        return cls(skill_id, unit * base, unit * extra)

    def values(self, observation):
        return self.base_value, self.conditional_value if self.matches(observation) else 0.0

    def matches(self, observation):
        matched = (observation is not None and observation.ready is True
                   and observation.skill_ids == (self.skill_id,))
        return matched

    def components(self, observation):
        return self.base_components, self.conditional_components if self.matches(observation) else ()
