"""Finite native attack schedules must not absorb conditional or poise rows."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from src.data.native_gameplay import native_record
from src.data.reviewed_damage_rows import reviewed_row_counts
from src.data.skill_timing import load_skill_timings

ROOT = Path(__file__).resolve().parents[1]


class TestReviewedDamageRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skills = json.loads((ROOT / 'assets/data/skill_damage_row_semantics.json').read_text(encoding='utf-8'))['skills']

    def test_laevatain_count_is_ten_literal_native_opportunities(self):
        reviewed = self.skills['laevatain_skill']
        self.assertEqual(reviewed_row_counts(reviewed), {'初始爆炸伤害倍率': 1, '持续伤害每段倍率': 10})
        self.assertNotIn('追加伤害倍率', reviewed['base_rows'])
        self.assertEqual(reviewed['native_schedule']['frames'], [25, 29, 33, 37, 41, 45, 50, 54, 58, 62])
        self.assertTrue(any(c['role'] == 'replacement' for c in reviewed['components']))

    def test_yvonne_conditional_and_per_layer_damage_do_not_become_base(self):
        reviewed = self.skills['yvonne_skill']
        self.assertEqual(reviewed_row_counts(reviewed), {'基础伤害倍率': 1})
        self.assertEqual([c['role'] for c in reviewed['components']], ['conditional_followup', 'per_consumed_layer'])

    def test_modified_record_and_unproven_repetition_are_rejected(self):
        reviewed = deepcopy(self.skills['laevatain_skill'])
        reviewed['native_schedule']['record_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Unreviewed native damage schedule record'):
            reviewed_row_counts(reviewed)
        del reviewed['native_schedule']
        with self.assertRaisesRegex(ValueError, 'needs a reviewed native schedule'):
            reviewed_row_counts(reviewed)
        reviewed['base_row_counts']['持续伤害每段倍率'] = True
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed damage row counts'):
            reviewed_row_counts(reviewed)

    def test_interval_disabled_and_poise_damage_cannot_stand_in_for_pulse_count(self):
        reviewed = self.skills['laevatain_skill']
        original = native_record(load_skill_timings(), reviewed['native_schedule']['record'])
        for mode in ('interval', 'disabled', 'poise'):
            record = deepcopy(original)
            window = next(w for w in record['data']['actionGroupData']['timelineActions'] if w['_startFrame'] == 25)
            node = next(n for n in window['_sequenceActionData']['actionData']
                        if n['$type'].endswith('.DamageAction+DamageActionData'))
            if mode == 'interval':
                window['_endFrame'] += 1
            elif mode == 'disabled':
                node['$value']['isEnable'] = False
            else:
                for unit in node['$value']['damageUnits']:
                    unit['damageAttributeType'] = 1
            with self.subTest(mode=mode), patch('src.data.reviewed_damage_rows.native_record', return_value=record):
                with self.assertRaises(ValueError):
                    reviewed_row_counts(reviewed)

    def test_fixed_quote_uses_reviewed_rows_without_changing_fixed_panels(self):
        rows = json.loads((ROOT / 'assets/data/fixed_damage_baseline.json').read_text(encoding='utf-8'))
        expected = {'laevatain': 2.8, 'yvonne': 2.5}
        for row in rows:
            if row['key'] in expected:
                skill = next(s for s in row['skills'] if s['type'] == '战技')
                self.assertEqual(skill['quote_basis']['multiplier'], expected[row['key']])
                self.assertEqual(skill['row_semantics']['base_rows'], self.skills[skill['skill_id']]['base_rows'])


if __name__ == '__main__':
    unittest.main()
