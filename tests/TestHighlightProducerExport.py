"""Producer extraction never treats consumers and missing graphs as proof."""

import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/skill-data/export_highlight_producers.py'
sys.path.insert(0, str(SCRIPT.parent))
try:
    spec = importlib.util.spec_from_file_location('highlight_producer_export_test', SCRIPT)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
finally:
    sys.path.remove(str(SCRIPT.parent))


def action(kind, **body):
    return {'$type': 'Beyond.Gameplay.Core.' + kind, '$value': {'isEnable': True, **body}}


def record(*nodes, tags=()):
    return {'data': {'actionData': list(nodes), 'applyTags': [{'raw': tag} for tag in tags]}}


ELEMENTS = {i: {'attachment': f'attachment{i}', 'status': f'status{i}'} for i in range(4)}


class TestHighlightProducerExport(unittest.TestCase):
    def test_disabled_paths_and_negative_buff_counts_are_not_producers(self):
        records = {
            'chr_test_battle': record(
                action('SpellInfliction+Data', inflictionType=0, isEnable=False),
                action('IfElseAction+IfElseActionData', isEnable=False,
                       child=action('SpellInfliction+Data', inflictionType=1)),
                action('CreateBuffAction+Data', count={'value': -1, 'useBlackboardKey': False},
                       buffs=[{'buffId': 'buff_common_consumed'}]),
                action('CheckBuffStackNum+Data', buffId={'buffId': 'buff_common_consumed'})),
            'buff_common_consumed': record(tags=['status0']),
        }
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS)
        self.assertTrue(row['closed'])
        self.assertFalse(row['tags'])

    def test_projectile_child_and_forced_status_are_positive_evidence(self):
        records = {
            'chr_test_battle': record(action('LaunchProjectile+Data', castSkillOnHit=True,
                                             projectileSkillId='common_child')),
            'common_child': record(action('SpellInfliction+Data', inflictionType=2),
                                   action('ForceSpellStatusAction+Data', spellStatusType=1)),
        }
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS)
        self.assertTrue(row['closed'])
        self.assertEqual(set(row['tags']), {'attachment2', 'status1'})
        self.assertTrue(all(item['decoded_record_sha256'] for item in row['evidence']))

    def test_disabled_child_callback_does_not_require_missing_record(self):
        records = {'chr_test_battle': record(action('LaunchProjectile+Data', castSkillOnHit=False,
                                                     projectileSkillId='missing_child'))}
        self.assertTrue(exporter.collect_actor(['chr_test'], records, ELEMENTS)['closed'])

    def test_missing_and_dynamic_child_keep_source_boundary_open(self):
        records = {'chr_test_battle': record(
            action('SpawnAbilityEntity+Data', abilityEntitySkillId='missing'),
            action('CreateBuffAction+Data', count={'value': 1},
                   buffs=[{'readIdFromBlackboard': True, 'buffIdKey': 'dynamic_id'}]))}
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS)
        self.assertFalse(row['closed'])
        self.assertEqual(len(row['gaps']), 2)

    def test_unknown_creation_count_keeps_boundary_open_without_defaulting_to_one(self):
        records = {'chr_test_battle': record(
            action('CreateBuffAction+Data', count={'useBlackboardKey': True, 'blackboardKey': 'count'},
                   buffs=[{'buffId': 'known_buff'}])),
            'known_buff': record(tags=['status0'])}
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS)
        self.assertFalse(row['closed'])
        self.assertTrue(row['unknown_counts'])
        self.assertEqual(row['tags'], ['status0'])  # Only a may-produce edge.

    def test_native_buff_tag_children_supply_parent_and_unknown_tags_stay_open(self):
        records = {'chr_test_battle': record(
            action('CreateBuffAction+Data', count={'value': 1}, buffs=[{'buffId': 'known_buff'}])),
            'known_buff': record(tags=['04000000'])}
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS,
                                     tag_expansions={'04000000': ['status0']})
        self.assertTrue(row['closed'])
        self.assertEqual(row['tags'], ['status0'])
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS, tag_expansions={})
        self.assertFalse(row['closed'])
        self.assertFalse(row['tags'])

    def test_reaction_listener_requires_other_element_before_status_is_added(self):
        # The native try_NEW_CONSUMED entry is accounted by team reactions.
        # Merely mentioning that old element in a listener cannot supply it.
        records = {
            'chr_test_battle': record(action('SpellInfliction+Data', inflictionType=0),
                                      action('CreateBuffAction+Data', count={'value': 1},
                                             buffs=[{'buffId': 'buff_common_try_fire_pulse_triggered'}])),
            'buff_common_try_fire_pulse_triggered': record(
                action('CreateBuffAction+Data', count={'value': 1}, buffs=[{'buffId': 'fire_status'}])),
            'fire_status': record(tags=['status0']),
        }
        row = exporter.collect_actor(['chr_test'], records, ELEMENTS)
        self.assertTrue(row['closed'])
        self.assertEqual(row['tags'], ['attachment0'])


if __name__ == '__main__':
    unittest.main()
