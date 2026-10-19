"""Source histories, scoped ring evidence and real mechanism-valued decisions."""

import unittest
from dataclasses import replace

from src.data.battle_conditions import BattleConditionEvaluator, ForecastConditions
from src.data.battle_highlight import BattleHighlightState
from src.data.combat_observation import ActionBlockReason
from src.data.mechanism_prediction import MechanismState, salvo_value
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestComboQueueScheduling import QueueTask
from tests.TestSkillTiming import FakeTask


class TestMechanismPrediction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_skill_timings()

    def observed(self, character, slot='1', visible=True, now=0, enhanced=False):
        state = BattleHighlightState(self.store)
        profiles = ((self.store.profile('chr_0030_zhuangfy_normal_skill_ult'),) if enhanced
                    else self.store.profiles(character, 'battle'))
        return BattleConditionEvaluator([character]).assess(state.observe(slot, profiles, visible, now))

    def test_ring_only_uses_minimum_untyped_recall_and_never_supplies_inventory(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        obs = self.observed('艾维文娜')
        quote = p.mechanism_quote('1', 0, obs)
        self.assertTrue(quote.minimum_recall)
        self.assertIsNone(quote.normal_spears)
        self.assertIsNone(quote.strong_spears)
        self.assertAlmostEqual(quote.value, 41649.77557466144)
        self.assertEqual(p.mechanism_state.requests, ())
        self.assertLess(p.mechanism_quote('1', 0, replace(obs, satisfied=False)).value, quote.value)
        self.assertLess(p.mechanism_quote('1', 0, replace(obs, satisfied=None)).value, quote.value)
        self.assertLess(p.mechanism_quote('1', 0, replace(obs, condition_json='{}')).value, quote.value)

    def test_minimum_evidence_cannot_be_reused_until_darkness_or_a_new_producer(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        obs = self.observed('艾维文娜')
        q = p.mechanism_quote('1', 0, obs)
        p.confirm('1', 'battle', 0, mechanism=q)
        self.assertFalse(p.mechanism_quote('1', 2, obs).minimum_recall)
        p.observe_conditions({'1': replace(obs, satisfied=False)})
        self.assertTrue(p.mechanism_quote('1', 3, obs).minimum_recall)

    def test_typed_requests_have_launch_and_expiry_but_never_observed_arrival(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        obs = self.observed('艾维文娜')
        p.confirm('1', 'link', 0)
        q = p.mechanism_quote('1', .4, obs)
        self.assertTrue(q.minimum_recall)
        q = p.mechanism_quote('1', 1, obs)
        self.assertEqual(q.normal_spears, 3)
        self.assertIsNone(q.strong_spears)
        self.assertAlmostEqual(q.value, 85657.0849677125)
        self.assertEqual(p.mechanism_state.requests[0].phase, 'requested')
        p.confirm('1', 'ult', 2)
        q = p.mechanism_quote('1', 4, obs)
        self.assertEqual((q.normal_spears, q.strong_spears), (3, 1))
        self.assertAlmostEqual(q.value, 142237.91534506404)
        dark = p.mechanism_quote('1', 4, replace(obs, satisfied=False))
        self.assertEqual(dark.value, p.quote('1', 'battle')['crit_expect'])
        self.assertEqual(len(p.mechanism_state.requests), 2)
        self.assertIsNone(p.mechanism_quote('1', 40, obs).normal_spears)

    def test_recall_consumes_current_histories_but_not_future_launches(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        obs = self.observed('艾维文娜')
        p.confirm('1', 'link', 0)
        q = p.mechanism_quote('1', 1, obs)
        p.confirm('1', 'ult', 1)
        p.confirm('1', 'battle', 1, mechanism=q)
        self.assertEqual(p.mechanism_state.requests[0].phase, 'returning')
        self.assertEqual(p.mechanism_state.requests[1].phase, 'requested')
        q = p.mechanism_quote('1', 3, obs)
        self.assertIsNone(q.normal_spears)
        self.assertEqual(q.strong_spears, 1)

    def test_repeated_same_kind_does_not_credit_unaudited_overlap(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        p.confirm('1', 'link', 0)
        p.confirm('1', 'link', 2)
        self.assertEqual(p.mechanism_quote('1', 3, self.observed('艾维文娜')).normal_spears, 3)

    def test_source_and_roster_cleanup_are_scoped(self):
        p = ReleaseBurstPlanner(['艾维文娜', '?'], store=self.store)
        p.confirm('1', 'link', 0)
        completed = ReleaseBurstPlanner(['艾维文娜', '佩丽卡'], store=self.store)
        completed.preserve(p)
        self.assertEqual(completed.mechanism_state, p.mechanism_state)
        moved = ReleaseBurstPlanner(['佩丽卡', '艾维文娜'], store=self.store)
        moved.preserve(p)
        self.assertFalse(moved.mechanism_state.requests)
        completed.source_unavailable('1')
        self.assertFalse(completed.mechanism_state.requests)

    def test_zhuang_free_request_is_not_backdated_into_its_own_snapshot(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', now=3, enhanced=True)
        first = p.mechanism_quote('1', 3, obs)
        self.assertEqual(first.conduct, 0)
        self.assertIsNone(first.selected_swords)
        p.confirm('1', 'battle', 3, mechanism=first)
        p.confirm('1', 'battle', 3, mechanism=first)
        self.assertEqual(len(p.mechanism_state.requests), 1)
        self.assertEqual(p.mechanism_state.requests[0].nominal_count, 3)
        self.assertFalse(p.stance_at('1', 4).free_battle)
        later = p.mechanism_quote('1', 4, obs)
        self.assertEqual((later.conduct, later.selected_swords), (1, 3))
        self.assertAlmostEqual(later.value, 142750.7226441165, places=4)
        self.assertIn('arrival_unconfirmed', later.assumptions)
        p.confirm('1', 'battle', 4, mechanism=later)
        self.assertEqual(sum(r.nominal_count for r in p.mechanism_state.requests), 5)
        self.assertIsNone(p.mechanism_quote('1', 4, replace(obs, satisfied=False)).selected_swords)
        self.assertIsNone(p.mechanism_quote('1', 50, obs).selected_swords)

    def test_zhuang_no_initial_zero_or_default_three_from_bare_ring(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        q = p.mechanism_quote('1', 0, self.observed('庄方宜'))
        self.assertIsNone(q.conduct)  # A mid-fight entry may have untracked free eligibility.
        self.assertIsNone(q.selected_swords)
        self.assertEqual(q.value, p.quote('1', 'battle')['crit_expect'])
        self.assertEqual(p.mechanism_state, MechanismState())

    def test_untracked_free_arm_stays_ambiguous_but_tracked_expiry_allows_minimum_conduct(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        obs = self.observed('庄方宜')
        self.assertIsNone(p.mechanism_quote('1', 0, obs).conduct)
        p.confirm('1', 'ult', 0)
        self.assertEqual(p.mechanism_quote('1', 3, self.observed('庄方宜', enhanced=True)).conduct, 0)
        self.assertEqual(p.mechanism_quote('1', 28, obs).conduct, 1)

    def test_salvo_all_conduct_and_sword_inputs_match_numeric_contract(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        row = p.rows['1']
        for enhanced in (False, True):
            for conduct in range(5):
                previous = 0
                for swords in range(1, 10):
                    value = salvo_value(row, row['mechanism'], conduct, swords, enhanced=enhanced)
                    self.assertGreater(value, previous)
                    previous = value
        self.assertAlmostEqual(salvo_value(row, row['mechanism'], 1, 2), 68131.02124725007)
        with self.assertRaises(ValueError):
            salvo_value(row, row['mechanism'], 0, 0)
        with self.assertRaises(ValueError):
            salvo_value(row, row['mechanism'], 5, 3)

    def test_forecast_projects_known_queue_producer_without_mutating_real_inventory(self):
        p = ReleaseBurstPlanner(['艾维文娜', '佩丽卡'], store=self.store)
        link = ReleaseBurstAction('1', 'link', 1, p.quote('1', 'link')['crit_expect'], queue_position=1)
        battle = ReleaseBurstAction('1', 'battle', .7, p.quote('1', 'battle')['crit_expect'], 100, 100)
        choice = p.choose((battle, link), 100, 0)
        self.assertEqual(choice.sequence, (('1', 'link'), ('1', 'battle')))
        self.assertGreater(choice.damage, link.value+battle.value+50000)
        self.assertEqual(p.mechanism_state, MechanismState())

    def test_real_step_minimum_recall_and_failure_leave_inventory_unchanged(self):
        task = FakeTask()
        task.sp = 100
        task.battle_pulses = {'3'}
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['庄方宜', '佩丽卡', '艾维文娜', '梨诺'], reset_runtime=True)
        logic.step()
        self.assertEqual(task.keys, ['3'])
        self.assertTrue(logic._attempt_mechanism.minimum_recall)
        self.assertFalse(logic.release_burst.mechanism_state.minimum_spent)
        task.sp = 0
        task.now = .2
        logic._confirm_battle(task.now)
        self.assertIn('3', logic.release_burst.mechanism_state.minimum_spent)
        self.assertIsNone(logic._attempt_mechanism)
        failed_task = FakeTask()
        failed_task.sp = 100
        failed_task.battle_pulses = {'1'}
        failed = TimedCombatLogic(failed_task, self.store, clock=lambda: failed_task.now)
        failed._configure_team(['艾维文娜'], reset_runtime=True)
        failed_task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        failed.step()
        failed.step()
        self.assertEqual(failed.release_burst.mechanism_state, MechanismState())

    def test_actual_identified_combo_produces_only_on_accepted_input(self):
        team = ['艾维文娜', '佩丽卡']
        for accept in (False, True):
            task = QueueTask(team, ['chr_0012_avywen'])
            task.accept = accept
            logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
            logic._configure_team(team, reset_runtime=True)
            logic._observe_combo_queue()
            logic._try_link_skill()
            self.assertEqual(bool(logic.release_burst.mechanism_state.requests), accept)

    def test_stale_scene_does_not_enter_unrelated_forecast_action(self):
        p = ReleaseBurstPlanner(['艾维文娜', '佩丽卡'], store=self.store)
        obs = self.observed('艾维文娜')
        action = ReleaseBurstAction('1', 'battle', .7, p.quote('1', 'battle')['crit_expect'], 100, 100,
                                    mechanism_assessment=obs)
        # A direct action may use its observation. Once an unknown transition
        # precedes it, the same scene condition must not be reused.
        node = (0., 1., 100., (0,), [], {}, {}, ForecastConditions(), 0., p.mechanism_state)
        later = p._forecast_step(node, action, 1, 0, releases=True, horizon=12, predecessors={1: ()})
        p.confirm('1', 'battle', 1)
        self.assertAlmostEqual(later[0], p.price('1', 'battle', 1))

    def test_actual_second_zhuang_battle_values_prior_free_requests(self):
        task = FakeTask()
        task.sp = 100
        task.ults = {'1'}
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['庄方宜', '佩丽卡'], reset_runtime=True)
        logic._use_timed_ultimate('1', self.store.profiles('庄方宜', 'ult'), burst_selected=True)
        task.now = 4
        task.battle_pulses = {'1'}
        self.assertTrue(logic._try_battle_token('1', 100, advance_cursor=False))
        self.assertIsNone(logic._attempt_mechanism.selected_swords)
        task.battle_pulses.clear()
        task.now = 4.2
        logic._confirm_battle(task.now)
        self.assertEqual(len(logic.release_burst.mechanism_state.requests), 1)
        task.now = 10
        task.battle_pulses = {'1'}
        logic._observe_battle_highlights()
        action = logic._release_battle_candidate('1', 100, 10, set())
        self.assertGreater(action.value, 140000)
        self.assertEqual(action.sp_cost, 100)
        self.assertGreater(logic._overflow_score('1') * action.duration, 200000)
        chosen = logic._release_burst_decision([], 100, 10)
        self.assertEqual(chosen.action.slot, '1')
        self.assertEqual(chosen.mechanism.selected_swords, 3)

    def test_failed_zhuang_attempt_does_not_create_requests_or_consume_free(self):
        task = FakeTask()
        task.sp = 0
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['庄方宜'], reset_runtime=True)
        logic.release_burst.confirm('1', 'ult', 0)
        task.now = 4
        task.battle_pulses = {'1'}
        self.assertTrue(logic._try_battle_token('1', 0))
        task.now = 5
        logic._confirm_battle(task.now)
        self.assertFalse(logic.release_burst.mechanism_state.requests)
        self.assertTrue(logic.release_burst.stance_at('1', 5).free_battle)

    def test_forecast_sp_shortage_and_dark_observation_do_not_create_a_cast(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        obs = self.observed('艾维文娜')
        action = ReleaseBurstAction('1', 'battle', .7, p.quote('1', 'battle')['crit_expect'], 100, 100,
                                    mechanism_assessment=obs)
        self.assertIsNone(p.choose((action,), 99, 0))
        self.assertEqual(p.mechanism_state, MechanismState())

    def test_combo_weapon_and_bone_are_independent_of_ring_and_not_final_multipliers(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        p.confirm('1', 'link', 0)
        # No ring supplies no spear hits, but confirmed release equipment is valid.
        q = p.mechanism_quote('1', 1)
        self.assertIsNone(q.normal_spears)
        self.assertEqual(q.tracked_bone_stacks, 1)
        self.assertAlmostEqual(p.price('1', 'battle', 1), 24204.65174741518)
        self.assertAlmostEqual(p.preview_release_price('1', 'battle', 1, .7, mechanism=q), 26612.932161517212)
        self.assertEqual(p.mechanism_state.bone_stacks, (('1', 1),))

    def test_bone_survives_weapon_expiry_and_consumes_all_on_accepted_battle(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        for at in (0, 1, 2):
            p.confirm('1', 'link', at)
        self.assertEqual(p.mechanism_state.bone_stacks, (('1', 2),))
        self.assertEqual(len(p.mechanism_state.combo_weapon), 1)
        self.assertEqual(p.mechanism_quote('1', 40).tracked_bone_stacks, 2)
        self.assertEqual(p.mechanism_state.weapon_bonus('1', 17, '电磁'), 0)
        q = p.mechanism_quote('1', 40)
        p.confirm('1', 'battle', 40, mechanism=q)
        self.assertEqual(p.mechanism_state.bone_stacks, ())
        self.assertEqual(p.mechanism_quote('1', 41).tracked_bone_stacks, 0)

    def test_two_cast_windows_and_two_bone_stacks_match_the_detailed_recall_quote(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        p.confirm('1', 'link', 0)
        p.confirm('1', 'link', 2)
        p.confirm('1', 'ult', 3)
        q = p.mechanism_quote('1', 5, self.observed('艾维文娜', now=5))
        self.assertEqual((q.normal_spears, q.strong_spears, q.tracked_bone_stacks), (3, 1, 2))
        value = p.preview_release_price('1', 'battle', 5, .7, mechanism=q)
        self.assertAlmostEqual(value, 208245.2914289281, delta=1)
        p.confirm('1', 'battle', 5, mechanism=q)
        self.assertAlmostEqual(p.price('1', 'battle', 5.7, mechanism=q), value)
        self.assertEqual(p.mechanism_state.bone_stacks, ())

    def test_failed_battle_keeps_pending_bone_and_source_requests(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['艾维文娜'], reset_runtime=True)
        logic.release_burst.confirm('1', 'link', 0)
        task.now = 2
        task.battle_pulses = {'1'}
        before = logic.release_burst.mechanism_state
        self.assertTrue(logic._try_battle_token('1', 100))
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        logic._probe_action_feedback()
        self.assertEqual(logic.release_burst.mechanism_state, before)

    def test_combo_preview_prices_current_combo_window_and_consumes_next_battle_only_in_branch(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        link = ReleaseBurstAction('1', 'link', 1, p.quote('1', 'link')['crit_expect'], queue_position=1)
        battle = ReleaseBurstAction('1', 'battle', .7, p.quote('1', 'battle')['crit_expect'], 100, 100)
        decision = p.choose((battle, link), 100, 0)
        self.assertEqual(decision.sequence, (('1', 'link'), ('1', 'battle')))
        self.assertEqual(p.mechanism_state, MechanismState())
        p.confirm('1', 'link', 0)
        q = p.mechanism_quote('1', 1, self.observed('艾维文娜', now=1))
        expected = p.price('1', 'link', 1)+p.preview_release_price('1', 'battle', 1, .7, mechanism=q)
        self.assertAlmostEqual(decision.damage, expected)

    def test_recall_does_not_credit_a_combo_weapon_window_that_expires_during_the_quote(self):
        p = ReleaseBurstPlanner(['艾维文娜'], store=self.store)
        p.confirm('1', 'link', 0)
        obs = self.observed('艾维文娜')
        early = p.mechanism_quote('1', 4, obs)
        later = p.mechanism_quote('1', 10, obs)
        with_both = p.preview_release_price('1', 'battle', 4, .7, mechanism=early)
        with_battle_only = p.preview_release_price('1', 'battle', 10, .7, mechanism=later)
        self.assertGreater(with_both, 116000)
        self.assertLess(with_battle_only, 106000)
        self.assertGreater(with_battle_only, 105000)
        self.assertLess(with_battle_only, with_both)
        self.assertEqual(p.mechanism_state.bone_stacks, (('1', 1),))

    def test_actor_swapped_mechanism_contract_is_rejected(self):
        from src.data.mechanism_prediction import validate_contract

        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        with self.assertRaisesRegex(ValueError, 'another actor'):
            validate_contract(p.rows['1']['mechanism'], actor='avywenna')

    def test_future_launch_does_not_remove_a_previous_request_before_its_own_deadline(self):
        state = MechanismState().request('1', 'old', 'sword', 1, 0, .5)
        state = state.request('1', 'future', 'sword', 1, .6, 36, cap=9, requested_at=.2)
        self.assertEqual([r.source for r in state.available('1', .3)], ['old'])
        self.assertEqual([r.source for r in state.available('1', .7)], ['future'])
