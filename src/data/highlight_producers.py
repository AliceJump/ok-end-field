"""May-produce closure for the current roster's audited skill source model.

This boundary does not assert that a hit occurred or that an enemy/environment
cannot supply a status. It only narrows button-local alternatives in this model;
observed contradictions invalidate the narrowing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / 'assets/data/highlight_producers'


@lru_cache(maxsize=4)
def read_producers(path=SNAPSHOT):
    path = Path(path)
    manifest = json.loads((path / 'index.json').read_text(encoding='utf8'))
    raw = (path / 'snapshot.json').read_bytes()
    data = json.loads(raw)
    if (manifest['schema_version'] != 1 or data['schema_version'] != 1
            or data['scope'] != 'current_team_skill_source_model'
            or hashlib.sha256(raw).hexdigest() != manifest['snapshot_sha256']
            or len(data['actors']) != manifest['actors'] or not manifest['source_hashes']
            or re.fullmatch('[0-9a-f]{40}', manifest['source_revision']) is None):
        raise ValueError('Highlight producer snapshot provenance/hash mismatch')
    domain = set(data['tags'])
    if (any(not set(a['tags']) <= domain or a['closed'] != (not a['gaps']) for a in data['actors'].values())
            or sum(a['closed'] for a in data['actors'].values()) != manifest['closed']):
        raise ValueError('Invalid producer source boundary')
    for actor, transitions in data['condition_transitions'].items():
        for spec in transitions:
            if (actor != 'antal' or spec['kind'] != 'ult' or spec['skill_id'] != 'chr_0023_antal_ultimate_skill'
                    or spec['scope'] != 'nominal_unchanged_enemy_tags; no_target_identity_or_remaining_lifetime'
                    or not spec['record_hashes'] or spec['skill_id'] not in spec['record_hashes']
                    or not spec['preserves_recommended_tags'] or not set(spec['preserves_recommended_tags']) <= domain):
                raise ValueError('Invalid reviewed condition transition')
    for actor, binding in data['damage_bindings'].items():
        if actor == 'arclight':
            components = binding['base_components'] + binding['conditional_components']
            if (binding['scope'] != 'native_nominal_components_without_hit_confirmation; current_action_only'
                    or binding['rank'] != 12 or binding['potential'] != 5
                    or [c['element'] for c in components] != ['物理', '电磁']
                    or [c['hits'] for c in components] != [2, 1]
                    or any(type(c['hits']) is not int or c['damage_tags'] != ['skill']
                           or any(type(c[k]) not in (int, float) or not math.isfinite(c[k]) or c[k] <= 0
                                  for k in ('parameter', 'non_crit', 'crit_expect'))
                           or not math.isfinite(c['bonus_pct']) or c['bonus_pct'] <= -100 for c in components)):
                raise ValueError('Invalid highlight damage components')
            continue
        if (binding['scope'] != 'native_nominal_projectiles_without_hit_confirmation; current_action_only'
                or any(type(binding[key]) is not int or binding[key] <= 0
                       for key in ('rank', 'base_projectiles', 'extra_projectiles'))
                or type(binding['potential']) is not int or not 0 <= binding['potential'] <= 5
                or any(type(binding[key]) not in (int, float) or not math.isfinite(binding[key]) or binding[key] <= 0
                       for key in ('base_parameter', 'extra_parameter', 'failed_extra_parameter'))):
            raise ValueError('Invalid highlight damage binding')
    return data


@dataclass(frozen=True)
class TeamProducerClosure:
    tags: frozenset[str]
    domain: frozenset[str]
    closed: bool
    gaps: tuple[str, ...] = ()

    @classmethod
    def for_team(cls, team, *, snapshot=SNAPSHOT):
        data = read_producers(snapshot)
        rows, gaps = [], []
        for index, name in enumerate(team):
            row = data['actors'].get(name)
            if row is None:
                gaps.append(f'unknown_roster:{index + 1}:{name}')
            else:
                rows.append(row)
                gaps.extend(f'{name}:{gap}' for gap in row['gaps'])
        if not team:
            gaps.append('empty_roster')
        tags = {tag for row in rows for tag in row['tags']}
        # Different elements, in either order, may create the new element's
        # abnormal status. A single attachment type is not a cross reaction.
        elements = [e for e in data['elements'] if e['attachment'] in tags]
        if len(elements) >= 2:
            tags.update(e['status'] for e in elements)
        return cls(frozenset(tags), frozenset(data['tags']), not gaps, tuple(gaps))

    def possible(self, atom):
        # Enemy target selectors are not interchangeable with owner buffs.
        # No proof for private resources, entity tags or unsupported predicates.
        if atom.kind != 'tag' or not atom.recommended_target or atom.identity not in self.domain:
            return None
        if atom.identity in self.tags:
            return True
        return False if self.closed else None
