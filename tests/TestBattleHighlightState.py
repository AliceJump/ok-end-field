"""Native predicates remain scoped to the observed button and current frame."""

import json
import unittest
from unittest.mock import Mock, patch

from src.data.battle_conditions import BattleConditionEvaluator, ConditionRequirement, ForecastConditions
from src.data.battle_execution_policy import battle_execution_policy
from src.data.battle_highlight import BattleHighlightState
from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestBattleHighlightState(unittest.TestCase):
    def setUp(self):
        self.store = load_skill_timings()
        self.state = BattleHighlightState(self.store)

    def test_latest_unknown_and_negative_replace_positive_without_deleting_buffs(self):
        profiles = self.store.profiles('莱万汀', 'battle')
        positive = self.state.observe('1', profiles, True, 1)
        self.assertTrue(positive.visible)
        self.assertFalse(hasattr(positive, 'inference'))
        self.assertFalse(hasattr(positive, 'model_inference'))
        self.assertFalse(hasattr(positive, 'satisfied'))
        negative = self.state.observe('1', profiles, False, 2)
        self.assertFalse(negative.visible)
        unknown = self.state.observe('1', profiles, None, 3)
        self.assertIsNone(unknown.visible)
        self.assertEqual(unknown.observed_at, 3)
        self.assertIs(self.state.observations['1'], unknown)

    def test_or_is_preserved_and_does_not_claim_either_branch(self):
        observation = self.state.observe('1', self.store.profiles('庄方宜', 'battle'), True, 0)
        condition = json.loads(observation.condition_json)
        branch = condition['actionData'][0]
        self.assertIn('OrConditionAction', branch['$type'])
        self.assertEqual(len(branch['$value']['conditionList']), 2)
        condition['actionData'].clear()
        self.assertTrue(json.loads(observation.condition_json)['actionData'])

    def test_non_boolean_input_does_not_become_a_positive_fact(self):
        profiles = self.store.profiles('弧光', 'battle')
        for value in ('false', 1, 0, object()):
            with self.subTest(value=value):
                observation = self.state.observe('1', profiles, value, 0)
                self.assertIsNone(observation.visible)
                self.assertFalse(hasattr(observation, 'inference'))

    def test_empty_and_unbound_phase_clear_old_observation(self):
        self.state.observe('1', self.store.profiles('莱万汀', 'battle'), True, 0)
        self.assertIsNone(self.state.observe('1', self.store.profiles('佩丽卡', 'battle'), False, 1))
        self.assertNotIn('1', self.state.observations)
        phases = self.store.battle_phase_profiles('弭弗')
        self.assertIsNone(self.state.condition(phases))
        self.assertIsNone(self.state.observe('1', phases[:1], True, 2))
        self.assertTrue(self.state.observe('1', phases[-1:], True, 3).visible)

    def test_step_overwrites_known_predicates_and_clears_unavailable_slots(self):
        task = FakeTask()
        task.sp = 0
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['莱万汀', '弧光', '庄方宜', '佩丽卡'], reset_runtime=True)
        task.battle_pulses = {'1', '2'}
        logic.step()
        self.assertTrue(logic.battle_highlights.observations['1'].visible)
        self.assertTrue(logic.battle_highlights.observations['2'].visible)
        self.assertFalse(logic.battle_highlights.observations['3'].visible)
        self.assertNotIn('4', logic.battle_highlights.observations)
        task.battle_pulses.clear()
        logic.step()
        self.assertFalse(logic.battle_highlights.observations['1'].visible)
        logic.disabled_slots.add('2')
        logic.step()
        self.assertNotIn('2', logic.battle_highlights.observations)
        logic._configure_team(['秋栗', '弧光', '庄方宜', '佩丽卡'])
        self.assertFalse(logic.battle_highlights.observations)

    def test_zhuang_execution_uses_fresh_observation_over_prediction(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['庄方宜', '?', '?', '?'], reset_runtime=True)
        self.assertFalse(logic._try_battle_token('1', 100))
        self.assertFalse(logic.battle_highlights.observations['1'].visible)
        task.battle_pulses.add('1')
        self.assertTrue(logic._try_battle_token('1', 100))
        self.assertTrue(logic.battle_highlights.observations['1'].visible)
        task.battle_pulses.clear()
        logic.step()
        self.assertFalse(logic.battle_highlights.observations['1'].visible)
        self.assertIsNone(logic.pending)

    def test_ordinary_battle_without_ring_keeps_existing_release_contract(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['狼卫', '?', '?', '?'], reset_runtime=True)
        logic._observe_battle_highlights()
        self.assertFalse(logic.battle_highlights.observations['1'].visible)
        self.assertTrue(logic._try_battle_token('1', 100))
        self.assertEqual(task.keys, ['1'])

    def test_visual_sampling_does_not_run_condition_inference_or_change_execution(self):
        with patch('src.data.battle_conditions.infer_highlight', side_effect=AssertionError('not a sensor')):
            sample = self.state.observe('1', self.store.profiles('弧光', 'battle'), True, 1)
        self.assertTrue(sample.visible)
        self.assertIsNone(ConditionRequirement.from_assessment('1', sample))
        self.assertFalse(ForecastConditions.from_assessments({'1': sample}).observations)
        assessment = BattleConditionEvaluator(['弧光']).assess(sample)
        self.assertTrue(assessment.satisfied)
        self.assertTrue(assessment.inference.confirms('tag', 'bf9d6e57'))
        self.assertTrue(sample.visible)
        self.assertFalse(hasattr(sample, 'inference'))

    def test_step_consumes_one_existing_batch_monitor_without_per_slot_rescans(self):
        task = FakeTask()
        task.sp = 0
        task.observe_battle_skill_highlights = Mock(return_value={'1': True, '2': None, '3': False})
        task.is_battle_skill_pulsing = Mock(side_effect=AssertionError('duplicate scan'))
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['狼卫', '弧光', '莱万汀', '佩丽卡'], reset_runtime=True)
        logic.step()
        task.observe_battle_skill_highlights.assert_called_once_with()
        task.is_battle_skill_pulsing.assert_not_called()
        self.assertTrue(logic.battle_highlights.observations['1'].visible)
        self.assertIsNone(logic.battle_highlights.observations['2'].visible)
        self.assertFalse(logic.battle_highlights.observations['3'].visible)
        self.assertFalse(task.keys)
        task.observe_battle_skill_highlights.return_value = {}
        logic._observe_battle_highlights()
        self.assertIsNone(logic.battle_highlights.observations['1'].visible)
        self.assertFalse(logic._scene_conditions().facts())
        task.observe_battle_skill_highlights.return_value = None
        logic._observe_battle_highlights()
        task.is_battle_skill_pulsing.assert_not_called()
        self.assertIsNone(logic.battle_highlights.observations['1'].visible)

    def test_execution_exception_is_explicit_and_separate_from_scene_assessment(self):
        ordinary = battle_execution_policy('wolf_guard')
        zhuang = battle_execution_policy('zhuang_fangyi')
        for visible in (True, False, None):
            self.assertTrue(ordinary.can_attempt(visible))
            self.assertFalse(ordinary.confirms_release(visible))
        self.assertTrue(zhuang.can_attempt(True))
        self.assertFalse(zhuang.can_attempt(False))
        self.assertFalse(zhuang.can_attempt(None))
        self.assertTrue(zhuang.confirms_release(False))
        self.assertFalse(zhuang.confirms_release(None))

    def test_ultimate_recovery_discards_old_ring_before_overflow_without_another_batch(self):
        task = FakeTask()
        task.sp = 300
        task.ults = {'1'}
        task.observe_battle_skill_highlights = Mock(return_value={'2': True})
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['佩丽卡', '弧光'], reset_runtime=True)
        scores = {}

        def overflow(_sp, checkpoint):
            scores[checkpoint] = logic._overflow_score('2')
            return False

        # Exercise the non-burst ultimate path and its immediate overflow check.
        with patch.object(logic, '_release_burst_decision', return_value=None), \
                patch.object(logic, '_try_overflow_battle_skill', side_effect=overflow):
            logic.step()
        self.assertEqual(task.keys, ['ult_1'])
        task.observe_battle_skill_highlights.assert_called_once_with()
        self.assertGreater(scores['主循环'], scores['终结技恢复'])
        self.assertIsNone(logic._battle_condition('2'))
        self.assertFalse(logic._scene_conditions().facts())
        # Clearing is unknown, not a negative condition; the next ordinary
        # batch may establish the same ring afresh.
        logic._observe_battle_highlights()
        self.assertTrue(logic._battle_condition('2').satisfied)


if __name__ == '__main__':
    unittest.main()
