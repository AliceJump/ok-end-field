"""A highlight changes damage valuation, never ordinary skill eligibility."""

import unittest
from dataclasses import replace

from src.data.battle_conditions import ForecastConditions
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
        node = (0.0, 0.0, 100.0, (), [], {}, {}, ForecastConditions())
        first = planner._forecast_step(node, action, 1, 0, releases=False, horizon=6)
        after_other = (0.0, .5, 100.0, (0,), [], {}, {}, ForecastConditions())
        later = planner._forecast_step(after_other, action, 1, 0, releases=False, horizon=6)
        self.assertEqual(first[0], 2100)
        self.assertEqual(later[0], 100)

    def test_arclight_base_and_conduct_extra_keep_separate_native_elements(self):
        planner = ReleaseBurstPlanner(['弧光'], store=self.store)
        state = BattleHighlightState(self.store, ['弧光'])
        profiles = self.store.profiles('弧光', 'battle')
        base, extra = planner.battle_components('1', state.observe('1', profiles, True, 0))
        self.assertEqual((base[0].element, extra[0].element), ('物理', '电磁'))
        self.assertEqual((base[0].bonus_pct, extra[0].bonus_pct), (29.9, 33.6))
        panel = planner.rows['1']['panel']
        neutral = panel['attack_white'] * panel['attribute_factor'] * (1 + panel['crit_rate'] * panel['crit_damage'])
        self.assertAlmostEqual(base[0].value, neutral * 2 * 1.0099999904632568 * 1.299)
        self.assertAlmostEqual(extra[0].value, neutral * 4.050000190734863 * 1.336)
        for observed in (False, None):
            self.assertEqual(planner.battle_components('1', state.observe('1', profiles, observed, 1)), (base, ()))
        wrong = state.observe('1', self.store.profiles('狼卫', 'battle'), True, 2)
        self.assertEqual(planner.battle_components('1', wrong), (base, ()))

    def test_team_electric_amplification_only_prices_arclight_electric_component(self):
        planner = ReleaseBurstPlanner(['安塔尔', '弧光', '秋栗'], store=self.store)
        binding = planner.highlight_damage['2']
        base, extra = binding.base_components, binding.conditional_components
        amp = 1 + next(s['magnitude']['base'] for s in planner.rules['1', 'ult'] if '电磁' in s['elements'])
        attack = 1 + planner.rules['3', 'ult'][0]['magnitude']['base']
        planner.confirm('1', 'ult', 0)
        self.assertAlmostEqual(planner.price_components('2', 1, base), base[0].value)
        self.assertAlmostEqual(planner.price_components('2', 1, extra), extra[0].value * amp)
        self.assertAlmostEqual(planner.price_components('2', 1, base + extra), base[0].value + extra[0].value * amp)
        planner.confirm('3', 'ult', 0)
        self.assertAlmostEqual(planner.price_components('2', 1, base + extra),
                               (base[0].value + extra[0].value * amp) * attack)
        self.assertAlmostEqual(planner.price_components('2', 13, base + extra), base[0].value + extra[0].value)

    def test_arclight_real_candidate_and_overflow_use_the_same_component_prices(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['弧光', '安塔尔', '佩丽卡', '余烬'], reset_runtime=True)
        logic.release_burst.confirm('2', 'ult', 0)
        logic._observe_battle_highlights()
        plain = logic._release_battle_candidate('1', 100, 0, set())
        plain_score = logic._overflow_score('1')
        task.battle_pulses.add('1')
        logic._observe_battle_highlights()
        enhanced = logic._release_battle_candidate('1', 100, 0, set())
        self.assertEqual((plain.sp_cost, enhanced.sp_cost), (100, 100))
        self.assertEqual(plain.base_components, enhanced.base_components)
        self.assertEqual(plain.observed_components, ())
        self.assertEqual(enhanced.observed_components[0].element, '电磁')
        expected = logic.release_burst.price_components('1', 0, enhanced.base_components + enhanced.observed_components)
        self.assertAlmostEqual(logic._overflow_score('1'), expected / max(enhanced.duration, .3))
        self.assertGreater(logic._overflow_score('1'), plain_score)
        task.battle_pulses.clear()
        logic._observe_battle_highlights()
        self.assertEqual(logic._overflow_score('1'), plain_score)
        self.assertIsNotNone(logic._release_battle_candidate('1', 100, 0, set()))

    def test_arclight_component_search_rechecks_condition_after_other_forecast_action(self):
        planner = ReleaseBurstPlanner(['弧光', '安塔尔'], store=self.store)
        binding = planner.highlight_damage['1']
        action = ReleaseBurstAction('1', 'battle', .5, binding.base_value, 100, 100,
                                    observed_bonus=binding.conditional_value,
                                    base_components=binding.base_components,
                                    observed_components=binding.conditional_components)
        planner.confirm('2', 'ult', 0)
        amp = 1 + next(s['magnitude']['base'] for s in planner.rules['2', 'ult'] if '电磁' in s['elements'])
        first = (0.0, 0.0, 100.0, (), list(planner.bonuses), {}, {}, ForecastConditions())
        later = (0.0, .5, 100.0, (0,), list(planner.bonuses), {}, {}, ForecastConditions())
        self.assertAlmostEqual(planner._forecast_step(first, action, 1, 0, releases=False, horizon=6)[0],
                               binding.base_value + binding.conditional_value * amp)
        self.assertAlmostEqual(planner._forecast_step(later, action, 1, 0, releases=False, horizon=6)[0],
                               binding.base_value)

    def test_arclight_condition_changes_burst_order_without_a_pulse_priority(self):
        planner = ReleaseBurstPlanner(['安塔尔', '弧光', '佩丽卡'], store=self.store)
        binding = planner.highlight_damage['2']
        support = ReleaseBurstAction('1', 'ult', .5, 0)
        ordinary = ReleaseBurstAction('2', 'battle', .5, binding.base_value, 100, 100,
                                      base_components=binding.base_components)
        ally = ReleaseBurstAction('3', 'battle', .5, 7000, 100, 100)
        enhanced = replace(ordinary, observed_bonus=binding.conditional_value,
                           observed_components=binding.conditional_components)
        plain = planner.choose((support, ordinary, ally), 200, 0)
        current = planner.choose((support, enhanced, ally), 200, 0)
        self.assertEqual((plain.action.slot, plain.action.kind), ('1', 'ult'))
        self.assertEqual((current.action.slot, current.action.kind), ('2', 'battle'))
        self.assertEqual(current.action.sp_cost, 100)


if __name__ == '__main__':
    unittest.main()
