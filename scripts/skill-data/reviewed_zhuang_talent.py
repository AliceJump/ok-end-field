"""Review the selected talent for conditional model events, not native execution."""

import hashlib

PAYLOAD = 'assets/data/character_mechanics/20261008/zhuangfy_release.json.gz'
PAYLOAD_SHA256 = 'f6dadf3d26e7dc5695096fff69dafe1f96710b6aca04fb21a494ad3ff73e019d'


def sword_talent(row, source):
    from src.data.native_zhuangfy_evidence import read_snapshot

    from src.data.character_skills import get_character

    if row['profile']['character_level'] != 90 or row['profile']['potential'] != 0:
        raise ValueError('Changed talent level or potential requires review')
    raw = (source / PAYLOAD).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PAYLOAD_SHA256:
        raise ValueError('Unreviewed Zhuang talent source')
    character = get_character('zhuang_fangyi', potential=row['profile']['potential'],
                              skill_rank=row['profile']['skill_rank'])
    selected = next(p for p in character.progression.talents if p.effect_id == 'chr_0030_zhuangfy_talent_1_2')
    parameters = dict(selected.parameters)
    if parameters != {'base_rate': 0.18000000715255737, 'duration': 5.0,
                      'enhance_rate': 0.019999999552965164}:
        raise ValueError('Changed selected talent parameters')
    records = read_snapshot(root=source)
    def markers(skill):
        def walk(value):
            if isinstance(value, dict):
                if value.get('$type', '').endswith('CreateBuffAction+Data'):
                    yield value['$value']
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)
        return [(block['_startFrame'], action) for block in records[skill]['data']['actionGroupData']['timelineActions']
                for action in walk(block['_sequenceActionData'])]
    for skill, frame in (('chr_0030_zhuangfy_normal_skill', 6), ('chr_0030_zhuangfy_normal_skill_ult', 5)):
        found = [stamp for stamp, action in markers(skill)
                 if any(item['buffId'] == 'buff_chr_0030_zhuangfy_talent1' for item in action['buffs'])]
        if found != [frame, frame]:
            raise ValueError('Changed conditional base marker branches')
    marks = [(stamp, action['serverActionIndex']) for stamp, action in markers('chr_0030_zhuangfy_normal_skill_ult_abilityrange')
             if any(item['buffId'] == 'buff_chr_0030_zhuangfy_talent1_mark' for item in action['buffs'])]
    if marks != [(12, 13), (69, 24)]:
        raise ValueError('Changed regular or final amplification marker')
    listener = records['chr_0030_zhuangfy_talent1']['data']['actionGroupData']['passiveEventActions'][0]
    condition, creation = [node['$value'] for node in listener['actions'][0]['actionData']]
    if (listener['abilityEvent'] != 9 or condition['buffIdList'] != [{'buffId': 'buff_chr_0030_zhuangfy_talent1'}]
            or creation['buffs'][0]['buffId'] != 'buff_chr_0030_zhuangfy_talent1_base'):
        raise ValueError('Changed talent marker receiver')
    base = records['buff_chr_0030_zhuangfy_talent1_base']['data']
    action = base['buffEventAction'][0]['actions'][0]['actionData'][0]['$value']
    if (base['duration']['blackboardKey'] != 'duration' or base['stackingSettings']['stackingType'] != 2
            or base['stackingSettings']['maxStackCnt'] != 1 or action['subType'] != 6
            or action['rate']['blackboardKey'] != 'base_rate' or action['overrideChildBuffId']
            or action['enhancingList'][0]['operationType'] != 1
            or action['enhancingList'][0]['value']['blackboardKey'] != 'enhance_rate'
            or action['enhancingList'][0]['buffIds'] != ['buff_chr_0030_zhuangfy_talent1_mark']):
        raise ValueError('Changed talent enhancement or refresh')
    keyword = records['buff_common_affixes_enhance_pulse']['data']
    modifier = keyword['attributeModifier']['attributeModifiers'][0]
    if (modifier['attributeType'] != 66 or modifier['param']['blackboardKey'] != 'rate'
            or keyword['stackingSettings']['priorityKey'] != 'rate'
            or not keyword['stackingSettings']['usePriorityKey']):
        raise ValueError('Changed electric amplification group')
    # Authored nominal phases. No projectile arrival, hit or native Jump claim.
    return {**parameters, 'base_after': {'ordinary': 6/30, 'enhanced': 5/30},
            'enhanced_area_after': 15/30, 'first_mark_after_area': 12/30,
            'mark_interval': .2, 'last_mark_after_area': 69/30,
            'scope': 'nominal_skill_mark_projection; not_observed_marks_or_native_keyword_execution',
            'stack_group': 'electric_enhancement_highest_active',
            'source_sha256': PAYLOAD_SHA256,
            'native_hashes': {key: record['source']['sha256'] for key, record in records.items()}}
