"""Selected next-battle/cast-quote equipment contracts, not native buff execution."""

import hashlib
import json
import re
from pathlib import Path

RANGLIU_SHA256 = '15bafef48437e2cfa285e03b6d1bf9dfe5360084dbeba44fa6ffa66da08a32f3'


def sword_equipment(row, source):
    """Admit only the selected build and the separately reviewed rank contract."""
    gear_path = source/'assets/data/equipments.json'
    gear = json.loads(gear_path.read_text(encoding='utf8'))
    if sum(gear[name]['set']=='壤流装备组' for name in row['build']['pieces']) < 3:
        raise ValueError('Selected sword build no longer activates Rangliu')
    path = Path(__file__).resolve().parents[2]/'assets/data/release_burst/rangliu-contract.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != RANGLIU_SHA256:
        raise ValueError('Changed reviewed Rangliu evidence')
    evidence = json.loads(raw)
    if (evidence['event'] != {'name':'OnConsumeBuff', 'value':208, 'tags':['bf9d6e57', 'edaee3e6']}
            or evidence['recipient']['source']!='Owner' or evidence['recipient']['value']!=4
            or evidence['skill_rank']!=1 or evidence['attributes']!={'52':'PulseDamageIncrease', '54':'NaturalDamageIncrease'}):
        raise ValueError('Changed Rangliu producer or recipient')
    return {'amount_per_stack': evidence['damage_bonus_per_stack'], 'duration': evidence['duration'],
            'max_stacks': evidence['max_stacks'], 'elements': evidence['elements'],
            'scope':'nominal_conduct_consumption; future_quotes_only',
            'contract_sha256': RANGLIU_SHA256,
            'gear_sha256': hashlib.sha256(gear_path.read_bytes()).hexdigest()}


def sword_weapon(row, source):
    if row['build']['weapon']!='孤舟':
        raise ValueError('Changed sword weapon requires review')
    path = source/'assets/data/equipment_mechanics/20261008/guzhou.json'
    raw = path.read_bytes()
    digest = '644d440edab4454cfe4d65b10d06233e53020c400caeb5a956aceec89841caa6'
    if hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Changed Guzhou consumer evidence')
    evidence = json.loads(raw)
    records = evidence['records']
    bb = {p['key']:p['value'] for p in evidence['selected_rank_patch']['blackboard']}
    root = records['sk_wpn_funnel_0015']['data']['actionGroupData']['passiveEventActions']
    nodes = [n['$value'] for n in next(e for e in root if e['abilityEvent']==208)['actions'][0]['actionData']]
    combo, ult = (records['buff_wpn_funnel_0015_'+suffix]['data'] for suffix in ('combo','ultimate'))
    if (nodes[1]['skillTypeList']!=[2] or nodes[2]['query']['tags']!=[{'raw':'d270dc57'}]
            or nodes[3]['count']['useBlackboardKey'] or nodes[3]['count']['value']!=1
            or combo['damageModifier']!=ult['damageModifier']
            or combo['stackingSettings']['identifierType']!=0
            or combo['stackingSettings']['stackingKey'] or ult['stackingSettings']['stackingKey']):
        raise ValueError('Changed Guzhou consumption or independent modifier identity')
    return {'key':'guzhou', 'amount_per_stack':bb['pulse_dmg_up2'], 'duration':bb['duration'],
            'max_stacks':int(bb['max_stack']), 'elements':['电磁'], 'damage_tags':['skill'],
            'scope':'nominal_conduct_consumption; future_quotes_only', 'source_sha256':digest,
            'stacks_per_cast':1, 'buff_id':'buff_wpn_funnel_0015_combo'}


def spear_equipment(row, source):
    if row['build']['weapon'] != 'J.E.T.':
        raise ValueError('Changed recall weapon requires review')
    weapons_path = source/'assets/data/weapons.json'
    weapons = json.loads(weapons_path.read_text(encoding='utf8'))
    clauses = [group['ranks']['Rank 9']['压制·太空物理学'] for group in weapons['J.E.T.']['weapon_skills']
               if '压制·太空物理学' in group.get('ranks', {}).get('Rank 9', {})]
    pattern = r'装备者施放连携技时，获得法术伤害\+(\d+(?:\.\d+)?)%，持续(\d+(?:\.\d+)?)秒。'
    matches = re.findall(pattern, clauses[0]) if len(clauses)==1 else []
    if len(matches)!=1 or '两种效果独立生效，且均无法叠加' not in clauses[0]:
        raise ValueError('Changed JET independent combo clause')
    gear_path = source/'assets/data/equipments.json'
    gear = json.loads(gear_path.read_text(encoding='utf8'))
    pieces = [name for name in row['build']['pieces'] if gear[name]['set']=='碾骨装备组']
    if len(pieces)<3:
        raise ValueError('Selected recall build no longer activates Bonecrusher')
    path = source/'assets/data/equipment_mechanics/20261008/next_skill_sets.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '3d6dbaa2c49b3713dd860c644d583286dd5f67c7da247f0f57526d77f7fc3465':
        raise ValueError('Changed next-skill native evidence')
    evidence = json.loads(raw)
    selected = evidence['sets']['suit_attri01']['rank_patch']
    bb = {b['key']: b['value'] for b in selected['blackboard']}
    records = evidence['records']
    parent = records['buff_equipsuit_attrisuit_01']['data']['abilityEventAction'][0]
    pending = records['buff_equipsuit_attrisuitup_01']['data']['abilityEventAction'][0]
    nodes = pending['actions'][0]['actionData']
    if (parent['abilityEvent']!=30 or parent['actions'][0]['actionData'][0]['$value']['skillTypeList']!=[6]
            or pending['abilityEvent']!=30 or nodes[0]['$value']['skillTypeList']!=[2]
            or not nodes[-1]['$value']['finishAll'] or nodes[-2]['$value']['asChildBuff']
            or not nodes[-2]['$value']['inheritSourceSkillCastInfo']):
        raise ValueError('Changed next-battle snapshot/consume ordering')
    return {'link_weapon': {'amount': float(matches[0][0])/100, 'duration': float(matches[0][1])},
            'next_battle': {'amount_per_stack': bb['dmg_up'], 'max_stacks': int(bb['max_stack']),
                            'consume': 'all_on_accepted_battle', 'duration': None,
                            'scope': 'cast_quote_only; no_native_SkillAffix_lifecycle_claim'},
            'sources': {'weapons.json': hashlib.sha256(weapons_path.read_bytes()).hexdigest(),
                        'equipments.json': hashlib.sha256(gear_path.read_bytes()).hexdigest(),
                        'next_skill_sets.json': hashlib.sha256(raw).hexdigest()},
            'native_records': {key: records[key]['source']['sha256'] for key in
                               ('buff_equipsuit_attrisuit_01', 'buff_equipsuit_attrisuitup_01',
                                'buff_equipsuit_attrisuitup_02')}}
