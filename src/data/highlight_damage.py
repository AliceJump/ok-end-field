"""Reviewed nominal damage branches driven by the current button condition.

The quote is a cast valuation, not hit confirmation. Native projectiles keep
their later target/status checks; no enemy status, consumption or payout is
written back from this prediction.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.highlight_producers import read_producers


@dataclass(frozen=True)
class HighlightDamageBinding:
    skill_id: str
    base_value: float
    conditional_value: float

    @classmethod
    def for_actor(cls, row, store):
        # The source audit currently closes only this nominal damage program.
        # Other conditions remain observations until their quote is reviewed.
        if row['key'] != 'wulfgard' or store is None:
            return None
        spec = read_producers()['damage_bindings'][row['key']]
        for skill_id, digest in spec['record_hashes'].items():
            if store.record(skill_id)['source']['sha256'] != digest:
                raise ValueError('Unreviewed highlight damage record')
        skill_id = spec['skill_id']
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
        matched = (observation is not None and observation.ready is True
                   and observation.skill_ids == (self.skill_id,))
        return self.base_value, self.conditional_value if matched else 0.0
