"""The reviewed cast envelope, kept separate from generic producer traversal."""

import hashlib
import json

WOLF_RECORDS = {
    'chr_0006_wolfgd_normal_skill': '910e4c688c4c9b7c8d11e24a18cdd8c357181c6ab3a2686e7699b57c3e470f30',
    'chr_0006_wolfgd_normal_skill_projhit': '286161c746b125a6a9628b6abe5ee81d6cc1ab83ac3c611b9256677f29d3ff89',
    'chr_0006_wolfgd_normal_skill_projhit_1': '06d3f7942c6e5e6379cb8c7d59d7eecd2be472514b1c5f825faa232ac2291343',
    'chr_0006_wolfgd_normal_skill_projhit_FireSpellInfiction': '909cfefb65aad9ab1f03653e40b02ae3cd7779e4dcfc6f97d68a8a5d15f96e2d',
    'chr_0006_wolfgd_normal_skill_plus_projhit': '9545b02ee32225dc9095546a89473e09853205c1482096c66d43fb179444b5e4',
}


def reviewed_damage(store, fixed_rows):
    row = next(r for r in fixed_rows if r['key'] == 'wulfgard')
    skill_id = 'chr_0006_wolfgd_normal_skill'
    for key, digest in WOLF_RECORDS.items():
        if store.record(key)['source']['sha256'] != digest:
            raise ValueError('Unreviewed conditional damage program')
    rank = row['profile']['skill_rank']
    native_row = store.ranked_skill(skill_id, rank)
    return {'wulfgard': {'skill_id': skill_id, 'rank': rank, 'potential': row['profile']['potential'],
                         'base_projectiles': 3, 'extra_projectiles': 1,
                         'base_parameter': store.ranked_parameter(skill_id, 'atk_scale', rank),
                         'extra_parameter': store.ranked_parameter(skill_id, 'atk_scale_plus', rank),
                         'failed_extra_parameter': store.ranked_parameter(skill_id, 'atk_scale_plus_fail', rank),
                         'element': '灼热', 'damage_tags': ['skill'],
                         'scope': 'native_nominal_projectiles_without_hit_confirmation; current_action_only',
                         'record_hashes': WOLF_RECORDS,
                         'ranked_row_sha256': hashlib.sha256(json.dumps(native_row, sort_keys=True,
                                                                        separators=(',', ':')).encode()).hexdigest()}}
