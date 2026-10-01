import json
import tempfile
import unittest
from pathlib import Path

from src.data.skill_timing import load_skill_timings
from src.data.timing_dps import (
    CastOption,
    CyclePlan,
    DamageQuote,
    build_options,
    evaluate_cycle,
    load_damage_quotes,
    optimize_cycle,
)
from tests.TestSkillTiming import FakeTask, logic_for


def option(slot, damage, duration=1, actionable=None, cost=100, cooldown=0, **quote):
    return CastOption(
        slot,
        DamageQuote(damage, quote.pop("conservative", damage), 0, 0, **quote),
        duration,
        duration if actionable is None else actionable,
        cooldown,
        cost,
        max(100, cost) if cost else 0,
    )


class TestTimingDps(unittest.TestCase):
    def test_more_damage_can_be_less_dps_and_dropped(self):
        slow = option("1", 100, duration=30)
        fast = option("2", 80)
        plan = optimize_cycle((slow, fast))
        self.assertEqual(plan.slots, ("2",))
        self.assertAlmostEqual(plan.seconds, 12.5)
        self.assertAlmostEqual(plan.dps, 6.4)
        self.assertGreater(plan.dps, evaluate_cycle((slow, fast)).dps)

    def test_full_starting_sp_is_not_infinite_resource(self):
        plan = evaluate_cycle((option("1", 100),))
        self.assertAlmostEqual(plan.seconds, 12.5)
        self.assertAlmostEqual(plan.dps, 8)
        self.assertNotEqual(plan.seconds, 1)
    def test_full_animation_duration_does_not_block_periodic_handoff(self):
        plan = evaluate_cycle((option("1", 100, duration=30, actionable=1, cost=0),))
        self.assertAlmostEqual(plan.seconds, 1)
        self.assertAlmostEqual(plan.dps, 100)

    def test_fractional_cost_uses_exact_sp_but_integer_hud_gate(self):
        plan = evaluate_cycle((option("1", 100, cost=25),))
        self.assertAlmostEqual(plan.seconds, 25 / 8)
        self.assertAlmostEqual(plan.dps, 32)
        (profile,) = load_skill_timings().profiles("梨诺", "battle")
        self.assertEqual(profile.sp_cost, 25)
        self.assertEqual(profile.skill_points, 1)

    def test_cooldown_closure_and_resource_recovery_share_time(self):
        plan = evaluate_cycle((option("1", 100, cooldown=20),))
        self.assertEqual(plan.seconds, 20)
        self.assertNotEqual(plan.seconds, 20 + 12.5 + 1)

    def test_unknown_support_value_is_retained_without_inventing_multiplier(self):
        support = option("1", 0, retain=True)
        carry = option("2", 80)
        plan = optimize_cycle((support, carry))
        self.assertEqual(set(plan.slots), {"1", "2"})
        self.assertEqual(plan.damage, 80)
        self.assertEqual(plan.seconds, 25)

    def test_each_consumer_spends_its_attachment(self):
        feeder = option("1", 0, cost=0, produces=frozenset({"自然"}))
        consumer = option("2", 1000, conservative=10, requires=frozenset({"自然"}))
        second = option("3", 1000, conservative=10, requires=frozenset({"自然"}))
        plan = evaluate_cycle((feeder, consumer, second))
        self.assertEqual(plan.damage, 1010)
        self.assertEqual(evaluate_cycle((consumer,)).damage, 10)

    def test_attachment_expires_during_resource_wait(self):
        feeder = option("1", 0, cost=0, produces=frozenset({"自然"}))
        consumer = option("2", 1000, conservative=10, cost=300, requires=frozenset({"自然"}))
        self.assertEqual(evaluate_cycle((feeder, consumer)).damage, 10)

    def test_cross_element_application_consumes_previous_attachment(self):
        natural = option("1", 0, cost=0, produces=frozenset({"自然"}))
        burn = option("2", 0, cost=0, produces=frozenset({"灼热"}))
        consumer = option("3", 1000, conservative=10, requires=frozenset({"自然"}))
        self.assertEqual(evaluate_cycle((natural, burn, consumer)).damage, 10)

    def test_opening_damage_is_separate_from_periodic_damage(self):
        feeder = option("1", 0, cost=0, produces=frozenset({"自然"}))
        consumer = option("2", 1000, conservative=10, requires=frozenset({"自然"}))
        plan = evaluate_cycle((consumer, feeder))
        self.assertEqual(plan.opening_damage, 10)
        self.assertEqual(plan.damage, 1000)

    def test_optimizer_can_select_profitable_feeder_consumer_pair(self):
        feeder = option("1", 1, produces=frozenset({"自然"}))
        consumer = option("2", 1000, conservative=10, requires=frozenset({"自然"}))
        plan = optimize_cycle((consumer, feeder))
        self.assertEqual(plan.slots, ("1", "2"))
        self.assertEqual(plan.damage, 1001)

    def test_no_periodic_solution_for_impossible_or_invalid_cost(self):
        self.assertIsNone(evaluate_cycle((option("1", 100, cost=400),)))
        self.assertIsNone(evaluate_cycle((option("1", 100),), regen=0))
        self.assertIsNone(evaluate_cycle((option("1", float("nan")),)))

    def test_loader_uses_single_skill_not_cycle_bundle_or_full_link4(self):
        row = {
            "character": "Fixture",
            "cycle_expect": 3200,
            "cycle_expect_link4": 3950,
            "cycle_expect_conservative": 2700,
            "full_caliber_requires": {"attach": "自然"},
            "skills": [
                {"type": "战技", "crit_expect": 100, "full_expect": 1000},
                {"type": "连携技", "crit_expect": 1000},
                {"type": "普通攻击", "crit_expect": 600},
                {"type": "终结技", "crit_expect": 2000},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.json"
            path.write_text(json.dumps([row]), encoding="utf-8")
            quote = load_damage_quotes(["Fixture"], path)["Fixture"]
        self.assertEqual(quote.battle, 1000)
        self.assertEqual(quote.conservative, 500)
        self.assertEqual(quote.ult, 2000)
        self.assertEqual(quote.link, 1000)

    def test_missing_state_or_damage_mapping_keeps_legacy_fallback(self):
        store = load_skill_timings()
        self.assertEqual(build_options(["佩丽卡"], store, {}), ())
        team = ["佩丽卡", "弭弗", "狼卫", "管理员"]
        self.assertEqual(build_options(team, store, load_damage_quotes(team)), ())


class TestTimedDpsRuntime(unittest.TestCase):
    def test_ready_ults_rank_by_own_damage_and_time(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.ult_order = ["2", "1", "3", "4"]
        logic.damage_quotes = {
            "佩丽卡": DamageQuote(100, 100, 1_000_000, 1),
            "狼卫": DamageQuote(100, 100, 10, 1),
        }
        task.ults = {"1", "2"}
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])

    def test_failed_cast_does_not_create_a_completed_cycle(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.order = ["1"]
        logic.plan = CyclePlan(("1",), 12.5, 100, 100)
        logic.step()
        task.now = 1
        logic.step()
        self.assertEqual(list(logic.cycle_samples), [])
        self.assertIsNone(logic._cycle_start)

    def test_window_closes_at_next_confirmed_first_cast_and_includes_interruptions(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.order = ["1"]
        logic.plan = CyclePlan(("1",), 12.5, 100, 100)
        logic.damage_quotes = {name: DamageQuote(100, 100, 50, 10) for name in logic.team}
        logic.step()
        task.points, task.now = 0, 0.1
        logic.step()
        logic._observe_bonus("ult", "2")
        logic._observe_bonus("link")
        task.points, task.now = 1, 20
        logic.step()
        task.points, task.now = 0, 20.1
        logic.step()
        self.assertEqual(list(logic.cycle_samples), [(20, 160)])
        self.assertTrue(any("窗口实测，伤害未实测" in message for message in task.messages))

    def test_detected_team_activates_damage_plan_without_old_switches(self):
        task = FakeTask()
        logic = logic_for(task)
        logic._detect_team(1)
        self.assertIsNotNone(logic.plan)
        self.assertEqual(logic.order, list(logic.plan.slots))
        self.assertTrue(logic.order)
