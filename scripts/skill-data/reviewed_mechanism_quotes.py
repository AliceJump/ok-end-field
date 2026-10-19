"""Export nominal sword/recall contracts, not observations of spawned entities."""

import hashlib
import json

from reviewed_mechanism_equipment import spear_equipment

VALUE = '$value'
HASHES = {
    'chr_0030_zhuangfy_normal_skill': '06a61a8b2630fbe792294c3a0dc0a9a24080d8c8be45cb5895d2f16f0f05a9fd',
    'chr_0030_zhuangfy_normal_skill_ult': '33483a4c247260eb69cdd90ab80a604c113fb8e57a79817d3a39f54dbf971ccf',
    'chr_0012_avywen_normal_skill': 'dfadd90a92539712513bfcfe93e59add3e134abc455b6056fdaa200948970e3c',
    'chr_0012_avywen_combo_skill': '533999c78e4539690b9888207fddaa29e9417b22c437e99d167f0c2c5ed2696c',
    'chr_0012_avywen_ultimate_skill': '0940cbb746d6cc88a82db4853c76022eed0db1ef04a2406b755cf75ea88fa9a1',
    'chr_0012_avywen_combo_skill_lance_gene': 'f7269976a92ef4fd83bf456558169e758105ea80308516df6ede25ec532153e5',
    'chr_0012_avywen_ultimate_skill_lance_gene': 'af59eb8e05369fff65782c2814eb0962c43591c78fe2be064898ea4997bde8e1',
    'chr_0030_zhuangfy_normal_skill_ult_abilityrange': 'd8c052a17e9f1332ba95277d19027e5df67af4e747fc601b4b171744f7daa6a2',
    'buff_chr_0030_zhuangfy_sword_triggerd': '789e3e86447639ddaa63c03e405aa91f1f29e2dbe2b037bb3adfca174e6abb2b',
    'chr_0030_zhuangfy_normal_skill_gene_sword_projhit': '8ffa9d0b679002efa96c3f92d809e1afb60c377bd0d1751256817fb9c66385c7',
    'chr_0030_zhuangfy_check_sword_passive': '7c8a4b2434cbbcca3be9a049d958a023661770cd5fa3c68be3fd2bc1e89f93ee',
    'buff_chr_0030_zhuangfy_passive_check_sword': '42cbce96f4b0f4aa9150067c43c36005a51cb9230e1cf45fd4edc6bd82f0014c',
}


def _nodes(value):
    if isinstance(value, dict):
        if '$type' in value:
            yield value['$type'].split('.')[-1], value.get(VALUE, {})
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _launch_frame(data, index):
    matches = [b['_startFrame'] for b in data['actionGroupData']['timelineActions']
               for name, node in _nodes(b['_sequenceActionData'])
               if name == 'LaunchProjectile+Data' and node['serverActionIndex'] == index and node['isEnable']]
    if len(matches) != 1:
        raise ValueError('Unreviewed mechanism launch phase')
    return matches[0]


def _params(store, skill):
    return {p['key']: p['value'] for p in store.ranked_skill(skill, 12)['blackboard']}


def _sword_contract(store, records):
    base, enhanced = 'chr_0030_zhuangfy_normal_skill', 'chr_0030_zhuangfy_normal_skill_ult'
    ordinary, stance = _params(store, base), _params(store, enhanced)
    region = records['chr_0030_zhuangfy_normal_skill_ult_abilityrange']['data']
    final = [body for name, body in _nodes(region) if name == 'SimpleCalcBBAction+Data'
             and body.get('serverActionIndex') == 25]
    if len(final) != 1 or final[0]['value2']['blackboardKey'] != 'final_rate' or stance['final_rate'] != 6:
        raise ValueError('Unreviewed final strike factor')
    return {'type': 'sword_salvo', 'skills': [base, enhanced],
            'ordinary': {k: ordinary[k] for k in ('atk_scale', 'atk_up_per_conduct', 'final_rate')},
            'enhanced': {k: stance[k] for k in ('atk_scale', 'atk_up_per_conduct', 'final_rate')},
            'count_cap': int(ordinary['remain_sword_limit']), 'duration': ordinary['sword_duration'],
            'free_requests': int(stance['free_sword_limit']),
            'conduct_requests_cap': int(ordinary['max_conduct_sword']),
            'launch_after': {'ordinary': _launch_frame(records[base]['data'], 59)/30,
                             'enhanced': _launch_frame(records[enhanced]['data'], 59)/30},
            'damage_duration': {'ordinary': store.profile(base).duration, 'enhanced': store.profile(enhanced).duration},
            'conduct_tag': 'bf9d6e57'}


