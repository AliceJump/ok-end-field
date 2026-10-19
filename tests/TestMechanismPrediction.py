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

    def test_zhuang_free_quote_is_explicit_nominal_branch_without_publishing_inventory(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', now=3, enhanced=True)
        first = p.mechanism_quote('1', 3, obs)
        self.assertEqual(first.conduct, 0)
        self.assertEqual(first.selected_swords, 3)
        self.assertEqual(first.same_cast_requested_swords, 3)
        self.assertIn('same_cast_requests_reach_before_area_snapshot_assumed', first.assumptions)
        self.assertFalse(p.mechanism_state.requests)
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
        self.assertEqual(logic._attempt_mechanism.selected_swords, 3)
        self.assertEqual(logic._attempt_mechanism.same_cast_requested_swords, 3)
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

    def test_overflow_and_burst_use_the_same_current_cast_weapon_and_bone_quote(self):
        task = FakeTask()
        task.sp = 100
        task.now = 1
        task.battle_pulses = {'1'}
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['艾维文娜'], reset_runtime=True)
        logic.release_burst.confirm('1', 'link', 0)
        logic._observe_battle_highlights()
        action = logic._release_battle_candidate('1', 100, 1, set())
        decision = logic.release_burst.choose((action,), 100, 1, conditions=logic._scene_conditions())
        self.assertAlmostEqual(logic._overflow_score('1')*max(action.duration, .3), decision.damage)
        self.assertAlmostEqual(decision.damage, 116032.0, delta=1)

    def test_rangliu_requires_consumption_not_a_bare_ring_or_first_free_cast(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        obs = self.observed('庄方宜')
        q = p.mechanism_quote('1', 0, obs)
        p.confirm('1', 'battle', 0, mechanism=q)
        self.assertFalse(p.mechanism_state.consumption_stacks)
        p.confirm('1', 'ult', 1)
        q = p.mechanism_quote('1', 4, self.observed('庄方宜', enhanced=True))
        self.assertEqual(q.conduct, 0)
        p.confirm('1', 'battle', 4, mechanism=q)
        self.assertFalse(p.mechanism_state.consumption_stacks)

    def test_rangliu_is_future_owner_only_and_does_not_reprice_its_own_cast(self):
        p = ReleaseBurstPlanner(['庄方宜', '佩丽卡'], store=self.store)
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', enhanced=True)
        p.confirm('1', 'battle', 3, mechanism=p.mechanism_quote('1', 3, obs))
        q = p.mechanism_quote('1', 10, obs)
        before = p.preview_release_price('1', 'battle', 10, 1, mechanism=q)
        self.assertEqual(p.mechanism_state.consumption_stacks, ())
        p.confirm('1', 'battle', 10, mechanism=q)
        p.confirm('1', 'battle', 10, mechanism=q)  # Repeated confirmation is idempotent.
        self.assertEqual(sum(w.key=='rangliu' for w in p.mechanism_state.consumption_stacks), 1)
        self.assertAlmostEqual(p.price('1', 'battle', 11, mechanism=q), before)
        # Isolate the suit contribution; the weapon coexistence is tested below.
        state = replace(p.mechanism_state, consumption_stacks=tuple(
            w for w in p.mechanism_state.consumption_stacks if w.key=='rangliu'))
        p.mechanism_state = state
        amount = p.rows['1']['mechanism']['consumption_equipment']['amount_per_stack']
        self.assertEqual(state.consumption_bonus('1', 16, '电磁'), 0)
        self.assertEqual(state.consumption_bonus('1', 17, '电磁'), amount)
        self.assertEqual(state.consumption_bonus('2', 18, '电磁'), 0)
        self.assertEqual(state.consumption_bonus('1', 18, '灼热'), 0)
        self.assertEqual(state.consumption_bonus('1', 18, '自然'), amount)
        self.assertEqual(state.consumption_bonus('1', 18, '电磁', until=35), 0)
        later = p.mechanism_quote('1', 18, obs)
        self.assertEqual(later.model_consumption_bonus, amount)
        without = replace(state, consumption_stacks=())
        price = p.price('1', 'battle', 18, mechanism=later)
        base = p.price('1', 'battle', 18, mechanism=later, mechanism_state=without)
        self.assertAlmostEqual(price, 238972.79038467698)
        bonus = p.quote('1', 'battle')['bonus_pct']/100
        self.assertAlmostEqual(price/base, (1+bonus+amount)/(1+bonus))

    def test_rangliu_independent_expiry_cap_does_not_invent_refresh(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        equipment = p.rows['1']['mechanism']['consumption_equipment']
        state = MechanismState()
        for started in (0, 1, 2, 3):
            state = state.after_consumption('1', str(started), equipment, started, started+7, 1)
        self.assertEqual(len(state.consumption_stacks), 3)
        self.assertEqual([w.expires for w in state.consumption_stacks], [25, 26, 27])
        self.assertAlmostEqual(state.consumption_bonus('1', 24, '电磁'), 3*equipment['amount_per_stack'])
        self.assertAlmostEqual(state.consumption_bonus('1', 25, '电磁'), 2*equipment['amount_per_stack'])
        state = state.after_consumption('1', 'new', equipment, 25, 32, 1)
        self.assertEqual([w.expires for w in state.consumption_stacks], [26, 27, 50])
        self.assertEqual(state.consumption_bonus('1', 30, '电磁'), 0)
        self.assertAlmostEqual(state.consumption_bonus('1', 32, '电磁'), equipment['amount_per_stack'])
        self.assertEqual(state.keep({'2'}).consumption_stacks, ())

    def test_rangliu_forecast_keeps_real_state_and_failed_attempt_unchanged(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, self.store, clock=lambda: task.now)
        logic._configure_team(['庄方宜'], reset_runtime=True)
        p = logic.release_burst
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', enhanced=True)
        p.confirm('1', 'battle', 3, mechanism=p.mechanism_quote('1', 3, obs))
        task.now = 10
        task.battle_pulses = {'1'}
        logic._observe_battle_highlights()
        action = logic._release_battle_candidate('1', 100, 10, set())
        before = p.mechanism_state
        decision = p.choose((action,), 100, 10, conditions=logic._scene_conditions())
        self.assertIsNotNone(decision)
        self.assertEqual(p.mechanism_state, before)
        self.assertTrue(logic._try_battle_token('1', 100))
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        logic._probe_action_feedback()
        self.assertEqual(p.mechanism_state, before)
        p.source_unavailable('1')
        self.assertFalse(p.mechanism_state.consumption_stacks)

    def test_guzhou_consume_and_ultimate_modifiers_coexist_without_same_cast_backfill(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', enhanced=True)
        p.confirm('1', 'battle', 3, mechanism=p.mechanism_quote('1', 3, obs))
        q = p.mechanism_quote('1', 10, obs)
        before = p.preview_release_price('1', 'battle', 10, 1, mechanism=q)
        p.confirm('1', 'battle', 10, mechanism=q)
        self.assertAlmostEqual(p.price('1', 'battle', 11, mechanism=q), before)
        later = p.mechanism_quote('1', 17, obs)
        value = p.price('1', 'battle', 17, mechanism=later)
        without = p.price('1', 'battle', 17, mechanism=later,
                          mechanism_state=replace(p.mechanism_state, consumption_stacks=()))
        amount = p.rows['1']['mechanism']['consumption_weapon']['amount_per_stack']
        suit = p.rows['1']['mechanism']['consumption_equipment']['amount_per_stack']
        ult = p.rules['1', 'ult'][0]['magnitude']['base']
        bonus = p.quote('1', 'battle')['bonus_pct']/100
        self.assertEqual(later.model_consumption_bonus, amount+suit)
        self.assertAlmostEqual(value/without, (1+bonus+ult+amount+suit)/(1+bonus+ult))
        self.assertAlmostEqual(without, 330414.0550171135)

    def test_guzhou_counts_one_per_consume_event_and_filters_damage_type(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        p.confirm('1', 'ult', 0)
        obs = self.observed('庄方宜', enhanced=True)
        p.confirm('1', 'battle', 3, mechanism=p.mechanism_quote('1', 3, obs))
        q = p.mechanism_quote('1', 10, obs)
        # Explicit high-conduct test input tests per-event versus per-layer.
        p.confirm('1', 'battle', 10, mechanism=replace(q, conduct=4))
        state = p.mechanism_state
        self.assertEqual(sum(w.key=='guzhou' for w in state.consumption_stacks), 1)
        self.assertEqual(sum(w.key=='rangliu' for w in state.consumption_stacks), 3)
        isolated = replace(state, consumption_stacks=tuple(w for w in state.consumption_stacks if w.key=='guzhou'))
        amount = p.rows['1']['mechanism']['consumption_weapon']['amount_per_stack']
        self.assertEqual(isolated.consumption_bonus('1', 17, '电磁', damage_tags=('skill',)), amount)
        self.assertEqual(isolated.consumption_bonus('1', 17, '电磁', damage_tags=('normal',)), 0)
        self.assertEqual(isolated.consumption_bonus('1', 17, '自然', damage_tags=('skill',)), 0)
        self.assertEqual(isolated.consumption_bonus('2', 17, '电磁', damage_tags=('skill',)), 0)
        self.assertEqual(isolated.consumption_bonus('1', 30, '电磁', damage_tags=('skill',)), 0)

    def test_consumption_caps_and_expiry_are_independent_for_suit_and_weapon(self):
        p = ReleaseBurstPlanner(['庄方宜'], store=self.store)
        spec = p.rows['1']['mechanism']
        state = MechanismState()
        for started in (0, 1, 2):
            state = state.after_consumption('1', str(started), spec['consumption_equipment'], started, started+7, 1)
            state = state.after_consumption('1', str(started), spec['consumption_weapon'], started, started+7, 1)
        self.assertEqual(sum(w.key=='guzhou' for w in state.consumption_stacks), 2)
        self.assertEqual(sum(w.key=='rangliu' for w in state.consumption_stacks), 3)
        bonus = state.consumption_bonus('1', 20, '电磁', damage_tags=('skill',))
        self.assertAlmostEqual(bonus, spec['consumption_weapon']['amount_per_stack']+
                               3*spec['consumption_equipment']['amount_per_stack'])


class TestZhuangEffectiveMultipliers(unittest.TestCase):
    def make_three_sword_case(self, team=('庄方宜',)):
        p = ReleaseBurstPlanner(team, store=load_skill_timings())
        p.confirm('1', 'ult', 0)
        # The free-action contract supplies a conditional three-request branch;
        # it does not initialize or observe the initial sword inventory.
        state = BattleHighlightState(load_skill_timings())
        profiles = (load_skill_timings().profile('chr_0030_zhuangfy_normal_skill_ult'),)
        obs = BattleConditionEvaluator(team).assess(state.observe('1', profiles, True, 3))
        return p, p.mechanism_quote('1', 3, obs)

    def test_three_explicit_swords_use_each_segment_rate_not_final_rate_for_all(self):
        p, quote = self.make_three_sword_case()
        before = p.mechanism_state
        self.assertEqual(quote.selected_swords, 3)
        self.assertEqual(quote.conduct, 0)
        self.assertEqual(quote.nominal_hit_offsets, (.9, 1.1, 2.8))
        self.assertAlmostEqual(p.price('1', 'battle', 3, mechanism=quote), 211550.8189915943, places=4)
        self.assertLess(p.price('1', 'battle', 3, mechanism=quote), 171643.66703456*1.24)
        self.assertEqual(p.mechanism_state, before)  # Preview never publishes marks.

    def test_personal_model_has_no_effect_from_ultimate_alone(self):
        p = ReleaseBurstPlanner(['庄方宜'])
        p.confirm('1', 'ult', 0)
        self.assertFalse(p.mechanism_state.skill_amplifications)
        self.assertAlmostEqual(p.price('1', 'normal', 3), p.stance_spec('1')['quotes']['normal']['crit_expect'])

    def test_confirmed_cast_installs_delayed_base_then_mark_changes_and_expiry(self):
        p, quote = self.make_three_sword_case()
        p.confirm('1', 'battle', 3, mechanism=quote)
        model = p.mechanism_state
        self.assertEqual(model.amplification('1', 3.1), 0)
        self.assertAlmostEqual(model.amplification('1', 3.2), .18, places=7)
        for stamp, rate in ((3.9, .20), (4.1, .22), (5.8, .24)):
            self.assertAlmostEqual(model.amplification('1', stamp), rate, places=7)
        self.assertAlmostEqual(model.amplification('1', 8.1), .24, places=7)
        self.assertEqual(model.amplification('1', 3+5/30+5), 0)
        base = p.stance_spec('1')['quotes']['normal']['crit_expect']
        self.assertAlmostEqual(p.price('1', 'normal', 6)/base, 1.24, places=7)
        self.assertAlmostEqual(p.price('1', 'normal', 9), base)
        self.assertEqual(model.keep({'2'}).skill_amplifications, ())

    def test_repeated_confirmation_is_idempotent_and_recast_resets_at_new_marker(self):
        p, quote = self.make_three_sword_case()
        p.confirm('1', 'battle', 3, mechanism=quote)
        before = p.mechanism_state
        p.confirm('1', 'battle', 3, mechanism=quote)
        self.assertEqual(p.mechanism_state, before)
        p.confirm('1', 'battle', 7, mechanism=replace(quote, quote_started=7))
        self.assertAlmostEqual(p.mechanism_state.amplification('1', 7.1), .24, places=7)
        self.assertAlmostEqual(p.mechanism_state.amplification('1', 7.2), .18, places=7)
        p.source_unavailable('1')
        self.assertFalse(p.mechanism_state.skill_amplifications)

    def test_normal_chain_does_not_backfill_late_marks_or_an_expiring_personal_window(self):
        p, quote = self.make_three_sword_case()
        p.confirm('1', 'battle', 3, mechanism=quote)
        base = p.stance_spec('1')['quotes']['normal']['crit_expect']
        self.assertAlmostEqual(p.price('1', 'normal', 6, action_started=3.4)/base, 1.18, places=7)
        self.assertAlmostEqual(p.price('1', 'normal', 9, action_started=6), base)

    def test_unknown_ordinary_sword_count_does_not_manufacture_additional_mark_count(self):
        p = ReleaseBurstPlanner(['庄方宜'])
        quote = p.mechanism_quote('1', 3)
        self.assertIsNone(quote.selected_swords)
        self.assertEqual(quote.same_cast_requested_swords, 0)
        p.confirm('1', 'battle', 3, mechanism=quote)
        self.assertEqual(p.mechanism_state.skill_amplifications[0].marks, ())

    def test_stronger_liino_amplification_replaces_personal_factor_instead_of_adding(self):
        p, quote = self.make_three_sword_case(('庄方宜', '梨诺'))
        p.confirm('2', 'ult', 0)
        baseline = 171643.66703456
        panel = p.rows['1']['panel']
        raw = panel['attack_white']*(1+panel['attack_percent'])+panel['attack_flat']
        attack = (raw+panel['attack_white']*p.rules['2', 'ult'][0]['magnitude']['base'])/raw
        amp = p.rules['2', 'ult'][1]['magnitude']['base']
        self.assertAlmostEqual(p.price('1', 'battle', 3, mechanism=quote), baseline*attack*(1+amp), places=4)
        self.assertLess(p.price('1', 'battle', 3, mechanism=quote), baseline*attack*(1+amp+.18))

    def test_personal_effect_does_not_leak_to_a_teammate_or_change_base_snapshot(self):
        p, quote = self.make_three_sword_case(('庄方宜', '佩丽卡'))
        before = p.price('2', 'battle', 4)
        normal_quote = dict(p.stance_spec('1')['quotes']['normal'])
        p.confirm('1', 'battle', 3, mechanism=quote)
        self.assertEqual(p.price('2', 'battle', 4), before)
        self.assertEqual(p.stance_spec('1')['quotes']['normal'], normal_quote)

    def test_real_failed_attempt_does_not_install_amplification(self):
        task = FakeTask()
        task.now, task.sp, task.battle_pulses = 3, 0, {'1'}
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(['庄方宜'], reset_runtime=True)
        logic.release_burst.confirm('1', 'ult', 0)
        self.assertTrue(logic._try_battle_token('1', 0))
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        logic._probe_action_feedback()
        self.assertFalse(logic.release_burst.mechanism_state.skill_amplifications)

    def test_real_step_prices_the_effective_cast_before_white_acknowledgement(self):
        task = FakeTask()
        task.now, task.sp, task.battle_pulses = 3, 0, {'1'}
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(['庄方宜'], reset_runtime=True)
        p = logic.release_burst
        p.confirm('1', 'ult', 0)
        logic.step()
        self.assertEqual(task.keys, ['1'])
        self.assertTrue(any('211551' in message for message in task.messages))
        self.assertFalse(p.mechanism_state.skill_amplifications)  # Still awaiting the white acknowledgement.
