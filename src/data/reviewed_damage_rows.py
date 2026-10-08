"""Validate reviewed rank-row counts against finite native damage schedules.

Frames are attack opportunities. They do not establish hits, target persistence,
entity blackboard propagation or the execution of a complete native program.
"""

from __future__ import annotations

from src.data.native_gameplay import native_record
from src.data.skill_timing import load_skill_timings


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
