"""Mixed skills keep the reviewed base element and conditional damage separate."""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_quote_data import read_fixed_quote
from src.data.damage_resolution import TimedDamageState
from src.data.native_gameplay import native_record
from src.data.reviewed_damage_rows import _damage_units, reviewed_base_element
from src.data.skill_timing import load_skill_timings

ROOT = Path(__file__).resolve().parents[1]


class TestReviewedDamageElements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reviewed = json.loads((ROOT / 'assets/data/skill_damage_row_semantics.json').read_text(encoding='utf-8'))['skills']['arclight_skill']
        cls.record = native_record(load_skill_timings(), cls.reviewed['native_base_element']['record'])
        cls.row = next(row for row in json.loads((ROOT / 'assets/data/fixed_damage_baseline.json').read_text(encoding='utf-8')) if row['key'] == 'arclight')
        cls.skill = next(skill for skill in cls.row['skills'] if skill['type'] == '战技')

    def test_two_slashes_replay_as_physical_without_conditional_electric_row(self):
        self.assertEqual(reviewed_base_element(self.reviewed), '物理')
        quote = read_fixed_quote(self.row, self.skill)
        self.assertEqual(quote.multiplier, 2.02)
        self.assertEqual(quote.element, '物理')
        self.assertEqual(self.skill['bonus_pct'], 29.9)
        self.assertEqual(self.row['element'], '电磁')  # Character/overall mixed skill stays electromagnetic.
        self.assertEqual(self.skill['row_semantics']['components'][0]['rank_values'], {'追加伤害倍率': '405%'})

    def test_electric_amplification_does_not_buff_the_physical_base_quote(self):
        quote = read_fixed_quote(self.row, self.skill)
        state = TimedDamageState(('support', 'carry'))
        base = quote.resolve(state, actor='carry', enemy='target', now=0)
        for element, expected_ratio in (('电磁', 1), ('物理', 1.22)):
            spec = DamageModifierSpec('amp_' + element, DamageBucket.AMPLIFICATION, (element,), 'team',
                                      ModifierMagnitude(.22), 'confirmed', duration=5)
            state.apply(spec, source_actor='support', now=0, event='confirmed')
            after = quote.resolve(state, actor='carry', enemy='target', now=1)
            self.assertAlmostEqual(after.expected / base.expected, expected_ratio)

    def test_changed_source_or_one_wrong_base_unit_is_rejected(self):
        reviewed = deepcopy(self.reviewed)
        reviewed['native_base_element']['record_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Unreviewed native base damage record'):
            reviewed_base_element(reviewed)
        record = deepcopy(self.record)
        unit = next(unit for window in record['data']['actionGroupData']['timelineActions']
                    if window['_startFrame'] == 19
                    for unit in _damage_units(window['_sequenceActionData'])
                    if unit['damageAttributeType'] == 0)
        unit['damageType'] = 3
        with patch('src.data.reviewed_damage_rows.native_record', return_value=record):
            with self.assertRaisesRegex(ValueError, 'differs from reviewed units'):
                reviewed_base_element(self.reviewed)

    def test_followup_frames_cannot_be_relabelled_as_unconditional_base(self):
        reviewed = deepcopy(self.reviewed)
        reviewed['native_base_element']['declaration_frames'].append(136)
        with self.assertRaisesRegex(ValueError, 'Unreviewed native base element declarations'):
            reviewed_base_element(reviewed)
        reviewed = deepcopy(self.reviewed)
        reviewed['native_base_element'].update(element='电磁', damage_type=3)
        with self.assertRaisesRegex(ValueError, 'Unreviewed base damage element'):
            reviewed_base_element(reviewed)


if __name__ == '__main__':
    unittest.main()