def _spear_contract(store, records, source):
    base, link, ult = ('chr_0012_avywen_'+name for name in ('normal_skill', 'combo_skill', 'ultimate_skill'))
    data = records[base]['data']
    highlight_find = data['skillHighlightCondition']['actionData'][0][VALUE]
    selectors = [node for name, node in _nodes(data['actionGroupData'])
                 if name == 'FindTargetAction+FindTargetActionData' and node.get('targetGroupKey') == 'lances']
    # The highlight's priority filter is a subset of the recalled owner/tag
    # selector; it proves nominal presence, not a typed count or a path hit.
    if not selectors or any(s['selectorOwner'] != highlight_find['selectorOwner']
                            or s['selectorData']['finderData'] != highlight_find['selectorData']['finderData']
                            or s['selectorData']['validatorData'] != highlight_find['selectorData']['validatorData']
                            for s in selectors):
        raise ValueError('Highlight no longer supports minimum recalled entity')
    canonical = json.loads((source/'assets/data/character_skills/avywenna.json').read_text(encoding='utf8'))
    for suffix in ('combo_skill_lance_gene', 'ultimate_skill_lance_gene'):
        actions = [body for name, body in _nodes(records['chr_0012_avywen_'+suffix]['data'])
                   if name == 'SpawnAbilityEntity+Data' and body['isEnable']]
        if len(actions) != 1 or actions[0]['dieOnEnd']:
            raise ValueError('Unreviewed spear entity creation')
    effects = {s['skill_id']: s['effects'][0] for s in canonical['skills']
               if s['skill_id'] in {'avywenna_link', 'avywenna_ultimate'}}
    if effects['avywenna_link']['count'] != 3 or effects['avywenna_ultimate']['count'] != 1:
        raise ValueError('Unreviewed nominal spear production counts')
    params = _params(store, base)
    return {'type': 'spear_recall', 'skills': [base],
            'ordinary': {k: params[k] for k in ('atk_scale', 'atk_scale_lance', 'atk_scale_lance_ult')},
            'productions': {
                'link': {'kind': 'normal_spear', 'count': effects['avywenna_link']['count'],
                         'duration': _params(store, link)['lance_duration'],
                         'launch_after': _launch_frame(records[link]['data'], 28)/30},
                'ult': {'kind': 'strong_spear', 'count': effects['avywenna_ultimate']['count'],
                        'duration': _params(store, ult)['lance_duration_ult'],
                        'launch_after': _launch_frame(records[ult]['data'], 23)/30}},
            'minimum_nominal_recall': 1,
            'damage_duration': store.profile(base).duration,
            'canonical_sha256': hashlib.sha256((source/'assets/data/character_skills/avywenna.json').read_bytes()).hexdigest()}


def mechanism_contract(row, store, source):
    from src.data.native_gameplay import native_record

    if row['key'] not in {'zhuang_fangyi', 'avywenna'}:
        return None
    if row['profile']['skill_rank'] != 12 or row['profile']['potential'] != (0 if row['key']=='zhuang_fangyi' else 5):
        raise ValueError('Unreviewed mechanism profile')
    prefix = 'zhuangfy' if row['key']=='zhuang_fangyi' else 'avywen'
    ids = [key for key in HASHES if prefix in key]
    records = {key: native_record(store, key) for key in ids}
    if any(records[key]['source']['sha256'] != HASHES[key] for key in ids):
        raise ValueError('Unreviewed mechanism source')
    result = (_sword_contract(store, records) if row['key']=='zhuang_fangyi'
              else _spear_contract(store, records, source))
    result.update(scope='conditional_nominal_projection; not_observed_inventory_or_hit',
                  native_hashes={key: HASHES[key] for key in ids},
                  condition_hashes={key: hashlib.sha256(json.dumps(records[key]['data']['skillHighlightCondition'],
                                    sort_keys=True,separators=(',',':')).encode()).hexdigest()
                                    for key in result['skills']})
    if row['key']=='avywenna':
        result['equipment'] = spear_equipment(row, source)
    return result
