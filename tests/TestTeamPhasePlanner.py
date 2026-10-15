import unittest

from src.data.character_mechanics import load_character_mechanics
from src.data.skill_timing import load_skill_timings
from src.data.team_phase_planner import (
    BurstAction,
    CombatPhase,
    TeamPhasePlanner,
    build_team_burst_plans,
    derive_sp_budget,
    make_burst_plan,
)


class TestTeamPhasePlanner(unittest.TestCase):
    def test_recovered_participant_restarts_canceled_burst_from_first_action(self):
        plan = make_burst_plan(
            "recover",
            (
                BurstAction("1", "A", "battle", "first", sp_gate=50, sp_cost=50),
                BurstAction("1", "A", "battle", "second", sp_gate=50, sp_cost=50),
            ),
            owner_slot="1",
            runtime_executable=True,
        )
        planner = TeamPhasePlanner()
        planner.configure((plan,), preferred_slots=("1",))
        planner.observe_sp(100)
        planner.start_if_ready("1", "battle")
        planner.observe_action("1", "battle")
        self.assertEqual(planner.next_action.label, "second")
        planner.disable_slots({"1"})
        self.assertIsNone(planner.active_plan)

        self.assertTrue(planner.restore_slots({"1"}, preferred_slots=("1",)))
        self.assertIs(planner.active_plan, plan)
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)
        self.assertEqual(planner.next_action.label, "first")
        self.assertEqual(planner.state.disabled_slots, set())

    def test_recovery_does_not_reset_unrelated_active_burst(self):
        plan = make_burst_plan(
            "unrelated",
            (
                BurstAction("1", "A", "battle", "first", sp_gate=50, sp_cost=50),
                BurstAction("1", "A", "battle", "second", sp_gate=50, sp_cost=50),
            ),
            owner_slot="1",
            runtime_executable=True,
        )
        planner = TeamPhasePlanner()
        planner.configure((plan,))
        planner.observe_sp(100)
        planner.start_if_ready("1", "battle")
        planner.observe_action("1", "battle")
        planner.disable_slots({"2"})
        self.assertFalse(planner.restore_slots({"2"}))
        self.assertIs(planner.active_plan, plan)
        self.assertEqual(planner.state.phase, CombatPhase.BURST)
        self.assertEqual(planner.state.action_index, 1)
        self.assertEqual(planner.state.disabled_slots, set())

    def test_burst_waits_until_every_disabled_participant_recovers(self):
        plan = make_burst_plan(
            "team",
            (
                BurstAction("1", "A", "battle", "first", sp_gate=50, sp_cost=50),
                BurstAction("2", "B", "battle", "second", sp_gate=50, sp_cost=50),
            ),
            owner_slot="1",
            runtime_executable=True,
        )
        planner = TeamPhasePlanner()
        planner.configure((plan,))
        planner.disable_slots({"1", "2"})
        self.assertFalse(planner.restore_slots({"1"}, preferred_slots=("1",)))
        self.assertEqual(planner.state.disabled_slots, {"2"})
        self.assertIsNone(planner.active_plan)
        self.assertTrue(planner.restore_slots({"2"}, preferred_slots=("1", "2")))
        self.assertEqual(planner.state.disabled_slots, set())
        self.assertEqual(planner.state.action_index, 0)

    def test_shared_sp_budget_supports_multiple_slots(self):
        actions = (
            BurstAction("1", "A", "battle", "A1", sp_gate=100, sp_cost=100, sp_refund=30),
            BurstAction("2", "B", "battle", "B1", sp_gate=100, sp_cost=100),
            BurstAction("1", "A", "battle", "A2", sp_gate=50, sp_cost=50),
        )
        minimum, ending = derive_sp_budget(actions)
        self.assertEqual(minimum, 220)
        self.assertEqual(ending, 0)

        plan = make_burst_plan("team", actions, owner_slot=None)
        self.assertEqual(plan.participants, ("1", "2"))
        self.assertTrue(plan.is_team_plan)
        self.assertEqual(plan.min_start_sp, 220)

    def test_mifu_chain_derives_150_sp_start_from_refund(self):
        plans = build_team_burst_plans(
            ["弭弗", "佩丽卡", "诀", "洛茜"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        plan = next(plan for plan in plans if "mi_fu:battle-chain" in plan.key)
        self.assertTrue(plan.runtime_executable)
        self.assertEqual(plan.min_start_sp, 150)
        self.assertEqual(plan.expected_end_sp, 0)
        self.assertEqual(
            [(a.label, a.sp_gate, a.sp_cost, a.sp_refund) for a in plan.actions],
            [
                ("断云", 100, 100, 50),
                ("追形", 50, 50, 0),
                ("开天", 50, 50, 0),
            ],
        )

    def test_support_mechanic_does_not_hijack_primary_core_reserve(self):
        plans = build_team_burst_plans(
            ["弭弗", "佩丽卡", "诀", "洛茜"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        planner = TeamPhasePlanner()
        self.assertIsNone(planner.configure(plans, preferred_slots=("2", "1", "3", "4")))
        self.assertEqual(planner.state.phase, CombatPhase.NORMAL)

        chosen = planner.configure(plans, preferred_slots=("1", "2", "3", "4"))
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.owner_slot, "1")
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)

    def test_charge_reserve_blocks_spending_until_full_sequence_is_funded(self):
        plans = build_team_burst_plans(
            ["弭弗", "佩丽卡", "诀", "洛茜"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        planner = TeamPhasePlanner()
        plan = planner.configure(plans, preferred_slots=("1", "2", "3", "4"))
        self.assertEqual(plan.min_start_sp, 150)

        planner.observe_sp(120)
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)
        self.assertFalse(planner.can_spend("1", "battle", 120, 50))
        self.assertFalse(planner.can_spend("2", "battle", 120, 100))
        self.assertTrue(planner.can_spend("2", "battle", 120, 0))
        self.assertTrue(planner.can_spend("2", "battle", 120, -20))

        planner.observe_sp(149)
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)

        planner.observe_sp(150)
        self.assertEqual(planner.state.phase, CombatPhase.BURST_READY)
        self.assertTrue(planner.can_spend("1", "battle", 150, 50))
        self.assertFalse(planner.can_spend("2", "battle", 150, 100))

    def test_burst_progresses_three_mifu_actions_then_recovers(self):
        plans = build_team_burst_plans(
            ["弭弗", "佩丽卡", "诀", "洛茜"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        planner = TeamPhasePlanner()
        planner.configure(plans, preferred_slots=("1", "2", "3", "4"))
        planner.observe_sp(150)
        self.assertTrue(planner.start_if_ready("1", "battle"))

        planner.observe_action("1", "battle")
        self.assertEqual(planner.state.phase, CombatPhase.BURST)
        self.assertEqual(planner.next_action.label, "追形")

        planner.observe_action("1", "battle")
        self.assertEqual(planner.next_action.label, "开天")

        planner.observe_action("1", "battle")
        self.assertEqual(planner.state.phase, CombatPhase.RECOVER)
        self.assertIsNone(planner.next_action)

        planner.observe_sp(0)
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)
        self.assertEqual(planner.next_action.label, "断云")

    def test_shortcut_can_seek_to_second_phase_and_recompute_reserve(self):
        plans = build_team_burst_plans(
            ["弭弗", "佩丽卡", "诀", "洛茜"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        planner = TeamPhasePlanner()
        planner.configure(plans, preferred_slots=("1", "2", "3", "4"))
        planner.observe_sp(80)
        self.assertTrue(planner.seek_action("1", "battle", "追形"))
        self.assertEqual(planner.next_action.label, "追形")
        self.assertEqual(planner.target_sp, 100)
        self.assertEqual(planner.state.phase, CombatPhase.CHARGE)

        planner.observe_sp(100)
        self.assertEqual(planner.state.phase, CombatPhase.BURST_READY)

    def test_hidden_resource_damage_uses_expected_value_not_full_value(self):
        plans = build_team_burst_plans(
            ["提弗洛斯", "庄方宜", "伊冯", "洁尔佩塔"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        by_actor = {plan.actions[0].actor: plan for plan in plans}

        ty_battle = next(action for action in by_actor["提弗洛斯"].actions if action.kind == "battle")
        self.assertGreater(ty_battle.expected_damage, ty_battle.damage_low)
        self.assertLess(ty_battle.expected_damage, ty_battle.damage_high)
        self.assertAlmostEqual(ty_battle.full_probability, 0.2)
        self.assertAlmostEqual(ty_battle.expected_fraction, 0.5)

        zhuang_free = next(action for action in by_actor["庄方宜"].actions if action.label == "天理合真首次惊霆诀")
        self.assertAlmostEqual(zhuang_free.expected_damage, zhuang_free.damage_high)
        self.assertEqual(zhuang_free.full_probability, 1)

        yvonne_battle = next(action for action in by_actor["伊冯"].actions if action.kind == "battle")
        self.assertGreater(yvonne_battle.expected_damage, yvonne_battle.damage_low)
        self.assertLess(yvonne_battle.expected_damage, yvonne_battle.damage_high)
        self.assertAlmostEqual(yvonne_battle.full_probability, 0.2)
        self.assertAlmostEqual(yvonne_battle.expected_fraction, 0.5)

    def test_diagnostic_plans_preserve_non_sp_dimensions_without_auto_execution(self):
        plans = build_team_burst_plans(
            ["提弗洛斯", "庄方宜", "伊冯", "佩丽卡"],
            load_character_mechanics(),
            load_skill_timings(),
        )
        by_actor = {plan.actions[0].actor: plan for plan in plans}
        self.assertFalse(by_actor["提弗洛斯"].runtime_executable)
        self.assertFalse(by_actor["庄方宜"].runtime_executable)
        self.assertFalse(by_actor["伊冯"].runtime_executable)
        self.assertTrue(any(a.kind == "normal" for a in by_actor["提弗洛斯"].actions))
        self.assertTrue(any("ult_state" in a.produces for a in by_actor["庄方宜"].actions))
        self.assertTrue(any("STATUS_FROZEN" in a.produces for a in by_actor["伊冯"].actions))


if __name__ == "__main__":
    unittest.main()
