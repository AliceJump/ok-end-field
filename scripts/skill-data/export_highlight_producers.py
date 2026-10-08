"""Export a conservative team producer graph from verified native records.

This describes may-produce edges, not executed hits. Every character-owned
record is a seed, so unselected variants/potentials can only widen possibility.
Missing children/dynamic IDs leave the source boundary open.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from export_release_burst_snapshot import ROOT, _clean_revision
from reviewed_highlight_damage import reviewed_damage

ELEMENT_NAMES = ('Fire', 'Pulse', 'Cryst', 'Natural')


def _walk(value, path=()):
    if isinstance(value, dict):
        if '$type' in value:
            body = value.get('$value', {})
            if not body.get('isEnable', True):
                return
            yield path, value['$type'].rsplit('.', 1)[-1], body
        for key, child in value.items():
            yield from _walk(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, index))


def collect_actor(native_ids, records, tag_identities, *, tag_expansions=None):
    roots = sorted(key for key in records if any(cid in key for cid in native_ids))
    pending = list(roots)
    seen, produces, gaps, evidence, unknown_counts = set(), set(), set(), [], set()
    domain = {tag for row in tag_identities.values() for tag in row.values()}

    def link(name, parent):
        if not name:
            return
        # Native try_NEW_CONSUMED entries require the old attachment. Their
        # status is derived by the team's cross-element rule, not granted by
        # merely encountering a listener that mentions another element.
        if re.fullmatch(r'buff_common_try_(fire|pulse|cryst|natural)_(fire|pulse|cryst|natural)_triggered', name):
            return
        if name not in records:
            gaps.add(f'missing_child:{parent}:{name}')
        elif name not in seen:
            pending.append(name)

    def emit(tags, record, path, kind):
        for tag in sorted(set(tags) & domain):
            produces.add(tag)
            evidence.append({'tag': tag, 'record': record, 'path': list(path), 'kind': kind,
                             'decoded_record_sha256': hashlib.sha256(json.dumps(records[record], sort_keys=True,
                                                                                separators=(',', ':')).encode()).hexdigest(),
                             'native_record_sha256': records[record].get('source', {}).get('sha256')})

    def buff_tags(child):
        result = set()
        for tag in records[child]['data'].get('applyTags', ()):
            if 'raw' in tag:
                raw = tag['raw'].lower()
            elif type(tag.get('tagId')) is int and -(2**31) <= tag['tagId'] < 2**31:
                raw = tag['tagId'].to_bytes(4, 'little', signed=True).hex()
            else:
                gaps.add(f'unknown_buff_tag:{child}')
                continue
            if raw == '00000000':
                continue  # Native INVALID_TAG_ID.
            if tag_expansions is None:
                result.add(raw)
            elif raw in tag_expansions:
                result.update(tag_expansions[raw])
            else:
                gaps.add(f'unknown_buff_tag:{child}:{raw}')
        return result

    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        data = records[key]['data']
        for path, kind, body in _walk(data):
            if kind == 'SpellInfliction+Data':
                element = body.get('inflictionType')
                if type(element) is int and element in range(4):
                    emit((tag_identities[element]['attachment'],), key, path, kind)
                else:
                    gaps.add(f'unknown_infliction:{key}:{element}')
            elif kind == 'ForceSpellStatusAction+Data':
                # Same-version SpellAbnormalType: Fire/Pulse/Cryst/Natural=0..3;
                # Burst=4 does not produce one of these status tags.
                element = body.get('spellStatusType')
                if type(element) is int and element in range(4):
                    emit((tag_identities[element]['status'],), key, path, kind)
                elif element != 4:
                    gaps.add(f'unknown_force_status:{key}:{element}')
            elif kind in ('CreateBuffAction+Data', 'CreateBuffAttachingSkill+Data', 'AuraAction+Data'):
                count = body.get('count', {})
                amount = count.get('value')
                if not count.get('useBlackboardKey') and type(amount) in (int, float) and amount <= 0:
                    continue  # Removal/no-op is not a producer.
                if kind != 'AuraAction+Data' and (count.get('useBlackboardKey') or amount is None):
                    unknown_count = f'{key}:{count.get("blackboardKey", "missing_count")}'
                    unknown_counts.add(unknown_count)
                    gaps.add(f'unknown_count:{unknown_count}')
                for reference in body.get('buffInput' if kind == 'AuraAction+Data' else 'buffs', ()):
                    if reference.get('readIdFromBlackboard'):
                        gaps.add(f'dynamic_buff:{key}:{reference.get("buffIdKey")}')
                        continue
                    child = reference.get('buffId')
                    if child in records:
                        emit(buff_tags(child), key, path, f'{kind}:{child}')
                    link(child, key)
            elif kind == 'CreateGlobalBuffAction+Data':
                # Global buff records use a different native format. A missing
                # interpreter/source remains open, even for a likely SP buff.
                gaps.add(f'unbound_global_buff:{key}')
            elif kind == 'CastSkill+Data':
                reference = body.get('skillId', {})
                if reference.get('useBlackboardKey'):
                    gaps.add(f'dynamic_skill:{key}:{reference.get("blackboardKey")}')
                else:
                    link(reference.get('value'), key)
            elif kind == 'InheritBuffAction+Data':
                # This transfers an existing instance, not a new element. The
                # literal buff's callbacks still belong to the source graph.
                reference = body.get('targetBuffId')
                if isinstance(reference, dict):
                    reference = reference.get('buffId')
                if isinstance(reference, str):
                    link(reference, key)
                else:
                    gaps.add(f'unbound_inherit_buff:{key}')
            elif kind in ('InverseSpellInfliction+Data', 'SkillAffixAction+Data'):
                gaps.add(f'unbound_producer:{key}:{kind}')
            elif kind == 'LaunchProjectile+Data':
                for flag, field in (('castSkillOnHit', 'projectileSkillId'), ('castSkillOnBlock', 'skillIdOnBlock'),
                                    ('castSkillOnFinish', 'skillIdOnFinish'), ('castSkillOnReach', 'skillIdOnReach')):
                    if body.get(flag):
                        link(body.get(field), key)
            elif kind == 'SpawnAbilityEntity+Data':
                link(body.get('abilityEntitySkillId'), key)
            elif kind == 'ChangeSkillAction+Data':
                link(body.get('targetSkillId'), key)
                if body.get('specificRevertedSkillId'):
                    link(body.get('revertedSkillId'), key)
            elif kind == 'IgniteAction+Data' and body.get('igniteType') not in (0, 1, 6, 7):
                gaps.add(f'unbound_ignite:{key}:{body.get("igniteType")}')
    if not roots:
        gaps.add('no_native_roots')
    return {'native_ids': native_ids, 'roots': roots, 'records': len(seen), 'tags': sorted(produces),
            'closed': not gaps, 'gaps': sorted(gaps), 'unknown_counts': sorted(unknown_counts),
            'evidence': sorted(evidence, key=lambda e: (e['record'], str(e['path']), e['tag']))}


def export(source, revision, destination):
    import subprocess

    if re.fullmatch('[0-9a-f]{40}', revision) is None:
        raise ValueError('Source revision must be a full lowercase commit SHA')
    source, destination = Path(source).resolve(), Path(destination).resolve()
    listing = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], cwd=ROOT, text=True)
    registered = {Path(line[9:]).resolve() for line in listing.splitlines() if line.startswith('worktree ')}
    allowed = {ROOT / 'assets/data/highlight_producers', ROOT / 'tmp/highlight-producers-reexport'}
    if source not in registered or destination not in {p.resolve() for p in allowed}:
        raise ValueError('Use a registered source checkout and the named producer output')
    if not destination.is_relative_to(ROOT):
        raise ValueError('Producer output escapes repository')
    _clean_revision(source, revision)
    sys.path.insert(0, str(source))
    from src.data.character_progression import _records
    from src.data.native_gameplay import _supplements, native_record
    from src.data.native_spell_runtime import attachment_policies
    from src.data.native_tags import tag_names

    from src.data.skill_timing import load_skill_timings

    store = load_skill_timings()
    records = {**store._all_records(), **_supplements()}
    names = tag_names()
    identities = {}
    domain = {}
    status_names = ('Burning', 'Conduct', 'Frozen', 'Corrupt')
    for index, (key, _, _, expanded) in attachment_policies().items():
        attachment = next(tag for tag in expanded if names[tag].endswith('/' + ELEMENT_NAMES[index] + 'Inflict'))
        status = next(tag for tag, name in names.items() if name == 'Skill/Character/Common/SpellStatus/' + status_names[index])
        encode = lambda tag: tag.to_bytes(4, 'little', signed=True).hex()
        identities[index] = {'attachment': encode(attachment), 'status': encode(status)}
        for tag in (attachment, status):
            domain[encode(tag)] = names[tag]
        assert native_record(store, key)['data']['applyTags']
    actors = {}
    expansions = {encode(tag): [raw for raw, parent in domain.items()
                               if name == parent or name.startswith(parent + '/')]
                  for tag, name in names.items()}
    for key, progression in _records().items():
        ids = sorted(cid for cid, row in store.index['characters'].items() if row['name'] == progression['name'])
        row = collect_actor(ids, records, identities, tag_expansions=expansions)
        row.update(key=key, baseline=progression['baseline'],
                   coverage='all_owned_native_records_and_literal_children; conservative_all_variants')
        actors[progression['name']] = row
    fixed_rows = json.loads((source / 'assets/data/fixed_damage_baseline.json').read_text(encoding='utf8'))
    payload = {'schema_version': 1, 'scope': 'current_team_skill_source_model', 'tags': domain,
               'elements': [identities[i] for i in range(4)], 'actors': actors,
               'damage_bindings': reviewed_damage(store, fixed_rows)}
    paths = ('assets/data/skill_timings/20261002/index.json', 'assets/data/skill_timings/20261002/records.json.gz',
             'assets/data/character_progression/20261003/index.json', 'assets/data/character_progression/20261003/characters.json',
             'assets/data/character_progression/20261003/supplement.json.gz', 'assets/data/common_mechanics/20261003/index.json',
             'assets/data/common_mechanics/20261003/records.json.gz', 'assets/data/common_mechanics/20261003/assets.json.gz',
             'assets/data/common_mechanics/20261003/enums.json', 'src/data/native_spell_runtime.py', 'src/data/native_tags.py',
             'docs/dev/native-combat-execution-audit.md', 'assets/data/fixed_damage_baseline.json',
             'assets/data/skill_timings/20261002/ranked_blackboards.json.gz',
             'assets/data/character_skills/wulfgard.json')
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    manifest = {'schema_version': 1, 'source_revision': revision, 'actors': len(actors),
                'closed': sum(a['closed'] for a in actors.values()), 'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
                'source_hashes': {path: hashlib.sha256((source / path).read_bytes()).hexdigest() for path in paths},
                'native_inputs': store.index['native_inputs'],
                'exporter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'damage_reviewer_sha256': hashlib.sha256((Path(__file__).parent / 'reviewed_highlight_damage.py').read_bytes()).hexdigest()}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'snapshot.json').write_bytes(raw)
    (destination / 'index.json').write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf8'))
    print(json.dumps({'actors': len(actors), 'closed': manifest['closed'], 'open': {k: a['gaps'] for k, a in actors.items() if not a['closed']}}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', default=str(ROOT / 'assets/data/highlight_producers'))
    arguments = parser.parse_args()
    export(arguments.source, arguments.revision, arguments.output)
