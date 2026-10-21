"""Scene evidence changes later burst actions without inventing enemy identity."""

import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from src.data.battle_conditions import BattleConditionEvaluator, ConditionRequirement, ForecastConditions
from src.data.battle_highlight import BattleHighlightState
from src.data.highlight_conditions import HighlightAtom, HighlightExpression, infer_highlight, parse_highlight
from src.data.highlight_producers import read_producers
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestBattleConditionForecast(unittest.TestCase):
    def setUp(self):
        self.store = load_skill_timings()
        self.team = ['安塔尔', '弧光', '狼卫', '莱万汀']
        self.sensor = BattleHighlightState(self.store)
        self.state = BattleConditionEvaluator(self.team)
        self.planner = ReleaseBurstPlanner(self.team, store=self.store)

    def observe(self, slot, character, ready=True):
        return self.state.assess(self.sensor.observe(slot, self.store.profiles(character, 'battle'), ready, 0))

    def forecast(self):
        return self.state.forecast(self.sensor.observations)

    def arclight(self, observation):
        binding = self.planner.highlight_damage['2']
        return ReleaseBurstAction('2', 'battle', .5, binding.base_value, 100, 100,
                                  observed_bonus=binding.conditional_value,
                                  base_components=binding.base_components,
                                  observed_components=binding.conditional_components,
                                  condition=ConditionRequirement.from_assessment('2', observation))

    def test_enemy_fact_and_or_group_survive_reviewed_ally_buff_cast(self):
        arc, wolf = self.observe('2', '弧光'), self.observe('3', '狼卫')
        self.observe('4', '莱万汀')
        initial = self.forecast()
        after = initial.after('1', 'ult', self.planner.condition_transitions['1', 'ult'])
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('2', arc)))
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('3', wolf)))
        self.assertEqual({f.slot for f in after.facts()}, {'2'})
        self.assertTrue(any(f.atom.identity == 'bf9d6e57' and f.truth for f in after.facts()))
        self.assertFalse(any(slot == '4' for slot, _ in after.observations))
        self.assertEqual(after.projected_actions, (('1', 'ult'),))
        self.assertEqual(len(initial.observations), 3)  # Search branches do not mutate actual observations.

    def test_or_group_is_not_promoted_to_an_arbitrary_scene_fact(self):
        wolf = self.observe('3', '狼卫')
        self.assertFalse(any(atom.kind == 'tag' for atom, truth in wolf.inference.facts))
        after = self.forecast().after('1', 'ult', self.planner.condition_transitions['1', 'ult'])
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('3', wolf)))
        self.assertFalse(after.facts())

    def test_three_arm_or_is_preserved_without_claiming_all_statuses(self):
        wolf = self.observe('3', '狼卫')
        raw = json.loads(wolf.condition_json)
        branch = next(n['$value']['query'] for n in raw['actionData'] if 'query' in n['$value'])
        frozen = next(tag for tag, name in read_producers()['tags'].items() if name.endswith('/Frozen'))
        branch['tags'].append({'raw': frozen})
        condition = json.dumps(raw, sort_keys=True, separators=(',', ':'))
        obs = replace(wolf, condition_json=condition, inference=infer_highlight(parse_highlight(condition), True))
        initial = ForecastConditions.from_assessments({'3': obs})
        after = initial.after('1', 'ult', self.planner.condition_transitions['1', 'ult'])
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('3', obs)))
        self.assertFalse(after.facts())

    def test_different_buttons_do_not_share_recommended_enemy_identity(self):
        observation = self.observe('2', '弧光')
        conditions = self.forecast()
        request = ConditionRequirement.from_assessment('2', observation)
        self.assertTrue(conditions.supports(request))
        self.assertFalse(conditions.supports(replace(request, slot='3')))
        self.assertFalse(conditions.supports(replace(request, skill_ids=('another_button',))))
        self.assertTrue(all(f.slot == '2' and f.atom.recommended_target for f in conditions.facts()))

    def test_unknown_or_damage_transition_invalidates_evidence_without_negative_facts(self):
        observation = self.observe('2', '弧光')
        initial = self.forecast()
        after = initial.after('3', 'battle', self.planner.condition_transitions.get(('3', 'battle')))
        self.assertFalse(after.supports(ConditionRequirement.from_assessment('2', observation)))
        self.assertFalse(after.facts())
        self.assertTrue(initial.facts())

    def test_readiness_gate_is_not_an_enemy_fact_or_a_substitute_for_an_or_arm(self):
        gate = HighlightExpression('atom', atom=HighlightAtom('availability', 'native_readiness'))
        tag = HighlightExpression('atom', atom=HighlightAtom('tag', 'bf9d6e57',
                                                          '{"targetGroupKey":"highlight_smart_target","targetSource":2}'))
        transition = self.planner.condition_transitions['1', 'ult']
        self.assertTrue(transition.preserves(HighlightExpression('and', (gate, tag))))
        self.assertFalse(transition.preserves(HighlightExpression('or', (gate, tag))))
        self.assertFalse(transition.preserves(HighlightExpression('and', (gate,))))

    def test_latest_false_unknown_and_changed_team_replace_projected_conditions(self):
        observation = self.observe('2', '弧光')
        request = ConditionRequirement.from_assessment('2', observation)
        for value in (False, None):
            self.observe('2', '弧光', value)
            self.assertFalse(self.forecast().supports(request))
            self.assertFalse(self.forecast().facts())
        task = FakeTask()
        task.sp = 0
        task.battle_pulses.add('2')
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(self.team, reset_runtime=True)
        logic._observe_battle_highlights()
        self.assertTrue(logic._scene_conditions().facts())
        logic._configure_team(['安塔尔', '佩丽卡', '狼卫', '莱万汀'])
        self.assertFalse(logic._scene_conditions().observations)

    def test_support_then_enhanced_arclight_wins_with_identical_actions_sp_and_times(self):
        observation = self.observe('2', '弧光')
        attack = self.arclight(observation)
        opener = ReleaseBurstAction('1', 'ult', .5, 0)
        unscoped = self.planner.choose((opener, replace(attack, condition=None)), 100, 0)
        scene = self.planner.choose((opener, attack), 100, 0, conditions=self.forecast())
        self.assertIsNone(unscoped)  # Old projection loses the enhancement after the zero-damage support.
        self.assertEqual(scene.sequence, (('1', 'ult'), ('2', 'battle')))
        self.assertEqual(scene.action.sp_cost, 0)
        binding = self.planner.highlight_damage['2']
        amp = 1 + next(s['magnitude']['base'] for s in self.planner.rules['1', 'ult'] if '电磁' in s['elements'])
        self.assertAlmostEqual(scene.damage, binding.base_value + binding.conditional_value * amp)
        self.assertFalse(self.planner.bonuses)
        self.assertFalse(self.planner.stances)

    def test_forecast_consumer_cannot_reuse_another_old_enemy_condition(self):
        arc = self.observe('2', '弧光')
        wolf = self.observe('3', '狼卫')
        initial = (0.0, 0.0, 200.0, (), [], {}, {}, self.forecast(), 0.0, self.planner.mechanism_state, self.planner.normal_state)
        used = self.planner._forecast_step(initial, self.arclight(arc), 0, 0, releases=False, horizon=6,
                                           predecessors={0: ()})
        self.assertFalse(used[7].supports(ConditionRequirement.from_assessment('3', wolf)))
        binding = self.planner.highlight_damage['3']
        action = ReleaseBurstAction('3', 'battle', .5, binding.base_value, 100, 100,
                                    observed_bonus=binding.conditional_value,
                                    condition=ConditionRequirement.from_assessment('3', wolf))
        later = self.planner._forecast_step(used, action, 1, 0, releases=False, horizon=6, predecessors={1: ()})
        self.assertAlmostEqual(later[0] - used[0], binding.base_value)

    def test_sp_shortage_still_prevents_support_only_prelude(self):
        attack = self.arclight(self.observe('2', '弧光'))
        self.assertIsNone(self.planner.choose((ReleaseBurstAction('1', 'ult', .5, 0), attack), 99, 0,
                                             conditions=self.forecast()))

    def test_reviewed_action_hash_must_match_live_store(self):
        dependencies = read_producers()['condition_transitions']['antal'][0]['record_hashes']
        for changed in dependencies:
            with self.subTest(changed=changed):
                store = Mock(wraps=self.store)
                store.record.side_effect = lambda key: {'source': {'sha256': '0' * 64}} if key == changed else self.store.record(key)
                with self.assertRaisesRegex(ValueError, 'Unreviewed live condition transition record: ' + changed):
                    ReleaseBurstPlanner(['安塔尔'], store=store)

    def test_missing_child_record_cannot_keep_condition_preservation(self):
        dependencies = read_producers()['condition_transitions']['antal'][0]['record_hashes']
        for missing in dependencies:
            with self.subTest(missing=missing):
                store = Mock(wraps=self.store)

                def read(key):
                    if key == missing:
                        raise KeyError(key)
                    return self.store.record(key)

                store.record.side_effect = read
                with self.assertRaisesRegex(ValueError, 'Missing live condition transition record: ' + missing):
                    ReleaseBurstPlanner(['安塔尔'], store=store)

    def test_real_step_selects_support_before_current_enhanced_output(self):
        task = FakeTask()
        task.sp = 100
        task.ults.add('1')
        task.battle_pulses.add('2')
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['安塔尔', '弧光', '?', '?'], reset_runtime=True)
        logic._observe_battle_highlights()
        decision = logic._release_burst_decision(logic._ready_ultimate_actions(), 100, 0)
        self.assertEqual(decision.sequence, (('1', 'ult'), ('2', 'battle')))
        logic.step()
        self.assertIn('ult_1', task.keys)
        self.assertNotIn('2', task.keys)
        task.battle_pulses.clear()
        logic._observe_battle_highlights()
        ordinary = logic._release_battle_candidate('2', 100, task.now, set())
        self.assertEqual(ordinary.observed_bonus, 0)

    def test_xaihi_preserves_enemy_group_but_not_owner_resources(self):
        arc, wolf = self.observe('2', '弧光'), self.observe('3', '狼卫')
        self.observe('4', '莱万汀')
        planner = ReleaseBurstPlanner(['赛希', '弧光', '狼卫', '莱万汀'], store=self.store)
        after = self.forecast().after('1', 'ult', planner.condition_transitions['1', 'ult'])
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('2', arc)))
        self.assertTrue(after.supports(ConditionRequirement.from_assessment('3', wolf)))
        self.assertFalse(any(slot == '4' for slot, _ in after.observations))
        self.assertEqual({f.slot for f in after.facts()}, {'2'})

    def test_xaihi_each_native_dependency_must_exist_and_match(self):
        dependencies = read_producers()['condition_transitions']['xaihi'][0]['record_hashes']
        for affected in dependencies:
            for missing in (False, True):
                with self.subTest(record=affected, missing=missing):
                    store = Mock(wraps=self.store)

                    def read(key):
                        if key != affected:
                            return self.store.record(key)
                        if missing:
                            raise KeyError(key)
                        return {'source': {'sha256': '0' * 64}}

                    store.record.side_effect = read
                    with self.assertRaisesRegex(ValueError, 'live condition transition record: ' + affected):
                        ReleaseBurstPlanner(['赛希'], store=store)

    def test_xaihi_cold_output_prelude_keeps_later_electric_component(self):
        planner = ReleaseBurstPlanner(['赛希', '弧光', '狼卫', '伊冯'], store=self.store)
        attack = self.arclight(self.observe('2', '弧光'))
        normal = ReleaseBurstAction('4', 'normal', planner.normal_durations['4'],
                                    planner.rows['4']['quotes']['normal']['crit_expect'])
        actions = (ReleaseBurstAction('1', 'ult', 2, 0), attack, normal)
        conditions = self.forecast()
        decision = planner.choose(actions, 100, 0, conditions=conditions)
        self.assertEqual(decision.sequence, (('1', 'ult'), ('2', 'battle'), ('4', 'normal')))
        cold_amp = next(spec['magnitude']['base'] for spec in planner.rules['1', 'ult'] if '寒冷' in spec['elements'])
        self.assertAlmostEqual(decision.damage, attack.value + attack.observed_bonus + normal.value * (1 + cold_amp))
        # Removing just the reviewed transition changes the real candidate ordering.
        del planner.condition_transitions['1', 'ult']
        old = planner.choose(actions, 100, 0, conditions=conditions)
        self.assertEqual(old.sequence, (('2', 'battle'), ('1', 'ult'), ('4', 'normal')))

    def test_xaihi_real_step_uses_existing_main_control_and_white_condition(self):
        task = FakeTask()
        task.sp = 100
        task.ults = {'1'}
        task.battle_pulses = {'2'}
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['赛希', '弧光', '狼卫', '伊冯'], reset_runtime=True)
        with patch.object(task, 'detect_current_char_index', return_value=3, create=True):
            logic.step()
            self.assertEqual(task.keys, ['ult_1'])
            # Forecast preservation selected the support first, but it is not
            # permission to reuse a pre-animation observation in the live scene.
            self.assertFalse(logic._scene_conditions().facts())
            task.battle_pulses.clear()
            logic.step()
        self.assertFalse(logic._battle_condition('2').satisfied)
        self.assertFalse(logic._scene_conditions().facts())


if __name__ == '__main__':
    unittest.main()
