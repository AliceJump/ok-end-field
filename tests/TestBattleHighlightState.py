"""Native predicates remain scoped to the observed button and current frame."""

import unittest

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
        self.assertTrue(positive.ready)
        self.assertIsNotNone(positive.confirmed_condition)
        negative = self.state.observe('1', profiles, False, 2)
        self.assertFalse(negative.ready)
        self.assertIsNone(negative.confirmed_condition)
        unknown = self.state.observe('1', profiles, None, 3)
        self.assertIsNone(unknown.ready)
        self.assertEqual(unknown.observed_at, 3)
        self.assertIs(self.state.observations['1'], unknown)

    def test_or_is_preserved_and_does_not_claim_either_branch(self):
        observation = self.state.observe('1', self.store.profiles('庄方宜', 'battle'), True, 0)
        condition = observation.confirmed_condition
        branch = condition['actionData'][0]
        self.assertIn('OrConditionAction', branch['$type'])
        self.assertEqual(len(branch['$value']['conditionList']), 2)
        condition['actionData'].clear()
        self.assertTrue(observation.confirmed_condition['actionData'])

    def test_non_boolean_input_does_not_become_a_positive_fact(self):
        profiles = self.store.profiles('弧光', 'battle')
        for value in ('false', 1, 0, object()):
            with self.subTest(value=value):
                observation = self.state.observe('1', profiles, value, 0)
                self.assertIsNone(observation.ready)
                self.assertIsNone(observation.confirmed_condition)

    def test_empty_and_unbound_phase_clear_old_observation(self):
        self.state.observe('1', self.store.profiles('莱万汀', 'battle'), True, 0)
        self.assertIsNone(self.state.observe('1', self.store.profiles('佩丽卡', 'battle'), False, 1))
        self.assertNotIn('1', self.state.observations)
        phases = self.store.battle_phase_profiles('弭弗')
        self.assertIsNone(self.state.condition(phases))
        self.assertIsNone(self.state.observe('1', phases[:1], True, 2))
        self.assertTrue(self.state.observe('1', phases[-1:], True, 3).ready)

    def test_step_overwrites_known_predicates_and_clears_unavailable_slots(self):
        task = FakeTask()
        task.sp = 0
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['莱万汀', '弧光', '庄方宜', '佩丽卡'], reset_runtime=True)
        task.battle_pulses = {'1', '2'}
        logic.step()
        self.assertTrue(logic.battle_highlights.observations['1'].ready)
        self.assertTrue(logic.battle_highlights.observations['2'].ready)
        self.assertFalse(logic.battle_highlights.observations['3'].ready)
        self.assertNotIn('4', logic.battle_highlights.observations)
        task.battle_pulses.clear()
        logic.step()
        self.assertFalse(logic.battle_highlights.observations['1'].ready)
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
        self.assertFalse(logic.battle_highlights.observations['1'].ready)
        task.battle_pulses.add('1')
        self.assertTrue(logic._try_battle_token('1', 100))
        self.assertTrue(logic.battle_highlights.observations['1'].ready)
        task.battle_pulses.clear()
        logic.step()
        self.assertFalse(logic.battle_highlights.observations['1'].ready)
        self.assertIsNone(logic.pending)

    def test_ordinary_battle_without_ring_keeps_existing_release_contract(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['狼卫', '?', '?', '?'], reset_runtime=True)
        logic._observe_battle_highlights()
        self.assertFalse(logic.battle_highlights.observations['1'].ready)
        self.assertTrue(logic._try_battle_token('1', 100))
        self.assertEqual(task.keys, ['1'])


if __name__ == '__main__':
    unittest.main()
