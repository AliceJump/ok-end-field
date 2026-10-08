"""Validate reviewed rank-row counts against finite native damage schedules.

Frames are attack opportunities. They do not establish hits, target persistence,
entity blackboard propagation or the execution of a complete native program.
"""

from __future__ import annotations

from src.data.native_gameplay import native_record
from src.data.skill_timing import load_skill_timings


def _damage_units(value):
    if isinstance(value, list):
        for child in value:
            yield from _damage_units(child)
    elif isinstance(value, dict):
        if '$type' in value and not value['$value'].get('isEnable', True):
            return
        if value.get('$type', '').endswith('.DamageAction+DamageActionData'):
            yield from value['$value']['damageUnits']
        else:
            for child in value.values():
                yield from _damage_units(child)


def reviewed_base_element(reviewed, *, store=None):
    """Prove the base units' element without treating branch declarations as hits."""
    basis = reviewed.get('native_base_element')
    if basis is None:
        return None
    # This reviewed override is limited to Arclight's two physical slashes.
    if (basis['element'] != '物理' or type(basis['damage_type']) is not int or basis['damage_type'] != 0
            or reviewed['character_id'] != 'arclight' or basis['parameter_key'] != 'atk_scale'):
        raise ValueError('Unreviewed base damage element')
    record = native_record(store or load_skill_timings(), basis['record'])
    if record['source']['sha256'] != basis['record_sha256']:
        raise ValueError('Unreviewed native base damage record')
    frames = []
    for window in record['data']['actionGroupData']['timelineActions']:
        # The 136-frame fallback/other-target attacks are not either base
        # slash. Their shared parameter does not make them unconditional base.
        if window['_startFrame'] not in basis['declaration_frames']:
            continue
        for unit in _damage_units(window['_sequenceActionData']):
            calc = unit.get('atkCalculation')
            if (unit['damageAttributeType'] != 0 or unit['simpleCalculation'] or calc is None
                    or not calc['$type'].endswith('.AtkScaleCalculation')):
                continue
            parameter = calc['$value']['atkScale']
            if parameter['useBlackboardKey'] and parameter['blackboardKey'] == basis['parameter_key']:
                if unit['damageType'] != basis['damage_type']:
                    raise ValueError('Native base damage element differs from reviewed units')
                frames.append(window['_startFrame'])
    if frames != basis['declaration_frames'] or frames != [19, 24, 112, 118]:
        raise ValueError('Unreviewed native base element declarations')
    return basis['element']


def reviewed_row_counts(reviewed, *, store=None):
    rows = reviewed['base_rows']
    counts = reviewed.get('base_row_counts', {row: 1 for row in rows})
    if set(counts) != set(rows) or any(type(v) is not int or v <= 0 for v in counts.values()):
        raise ValueError('Invalid reviewed damage row counts')
    schedule = reviewed.get('native_schedule')
    if schedule is None:
        if any(v != 1 for v in counts.values()):
            raise ValueError('Repeated damage row needs a reviewed native schedule')
        return counts
    if (schedule['row'] not in counts or any(count != 1 for row, count in counts.items() if row != schedule['row'])
            or any(type(frame) is not int or frame < 0 for frame in schedule['frames'])):
        raise ValueError('Unreviewed repeated damage row')
    record = native_record(store or load_skill_timings(), schedule['record'])
    if record['source']['sha256'] != schedule['record_sha256']:
        raise ValueError('Unreviewed native damage schedule record')
    frames = []
    for window in record['data']['actionGroupData']['timelineActions']:
        for node in window['_sequenceActionData']['actionData']:
            body = node['$value']
            if not body.get('isEnable', True) or not node['$type'].endswith('.DamageAction+DamageActionData'):
                continue
            for unit in body['damageUnits']:
                calc = unit.get('atkCalculation')
                if (unit['damageAttributeType'] != 0 or unit['damageType'] != schedule['damage_type']
                        or unit['simpleCalculation'] or calc is None or not calc['$type'].endswith('.AtkScaleCalculation')):
                    continue
                parameter = calc['$value']['atkScale']
                if parameter['useBlackboardKey'] and parameter['blackboardKey'] == schedule['parameter_key']:
                    if window['_startFrame'] != window['_endFrame']:
                        raise ValueError('Interval damage needs execution semantics, not a literal count')
                    frames.append(window['_startFrame'])
    if (frames != schedule['frames'] or len(frames) != counts[schedule['row']]
            or len(set(frames)) != len(frames)):
        raise ValueError('Reviewed damage count differs from native literal frames')
    return counts
