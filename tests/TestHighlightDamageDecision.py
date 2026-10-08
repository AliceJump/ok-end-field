"""A highlight changes damage valuation, never ordinary skill eligibility."""

import unittest

from src.data.battle_highlight import BattleHighlightState
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestHighlightDamageDecision(unittest.TestCase):
    def setUp(self):
        self.store = load_skill_timings()

    def test_native_projectile_damage_uses_baseline_rank_and_current_condition(self):
        planner = ReleaseBurstPlanner(['狼卫'], store=self.store)
        state = BattleHighlightState(self.store, ['狼卫'])
        profiles = self.store.profiles('狼卫', 'battle')
        quote = planner.quote('1', 'battle')
        unit = quote['crit_expect'] / quote['quote_basis']['multiplier']
        base, extra = planner.battle_values('1', state.observe('1', profiles, True, 0))
        self.assertAlmostEqual(base / unit, 3 * 0.7699999809265137)
        self.assertAlmostEqual(extra / unit, 8.5)
        for observed in (False, None):
            self.assertEqual(planner.battle_values('1', state.observe('1', profiles, observed, 1)), (base, 0))
        other = state.observe('1', self.store.profiles('弧光', 'battle'), True, 2)
        self.assertEqual(planner.battle_values('1', other), (base, 0))

    def test_native_candidate_and_overflow_score_change_but_cost_does_not(self):
        task = FakeTask()
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['狼卫', '安塔尔', '余烬', '骏卫'], reset_runtime=True)
        task.sp = 100
        logic._observe_battle_highlights()
        plain = logic._release_battle_candidate('1', 100, 0, set())
        plain_score = logic._overflow_score('1')
        task.battle_pulses.add('1')
        logic._observe_battle_highlights()
        enhanced = logic._release_battle_candidate('1', 100, 0, set())
        self.assertEqual(plain.value, enhanced.value)
        self.assertEqual(plain.observed_bonus, 0)
        self.assertGreater(enhanced.observed_bonus, 0)
        self.assertEqual((plain.sp_cost, enhanced.sp_cost), (100, 100))
        self.assertGreater(logic._overflow_score('1'), plain_score)
        task.battle_pulses.clear()
        logic._observe_battle_highlights()
        self.assertEqual(logic._overflow_score('1'), plain_score)
        self.assertTrue(logic._try_battle_token('1', 100))

    def test_ring_does_not_override_sp_budget_or_dispatch_by_itself(self):
        task = FakeTask()
        task.sp = 0
        task.battle_pulses.add('1')
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['狼卫', '安塔尔', '余烬', '骏卫'], reset_runtime=True)
        logic.step()
        self.assertTrue(logic.battle_highlights.observations['1'].ready)
        self.assertIsNone(logic._release_battle_candidate('1', 0, task.now, set()))
        self.assertNotIn('1', task.keys)

    def test_same_burst_candidates_change_order_from_damage_value(self):
        # All SP costs/time/other actions are identical. Only current enhanced
        # damage differs, and search rather than a pulse-priority rule decides.
        planner = ReleaseBurstPlanner(['安塔尔', '狼卫', '佩丽卡'])
        support = ReleaseBurstAction('1', 'ult', .5, 0)
        ordinary = ReleaseBurstAction('2', 'battle', .5, 100, 100, 100)
        ally = ReleaseBurstAction('3', 'battle', .5, 500, 100, 100)
        plain = planner.choose((support, ordinary, ally), 200, 0)
        enhanced = ReleaseBurstAction('2', 'battle', .5, 100, 100, 100, observed_bonus=2000)
        current = planner.choose((support, enhanced, ally), 200, 0)
        self.assertEqual((plain.action.slot, plain.action.kind), ('1', 'ult'))
        self.assertEqual((current.action.slot, current.action.kind), ('2', 'battle'))
        self.assertFalse(planner.bonuses)

    def test_current_condition_is_not_carried_past_another_forecast_action(self):
        planner = ReleaseBurstPlanner(['狼卫', '余烬'])
        action = ReleaseBurstAction('1', 'battle', .5, 100, 100, 100, observed_bonus=2000)
        node = (0.0, 0.0, 100.0, (), [], {}, {})
        first = planner._forecast_step(node, action, 1, 0, releases=False, horizon=6)
        after_other = (0.0, .5, 100.0, (0,), [], {}, {})
        later = planner._forecast_step(after_other, action, 1, 0, releases=False, horizon=6)
        self.assertEqual(first[0], 2100)
        self.assertEqual(later[0], 100)


if __name__ == '__main__':
    unittest.main()
