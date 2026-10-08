"""Release buffs change real dispatch, without assuming hits or native completeness."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.data.team_phase_planner import BurstAction, CombatPhase, make_burst_plan
from src.data.timing_dps import load_damage_quotes
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


class TestReleaseBurstDispatch(unittest.TestCase):
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
        self.assertEqual(task.keys, ["ult_1", "2"], "Preserve the existing funded burst after the support opener")

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
