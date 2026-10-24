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

ARCLIGHT_RECORDS = {
    'chr_0007_ikut_normal_skill': '338ddd04e7453cb9fba84e8d867b1e61fd2eba38140b9f1bf6b774b19297dd50',
}


def basis_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def arclight_damage(store, row):
    skill_id = 'chr_0007_ikut_normal_skill'
    for key, digest in ARCLIGHT_RECORDS.items():
        if store.record(key)['source']['sha256'] != digest:
            raise ValueError('Unreviewed Arclight conditional damage program')
    rank = row['profile']['skill_rank']
    panel = row['panel']['damage_basis']
    attack = (panel['attack_white'] * (1 + panel['attack_percent']) + panel['attack_flat']) * panel['attribute_factor']
    quotes = []
    for element, count, key in (('物理', 2, 'atk_scale'), ('电磁', 1, 'atk_scale2')):
        parameter = store.ranked_parameter(skill_id, key, rank)
        fixed = panel['damage_bonus']
        bonus = fixed['all'] + fixed['all_skill'] + fixed[element] + fixed['skill']
        value = attack * count * parameter * (1 + bonus) * (1 + panel['amplification'][element])
        quotes.append({'element': element, 'damage_tags': ['skill'], 'hits': count,
                       'parameter_key': key, 'parameter': parameter, 'bonus_pct': bonus * 100,
                       'non_crit': value, 'crit_expect': value * (1 + panel['crit_rate'] * panel['crit_damage'])})
    return {'skill_id': skill_id, 'rank': rank, 'potential': row['profile']['potential'],
            'scope': 'native_nominal_components_without_hit_confirmation; current_action_only',
            'base_components': quotes[:1], 'conditional_components': quotes[1:],
            'record_hashes': ARCLIGHT_RECORDS, 'panel_sha256': basis_hash(panel),
            'profile_sha256': basis_hash(row['profile']), 'build_sha256': basis_hash(row['build']),
            'ranked_row_sha256': basis_hash(store.ranked_skill(skill_id, rank)),
            'control_flow': {'base_frames': [19, 24], 'enhanced_base_frames': [112, 118],
                             'conditional_frame': 136, 'conduct_entry_sets': 'SpawnThird=1',
                             'jump_compare': 'Equals=4', 'enhanced_jump': 96, 'base_exit_jump': 204,
                             'conditional_target': 'smart_target', 'other_target_group': 'tar_excludes_smart_target',
                             'scope': 'nominal_recommended_target_in_range; late_conduct_and_distance_recheck_unobserved'}}


def reviewed_damage(store, fixed_rows):
    row = next(r for r in fixed_rows if r['key'] == 'wulfgard')
    skill_id = 'chr_0006_wolfgd_normal_skill'
    for key, digest in WOLF_RECORDS.items():
        if store.record(key)['source']['sha256'] != digest:
            raise ValueError('Unreviewed conditional damage program')
    rank = row['profile']['skill_rank']
    native_row = store.ranked_skill(skill_id, rank)
    result = {'wulfgard': {'skill_id': skill_id, 'rank': rank, 'potential': row['profile']['potential'],
                         'base_projectiles': 3, 'extra_projectiles': 1,
                         'base_parameter': store.ranked_parameter(skill_id, 'atk_scale', rank),
                         'extra_parameter': store.ranked_parameter(skill_id, 'atk_scale_plus', rank),
                         'failed_extra_parameter': store.ranked_parameter(skill_id, 'atk_scale_plus_fail', rank),
                         'element': '灼热', 'damage_tags': ['skill'],
                         'scope': 'native_nominal_projectiles_without_hit_confirmation; current_action_only',
                         'record_hashes': WOLF_RECORDS,
                         'ranked_row_sha256': hashlib.sha256(json.dumps(native_row, sort_keys=True,
                                                                        separators=(',', ':')).encode()).hexdigest()}}
    result['arclight'] = arclight_damage(store, next(r for r in fixed_rows if r['key'] == 'arclight'))
    return result
