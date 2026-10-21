"""Release buffs change real dispatch, without assuming hits or native completeness."""

import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_resolution import FixedDamagePanel, TimedDamageState
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.data.team_phase_planner import BurstAction, CombatPhase, make_burst_plan
from src.data.timing_dps import load_damage_quotes
from src.data.yvonne_normal_window import YvonneNormalWindow, _projectile_counts
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestReleaseBurstPlanner(unittest.TestCase):
    def test_zero_damage_team_buff_precedes_carry_and_preserves_shared_sp(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0),
                   ReleaseBurstAction("2", "ult", 1, 1000),
                   ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        decision = planner.choose(actions, 100, 0)
        self.assertEqual((decision.action.slot, decision.action.kind), ("1", "ult"))
        self.assertEqual(decision.sequence, (("1", "ult"), ("2", "ult"), ("2", "battle")))
        self.assertAlmostEqual(decision.damage, 2440, places=4)
        self.assertAlmostEqual(decision.gain, 440, places=4)
        self.assertFalse(planner.state.modifiers, "Forecasting must not apply a live buff")

    def test_wrong_element_no_budget_and_expired_window_do_not_promote_buff(self):
        planner = ReleaseBurstPlanner(["安塔尔", "陈千语"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0), ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        self.assertIsNone(planner.choose(actions, 100, 0))
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        self.assertIsNone(planner.choose(actions, 99, 0))
        late = (actions[0], ReleaseBurstAction("2", "battle", 12, 1000, 100, 100))
        self.assertIsNone(planner.choose(late, 100, 0, horizon=15))

    def test_same_source_refresh_expiry_and_personal_filters(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫"])
        original = planner.price("1", "ult", 0)
        ally = planner.price("2", "ult", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(len(planner.state.modifiers), 1)
        self.assertGreater(planner.price("1", "ult", 1), original)
        self.assertEqual(planner.price("2", "ult", 1), ally)
        planner.confirm("1", "ult", 5)
        self.assertEqual(len(planner.state.modifiers), 1)
        self.assertGreater(planner.price("1", "ult", 15), original)
        self.assertEqual(planner.price("1", "ult", 20), original)
        zhuang = ReleaseBurstPlanner(["庄方宜"])
        before_battle = zhuang.price("1", "battle", 0)
        before_ult = zhuang.price("1", "ult", 0)
        zhuang.confirm("1", "ult", 0)
        self.assertGreater(zhuang.price("1", "battle", 1), before_battle)
        self.assertEqual(zhuang.price("1", "ult", 1), before_ult)

    def test_normal_weapon_bonus_influences_ultimate_choice_only_with_normal_tail(self):
        planner = ReleaseBurstPlanner(["莱万汀"])
        ult = ReleaseBurstAction("1", "ult", 1, 1000)
        normal = ReleaseBurstAction("1", "normal", 3, 1000)
        self.assertIsNone(planner.choose((ult,), 0, 0))
        decision = planner.choose((ult, normal), 0, 0)
        self.assertEqual(decision.action.kind, "ult")
        self.assertGreater(decision.gain, 0)
        self.assertEqual(decision.sequence, (("1", "ult"), ("1", "normal")))
        original = planner.price("1", "battle", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.price("1", "battle", 1), original)

    def test_akekuri_atk_uses_native_window_and_selected_potential_and_real_attack_basis(self):
        planner = ReleaseBurstPlanner(["秋栗", "狼卫"], store=load_skill_timings())
        original = planner.price("2", "battle", 0)
        planner.confirm("1", "ult", 0)
        panel = planner.panels["2"]
        self.assertAlmostEqual(planner.price("2", "battle", 1) / original,
                               panel.attack(.10000000149011612) / panel.attack())
        self.assertEqual(planner.price("2", "battle", 5), original)
        planner.confirm("1", "ult", 10)
        planner.confirm("2", "battle", 11)
        self.assertGreater(planner.price("2", "battle", 11), original, "Ally casts do not end source window")
        planner.confirm("1", "battle", 12)
        self.assertEqual(planner.price("2", "battle", 12), original)

    def test_confirmed_final_attribute_change_updates_attack_quote_and_decision(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫"])
        original = planner.price("1", "battle", 0)
        planner.state.apply_final_attribute_delta(actor="1", key="confirmed", source="1", attribute="智识",
                                                 amount=100, now=0, duration=5)
        self.assertGreater(planner.price("1", "battle", 1), original)
        self.assertIsNotNone(planner.choose((ReleaseBurstAction("1", "battle", 1, original, 100, 100),), 100, 1))
        self.assertEqual(planner.price("1", "battle", 5), original)

    def test_unknown_attribute_aura_hit_combo_and_outcome_are_not_enabled(self):
        planner = ReleaseBurstPlanner(["赛希", "梨诺", "洁尔佩塔", "艾维文娜"])
        self.assertEqual(len(planner.deferred), 2)
        for slot in ("1", "2", "3", "4"):
            self.assertFalse(planner.rules[slot, "ult"])
        self.assertTrue(planner.rules["4", "battle"])
        self.assertNotIn(("4", "link"), planner.rules)

    def test_portrait_completion_keeps_accepted_team_window(self):
        previous = ReleaseBurstPlanner(["安塔尔", "?"])
        previous.confirm("1", "ult", 0)
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        planner.preserve(previous)
        original = planner.price("2", "battle", 1, state=planner.empty)
        self.assertAlmostEqual(planner.price("2", "battle", 1) / original, 1.22, places=5)
        self.assertEqual(planner.price("2", "battle", 12), original)

    def test_same_actor_lock_cannot_create_a_fictitious_followup_inside_buff(self):
        planner = ReleaseBurstPlanner(["佩丽卡"])
        actions = (ReleaseBurstAction("1", "ult", 1, 100, same_actor_duration=20),
                   ReleaseBurstAction("1", "battle", 1, 1000, 100, 100))
        self.assertEqual(planner.choose(actions, 100, 0, horizon=25).sequence,
                         (("1", "battle"), ("1", "ult")), "Only the ultimate's own buffed damage remains")
        # A source's same-actor lock must not stall other team members.
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0, same_actor_duration=20),
                   ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        self.assertEqual(planner.choose(actions, 100, 0).action.slot, "1")

    def test_forecast_refund_cannot_exceed_the_shared_sp_cap(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0, sp_cost=-100),
                   ReleaseBurstAction("2", "battle", 1, 1000, 350, 100))
        self.assertIsNone(planner.choose(actions, 300, 0))


class TestYvonneNormalWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_skill_timings()
        cls.model = YvonneNormalWindow.from_store(cls.store, 12)

    def setUp(self):
        self.panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        self.state = TimedDamageState(("1", "2"))

    def test_authored_primary_cadence_ramps_layers_and_one_finisher(self):
        attacks = self.model.attacks(0)
        self.assertEqual(len([a for a in attacks if a.kind == "shot"]), 73)
        self.assertEqual([a.layers for a in attacks[:3]], [1, 2, 3])
        self.assertEqual([a.layers for a in attacks[3:7]], [4, 5, 6, 6], "Fourth projectile has no extra layer node")
        self.assertEqual(len([a for a in attacks if a.kind == "finisher"]), 1)
        self.assertEqual(attacks[-1].layers, 10)
        self.assertAlmostEqual(attacks[-1].projected_at, 299 / 30)
        self.assertAlmostEqual(attacks[-1].multiplier, 3)

    def test_expected_damage_uses_each_layer_not_full_crit_from_first_shot(self):
        quote = self.model.price("1", self.panel, self.state, 0)
        expected = sum(100 * a.multiplier * (1 + min(1, a.layers * self.model.crit_per_layer)
                       * (.5 + (self.model.full_crit_damage if a.layers == 10 else 0))) for a in quote.attacks)
        self.assertAlmostEqual(quote.damage, expected)
        all_full = sum(100 * a.multiplier * (1 + .3 * 1.1) for a in quote.attacks)
        self.assertLess(quote.damage, all_full)
        self.assertAlmostEqual(quote.conditional_frozen_damage,
                               900 * (1 + .3 * 1.5) - 300 * (1 + .3 * 1.1), places=4)

    def test_frozen_branch_is_conditional_and_never_duplicates_base_finisher(self):
        basic = self.model.price("1", self.panel, self.state, 0)
        frozen = self.model.price("1", self.panel, self.state, 0, frozen=True)
        unknown = self.model.price("1", self.panel, self.state, 0, frozen=None)
        self.assertAlmostEqual(frozen.damage - basic.damage, basic.conditional_frozen_damage)
        self.assertEqual(unknown.damage, basic.damage)
        self.assertEqual(len(frozen.attacks), len(basic.attacks))

    def test_stopping_or_horizon_clipping_does_not_keep_the_final_hit(self):
        early = self.model.price("1", self.panel, self.state, 0, stopped_at=4)
        self.assertTrue(early.attacks)
        self.assertTrue(all(a.projected_at < 4 and a.kind == "shot" for a in early.attacks))
        self.assertEqual(early.conditional_frozen_damage, 0)
        horizon = self.model.price("1", self.panel, self.state, 0, until=8)
        self.assertTrue(all(a.projected_at <= 8 for a in horizon.attacks))
        self.assertEqual(horizon.conditional_frozen_damage, 0)
        self.assertEqual(self.model.price("1", self.panel, self.state, 0, input_at=20).damage, 0)

    def test_team_buff_applies_at_individual_projected_times_and_not_to_whole_window(self):
        baseline = self.model.price("1", self.panel, self.state, 0)
        spec = DamageModifierSpec("short", DamageBucket.DAMAGE_BONUS, ("all",), "team", ModifierMagnitude(1),
                                  "ultimate_cast", duration=4)
        self.state.apply(spec, source_actor="2", now=0, event="ultimate_cast")
        changed = self.model.price("1", self.panel, self.state, 0)
        early_value = sum(100 * a.multiplier * (1 + a.layers * self.model.crit_per_layer
                          * (.5 + (self.model.full_crit_damage if a.layers == 10 else 0)))
                          for a in baseline.attacks if a.projected_at < 4)
        self.assertAlmostEqual(changed.damage - baseline.damage, early_value)
        self.assertLess(changed.damage, 2 * baseline.damage)

    def test_projectile_or_arms_must_converge_in_count_not_sum_both(self):
        shot = {"$type": "LaunchProjectile+Data", "$value": {"isEnable": True,
                "projectileId": "projectile_chr_0017_yvonne_ult_attack1", "castSkillOnHit": True,
                "assignBlackboard": True, "assignPairs": [], "projectileSkillId": "chr_0017_yvonne_ult_attack1_projhit"}}
        branch = {"actionData": [{"$type": "IfElseAction+IfElseActionData", "$value": {
            "isEnable": True, "succeedActions": {"actionData": [shot]}, "failActions": {"actionData": [shot]}}}]}
        self.assertEqual(_projectile_counts(branch), {1})
        changed = deepcopy(branch)
        changed["actionData"][0]["$value"]["failActions"]["actionData"] = []
        self.assertEqual(_projectile_counts(changed), {0, 1})

    def test_window_enters_actual_choice_and_old_ultimate_row_is_not_added_again(self):
        planner = ReleaseBurstPlanner(["伊冯"], store=self.store)
        ult = ReleaseBurstAction("1", "ult", self.model.first_input, 999999)
        quote = self.model.price("1", planner.panels["1"], planner.empty, 0)
        decision = planner.choose((ult,), 0, 0, horizon=quote.ends_at)
        self.assertEqual(decision.action, ult)
        self.assertEqual(decision.sequence, (("1", "ult"), ("1", "normal_window")))
        self.assertAlmostEqual(decision.damage, quote.damage)
        self.assertEqual(planner.price("1", "ult", 0, value=999999), 0)
        self.assertFalse(planner.window_casts, "Speculative activation must not mutate accepted windows")

    def test_opening_gain_subtracts_same_horizon_ordinary_output_and_can_choose_not_to_cast(self):
        planner = ReleaseBurstPlanner(["伊冯"], store=self.store)
        ult = ReleaseBurstAction("1", "ult", self.model.first_input, 999999)
        decision = planner.choose((ult,), 0, 0, main="1")
        ordinary = planner.quotes["1", "normal"]["crit_expect"] / planner.normal_durations["1"] * 12
        self.assertAlmostEqual(decision.gain, decision.damage - ordinary)
        self.assertLess(decision.gain, decision.damage)
        # A large existing ordinary quote represents a real opportunity cost;
        # the forecast must not fall through to a legacy automatic ultimate.
        planner.quotes["1", "normal"] = {**planner.quotes["1", "normal"], "crit_expect": 10000000}
        decision = planner.choose((ult,), 0, 0, main="1")
        self.assertEqual(decision.action.kind, "normal_window")
        self.assertEqual(decision.sequence, (("1", "normal"),))
        self.assertEqual(decision.gain, 0)

    def test_existing_team_buff_also_benefits_the_no_ultimate_counterfactual(self):
        planner = ReleaseBurstPlanner(["伊冯"], store=self.store)
        spec = DamageModifierSpec("existing_cold", DamageBucket.AMPLIFICATION, ("寒冷",), "team",
                                  ModifierMagnitude(.5), "ultimate_cast", duration=20)
        planner.state.apply(spec, source_actor="1", now=0, event="ultimate_cast")
        action = ReleaseBurstAction("1", "ult", self.model.first_input, 0)
        decision = planner.choose((action,), 0, 0, main="1")
        ordinary = planner.quotes["1", "normal"]["crit_expect"] / planner.normal_durations["1"] * 12 * 1.5
        self.assertAlmostEqual(decision.gain, decision.damage - ordinary)

    def test_cold_support_can_precede_window_without_locking_other_actors_for_seven_seconds(self):
        planner = ReleaseBurstPlanner(["伊冯", "狼卫"], store=self.store)
        planner.rules["2", "ult"] = (DamageModifierSpec("cold", DamageBucket.AMPLIFICATION, ("寒冷",), "team",
                                      ModifierMagnitude(.5), "ultimate_cast", duration=20),)
        ult = ReleaseBurstAction("1", "ult", self.model.first_input, 0)
        support = ReleaseBurstAction("2", "ult", .5, 0)
        decision = planner.choose((ult, support), 0, 0, horizon=14)
        self.assertEqual(decision.sequence[:2], (("2", "ult"), ("1", "ult")))
        self.assertGreater(decision.damage, self.model.price("1", planner.panels["1"], planner.empty, 0).damage)

    def test_confirmed_window_waits_only_on_known_main_and_expires(self):
        planner = ReleaseBurstPlanner(["伊冯", "狼卫"], store=self.store)
        planner.confirm("1", "ult", 0)
        original = planner.window_casts["1"]
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.window_casts["1"], original)
        self.assertIsNone(planner.choose((), 0, 3, main=None))
        self.assertIsNone(planner.choose((), 0, 3, main="2"))
        self.assertEqual(planner.choose((), 0, 3, main="1").action.kind, "normal_window")
        self.assertIsNone(planner.choose((), 0, 11, main="1"))
        self.assertFalse(planner.window_casts)

    def test_commands_reduce_remaining_window_and_preserve_projected_layers(self):
        planner = ReleaseBurstPlanner(["伊冯", "狼卫"], store=self.store)
        planner.confirm("1", "ult", 0)
        before = planner.normal_window_price("1", 4).damage
        planner.confirm("2", "ult", 4)
        projection = planner.window_casts["1"]
        self.assertGreater(projection.input_at, 4)
        self.assertGreater(projection.layers, 0)
        self.assertLess(planner.normal_window_price("1", 4).damage, before)
        replacement = ReleaseBurstPlanner(["伊冯", "安塔尔"], store=self.store)
        replacement.preserve(planner)
        self.assertEqual(replacement.window_casts["1"], projection)

    def test_high_value_ally_can_interrupt_and_late_recovery_cannot_invent_early_shots(self):
        planner = ReleaseBurstPlanner(["伊冯", "狼卫"], store=self.store)
        planner.confirm("1", "ult", 0, recovered_at=5)
        self.assertEqual(planner.window_casts["1"].input_at, 5)
        delayed = planner.normal_window_price("1", 5)
        self.assertEqual(delayed.attacks[0].layers, 1)
        self.assertGreater(delayed.attacks[0].projected_at, 5)
        self.assertLess(delayed.damage, self.model.price("1", planner.panels["1"], planner.empty, 0).damage)
        action = ReleaseBurstAction("2", "battle", .5, 1000000, 100, 100)
        self.assertEqual(planner.choose((action,), 100, 5, main="1").action, action)

    def test_one_to_four_member_rosters_bind_the_real_carry_slot(self):
        for size in range(1, 5):
            team = ["安塔尔", "佩丽卡", "狼卫"][:size - 1] + ["伊冯"]
            planner = ReleaseBurstPlanner(team, store=self.store)
            actor = str(size)
            self.assertEqual(set(planner.window_models), {actor})
            decision = planner.choose((ReleaseBurstAction(actor, "ult", self.model.first_input, 0),), 0, 0)
            self.assertEqual(decision.sequence, ((actor, "ult"), (actor, "normal_window")))
            self.assertGreater(decision.damage, 0)

    def test_long_interruption_expires_projected_layers_instead_of_preserving_maximum(self):
        attacks = self.model.attacks(0, input_at=6.5, initial_layers=9, initial_layers_until=6.6)
        self.assertEqual(attacks[0].layers, 1)
        planner = ReleaseBurstPlanner(["伊冯", "狼卫"], store=self.store)
        planner.confirm("1", "ult", 0)
        planner.confirm("2", "ult", 3, recovered_at=8)
        self.assertEqual(planner.window_casts["1"].layers, 0)
        self.assertIsNone(planner.window_casts["1"].layers_until)


class TestReleaseBurstDispatch(unittest.TestCase):
    def test_real_step_selects_yvonne_ultimate_and_then_holds_confirmed_owner_normals(self):
        task = FakeTask()
        task.sp = 0
        task.ults = {"1"}
        task.detect_current_char_index = lambda: 0
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["伊冯"], reset_runtime=True)
        logic._cycle_start = 0
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertEqual(logic._cycle_bonus, 0, "Future normal output is not booked as immediate ultimate damage")
        self.assertIn("1", logic.release_burst.window_casts)
        task.now = 3
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertTrue(logic._holding)
        task.link = True
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "e"], "Anonymous link keeps its existing contract, not a zero-value quote")

    def test_rejected_yvonne_ultimate_does_not_activate_normal_forecast(self):
        task = FakeTask()
        task.sp = 0
        task.ults = {"1"}
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["伊冯"], reset_runtime=True)
        with patch.object(task, "use_ult", return_value=False):
            self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("伊冯", "ult"), burst_selected=True))
        self.assertFalse(logic.release_burst.window_casts)

    def test_disabled_or_locked_main_does_not_supply_a_normal_window(self):
        task = FakeTask()
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["伊冯"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        logic._burst_main_control = "1"
        task.now = 3
        logic.disabled_slots = {"1"}
        self.assertIsNone(logic._release_burst_decision((), 0, task.now))
        logic.disabled_slots = set()
        with patch.object(logic, "_allowed", return_value=False):
            self.assertIsNone(logic._release_burst_decision((), 0, task.now))

    def make_logic(self):
        task = FakeTask()
        task.ults = {"1", "2"}
        task.sp = 300
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["安塔尔", "莱万汀", "佩丽卡", "狼卫"], reset_runtime=True)
        return task, logic

    def test_real_step_inserts_support_before_burst_and_damage_ultimate_despite_native_gaps(self):
        task, logic = self.make_logic()
        self.assertTrue(logic.combat_runtime.catalog.diagnostics)
        self.assertEqual(logic.ult_order[0], "2", "Old direct-damage ranking prefers the carry")
        plan = make_burst_plan("existing-burst", (BurstAction("2", "莱万汀", "battle", "焚灭", sp_gate=100, sp_cost=100),),
                               owner_slot="2", runtime_executable=True)
        logic.phase_planner.configure((plan,))
        before = logic.release_burst.price("2", "ult", 0)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertEqual(logic.phase_planner.state.phase, CombatPhase.BURST_READY)
        self.assertAlmostEqual(logic.release_burst.price("2", "ult", task.now) / before, 1.22, places=5)
        self.assertEqual(task.sp, 300)
        logic.step()
        self.assertIn(task.keys, (["ult_1", "2"], ["ult_1", "ult_2"]),
                      "Support precedes a legal carry output; timing decides which carry action")

    def test_team_buff_precedes_ready_damage_ultimate_without_preexisting_burst(self):
        task, logic = self.make_logic()
        task.sp = 0
        logic.step()
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

    def test_rejected_release_does_not_commit_or_change_cooldown(self):
        task, logic = self.make_logic()
        with patch.object(task, "use_ult", return_value=False):
            self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("安塔尔", "ult"), burst_selected=True))
        self.assertFalse(logic.release_burst.state.modifiers)
        self.assertFalse(logic.cooldowns)

    def test_unavailable_native_phase_cannot_be_valued_as_a_ready_beneficiary(self):
        task, logic = self.make_logic()
        original = logic.combat_runtime.catalog.available

        def available(actor, kind):
            return () if actor == "2" else original(actor, kind)

        with patch.object(logic.combat_runtime.catalog, "available", side_effect=available):
            ready = logic._ready_ultimate_actions()
            self.assertNotIn("2", [token for _, token, _ in ready])
            # Other slots cannot afford a battle; the locked carry supplies no
            # invented beneficiary for Antal's zero-direct-damage ultimate.
            self.assertIsNone(logic._release_burst_decision(ready, 0, task.now))

    def test_active_personal_buff_changes_battle_dispatch_instead_of_only_log(self):
        task = FakeTask()
        task.sp = 100
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic.team = ["佩丽卡", "狼卫"]
        logic.order = ["2"]  # The steady-state subset had excluded the buffed actor.
        logic.ult_order = ["2", "1"]
        logic.damage_quotes = load_damage_quotes(logic.team)
        logic.release_burst = ReleaseBurstPlanner(logic.team)
        # Equal direct quotes isolate the buff's effect on actual key choice.
        logic.damage_quotes = {key: replace(value, battle=1000) for key, value in logic.damage_quotes.items()}
        logic.release_burst.confirm("1", "ult", 0)
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertIsNotNone(logic.pending)
