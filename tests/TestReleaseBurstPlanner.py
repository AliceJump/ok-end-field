"""Release gains reach actual scheduler dispatch without the research simulator."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.data.burst_decision_table import BurstDecisionTable, budget_cell, team_path
from src.data.combat_observation import ActionBlockReason
from src.data.normal_attack_window import read_windows
from src.data.release_burst_planner import (
    SNAPSHOT,
    ReleaseBurstAction,
    ReleaseBurstDecision,
    ReleaseBurstPlanner,
    read_snapshot,
)
from src.data.skill_timing import load_skill_timings
from src.data.team_phase_planner import BurstAction, CombatPhase, make_burst_plan
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestYvonneWindowIntegration(unittest.TestCase):
    def planner(self, team=("伊冯",)):
        return ReleaseBurstPlanner(team, store=load_skill_timings())

    def test_reviewed_contract_has_nominal_trace_and_no_research_imports(self):
        model, _, _ = read_windows()
        attacks = model.attacks(0)
        self.assertEqual(len(attacks), 74)
        self.assertEqual([a.layers for a in attacks[:7]], [1, 2, 3, 4, 5, 6, 6])
        self.assertEqual(attacks[-1].kind, "finisher")
        self.assertAlmostEqual(attacks[-1].projected_at, 299/30)
        source = Path("src/data/normal_attack_window.py").read_text(encoding="utf8")
        self.assertNotIn("native_action_program", source)
        self.assertNotIn("combat_simulation", source)

    def test_actual_price_matches_reviewed_window_and_frozen_talent_branch(self):
        p = self.planner()
        p.confirm("1", "ult", 0)
        basic = p.normal_window_quote("1", 0)
        frozen = p.normal_window_quote("1", 0, frozen=True)
        self.assertAlmostEqual(basic.damage, 261824.5464605, places=5)
        self.assertAlmostEqual(frozen.damage, 379237.1067516, places=5)
        self.assertAlmostEqual(frozen.damage-basic.damage, basic.conditional_frozen_damage)
        self.assertEqual(p.price("1", "ult", 3, value=999999), 0)
        short = p.normal_window_quote("1", 0, until=5)
        self.assertLess(short.damage, basic.damage)
        self.assertFalse(any(a.kind == "finisher" for a in short.attacks))

    def test_partial_team_window_prices_per_phase_with_existing_amp_maximum(self):
        p = self.planner(("祀", "伊冯"))
        p.confirm("2", "ult", 0)
        basic = p.normal_window_quote("2", 0).damage
        p._release(p.bonuses, "1", "ult", 0)  # Isolate coverage from an intervening accepted cast's output loss.
        boosted = p.normal_window_quote("2", 0).damage
        self.assertGreater(boosted, basic)
        self.assertAlmostEqual(boosted/basic, 1.3600000143051147)
        p.bonuses = [replace(b, expires_at=4) for b in p.bonuses]
        partial = p.normal_window_quote("2", 0).damage
        self.assertGreater(partial, basic)
        self.assertLess(partial, boosted)

    def test_unknown_main_does_not_claim_current_output_and_future_ult_unlocks_it(self):
        p = self.planner()
        ult = ReleaseBurstAction("1", "ult", 2.283333333333333, 999999)
        decision = p.choose((ult,), 0, 0)
        self.assertEqual(decision.action.kind, "ult")
        self.assertEqual(decision.sequence[:2], (("1", "ult"), ("1", "normal")))
        self.assertGreater(decision.damage, 260000)
        self.assertFalse(p.normal_state.windows)
        p.confirm("1", "ult", 0)
        self.assertIsNone(p.choose((), 0, 3))
        normal = ReleaseBurstAction("1", "normal", p.normal_duration("1", 3), p.quote("1", "normal")["crit_expect"])
        decision = p.choose((normal,), 0, 3, main="1")
        self.assertEqual(decision.action.kind, "normal")

    def test_si_precedes_yvonne_and_marginal_cost_keeps_known_combo_prefix(self):
        p = self.planner(("祀", "伊冯", "狼卫"))
        actions = (ReleaseBurstAction("1", "ult", 3, 0), ReleaseBurstAction("2", "ult", 2.283333333333333, 999999),
                   ReleaseBurstAction("3", "link", .5, 100, queue_position=1),
                   ReleaseBurstAction("1", "link", .5, 20000, queue_position=2))
        decision = p.choose(actions, 0, 0, horizon=16)
        self.assertEqual(decision.sequence[0], ("1", "ult"))
        self.assertIn(("2", "ult"), decision.sequence)
        if ("1", "link") in decision.sequence:
            self.assertLess(decision.sequence.index(("3", "link")), decision.sequence.index(("1", "link")))
        self.assertIsNone(decision.sp_value.damage_per_sp)

    def test_current_main_ordinary_output_is_real_opportunity_cost(self):
        p = self.planner()
        ult = ReleaseBurstAction("1", "ult", 2.283333333333333, 999999)
        normal = ReleaseBurstAction("1", "normal", p.normal_durations["1"], p.quote("1", "normal")["crit_expect"])
        result = p.choose((ult, normal), 0, 0, main="1")
        self.assertEqual(result.action.kind, "ult")
        self.assertGreater(result.gain, 0)
        self.assertLess(result.gain, result.damage)
        huge = replace(normal, value=10000000)
        result = p.choose((ult, huge), 0, 0, main="1")
        self.assertEqual(result.action.kind, "normal")

    def test_long_command_expires_layers_recovery_and_source_cleanup(self):
        p = self.planner(("伊冯", "狼卫"))
        p.confirm("1", "ult", 0, recovered_at=5)
        self.assertEqual(p.normal_state.window("1").input_at, 5)
        p.confirm("2", "ult", 6, recovered_at=12)
        self.assertEqual(p.normal_state.window("1").layers, 0)
        p.source_unavailable("1")
        self.assertFalse(p.normal_state.windows)

    def test_table_replays_window_and_rejects_changed_main_or_timeline(self):
        p = self.planner(("伊冯", "狼卫"))
        actions = (ReleaseBurstAction("1", "ult", 2.283333333333333, 999999),)
        first = p.choose(actions, 0, 0)
        with patch.object(p, "_choose_uncached", side_effect=AssertionError("Repeated exact input should replay")):
            second = p.choose(actions, 0, 0)
        self.assertEqual(second.damage, first.damage)
        p.confirm("1", "ult", 0)
        normal = ReleaseBurstAction("1", "normal", p.normal_duration("1", 3), 1000)
        p.choose((normal,), 0, 3, main="1")
        with patch.object(p, "_choose_uncached", wraps=p._choose_uncached) as search:
            p.choose((normal,), 0, 3.1, main="1")
            self.assertTrue(search.called, "Active window timestamps cannot reuse the old quote")

    def test_all_roster_sizes_and_contract_corruption(self):
        for size in range(1, 5):
            team = ("安塔尔", "佩丽卡", "狼卫")[:size-1] + ("伊冯",)
            p = self.planner(team)
            actor = str(size)
            self.assertEqual(set(p.window_models), {actor})
            decision = p.choose((ReleaseBurstAction(actor, "ult", 2.283333333333333, 0),), 0, 0)
            self.assertEqual(decision.action.slot, actor)
        from src.data.normal_attack_window import SNAPSHOT as WINDOW_SNAPSHOT
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "index.json").write_bytes((WINDOW_SNAPSHOT / "index.json").read_bytes())
            (path / "windows.json").write_bytes((WINDOW_SNAPSHOT / "windows.json").read_bytes()+b" ")
            with self.assertRaisesRegex(ValueError, "hash"):
                read_windows(path)

    def test_real_step_confirms_window_and_keeps_held_normal_choice(self):
        task = FakeTask()
        task.sp = 0
        task.ults = {"1"}
        task.detect_current_char_index = lambda: 0
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["伊冯"], reset_runtime=True)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertIsNotNone(logic.release_burst.normal_state.window("1"))
        task.now = 3
        logic.step()
        self.assertTrue(logic._holding)
        self.assertEqual(task.keys, ["ult_1"])


class TestReleaseBurstPlanner(unittest.TestCase):
    def test_export_scope_and_selected_builds(self):
        rows = read_snapshot()
        self.assertEqual(len(rows), 33)
        self.assertEqual(sum(len(r["rules"]) for r in rows.values()), 16)
        self.assertEqual(len([r for r in rows.values() if r["rules"]]), 10)
        self.assertEqual(rows["管理员"]["profile"]["potential"], 3)
        self.assertEqual(rows["余烬"]["profile"]["character_level"], 80)
        self.assertEqual(rows["余烬"]["profile"]["skill_rank"], 9)
        for name in ("洁尔佩塔", "噗切娜", "伊冯"):
            self.assertFalse(rows[name]["rules"])
        for row in rows.values():
            self.assertEqual(row["quotes"]["link"]["quote_basis"]["damage_tags"], ["combo"])

    def test_si_team_amplification_starts_at_verified_child_frame_and_filters_elements(self):
        planner = ReleaseBurstPlanner(["祀", "伊冯", "狼卫", "佩丽卡"])
        before = [planner.price(actor, "ult", 0) for actor in ("2", "3", "4")]
        planner.confirm("1", "ult", 0)
        starts, ends = 62 / 30, 62 / 30 + 12
        for actor, basic in zip(("2", "3", "4"), before):
            self.assertEqual(planner.price(actor, "ult", starts - .001), basic)
            self.assertAlmostEqual(planner.price(actor, "ult", starts) / basic,
                                   1 if actor == "4" else 1.3600000143051147)
            self.assertEqual(planner.price(actor, "ult", ends), basic)
        own = planner.price("1", "battle", 0)
        self.assertAlmostEqual(planner.price("1", "battle", starts) / own, 1.3600000143051147)

    def test_si_release_refresh_is_timed_and_not_cancelled_by_her_next_action(self):
        planner = ReleaseBurstPlanner(["祀", "伊冯"])
        before = planner.price("2", "ult", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(len(planner.bonuses), 4)
        planner.confirm("1", "battle", 3)
        planner.source_unavailable("1")
        self.assertGreater(planner.price("2", "ult", 4), before)
        planner.confirm("1", "ult", 10)
        self.assertEqual(len(planner.bonuses), 4)
        self.assertGreater(planner.price("2", "ult", 20), before)
        self.assertEqual(planner.price("2", "ult", 25), before)

    def test_si_zero_sp_support_opener_changes_actual_sequence_without_entity_damage(self):
        planner = ReleaseBurstPlanner(["祀", "伊冯"])
        actions = (ReleaseBurstAction("1", "ult", 3, 999999), ReleaseBurstAction("2", "ult", 2, 1000))
        chosen = planner.choose(actions, 0, 0)
        self.assertEqual(chosen.sequence, (("1", "ult"), ("2", "ult")))
        self.assertAlmostEqual(chosen.damage, 1360.0000143051147)
        self.assertFalse(planner.bonuses)
        self.assertEqual(planner.price("1", "ult", 3, value=999999), 0)
        self.assertIsNone(planner.choose(actions[:1], 0, 0))
        wrong = ReleaseBurstPlanner(["祀", "陈千语"])
        self.assertIsNone(wrong.choose(actions, 0, 0))
        self.assertIsNone(planner.choose((actions[0], ReleaseBurstAction("2", "battle", 1, 1000, 100, 100)), 99, 0))

    def test_si_and_xaihi_amp_share_one_bucket_without_duplicate_fixed_equipment(self):
        planner = ReleaseBurstPlanner(["祀", "赛希", "伊冯"])
        basic = planner.price("3", "ult", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("2", "ult", 0)
        self.assertAlmostEqual(planner.price("3", "ult", 3) / basic, 1 + max(.36000001430511475, .38478), places=7)

    def test_changed_snapshot_fails_hash_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "index.json").write_bytes((SNAPSHOT / "index.json").read_bytes())
            (path / "support-evidence.json").write_bytes((SNAPSHOT / "support-evidence.json").read_bytes())
            (path / "snapshot.json").write_bytes((SNAPSHOT / "snapshot.json").read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                read_snapshot(path)

    def test_unknown_input_rule_is_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = json.loads((SNAPSHOT / "snapshot.json").read_text(encoding="utf8"))
            rule = next(r["rules"][0] for r in data["actors"] if r["rules"])
            rule["magnitude"]["terms"] = [{"input": "unknown", "coefficient": 1}]
            payload = json.dumps(data).encode()
            manifest = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf8"))
            manifest["snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
            (path / "snapshot.json").write_bytes(payload)
            (path / "support-evidence.json").write_bytes((SNAPSHOT / "support-evidence.json").read_bytes())
            (path / "index.json").write_text(json.dumps(manifest), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "Unconfirmed"):
                read_snapshot(path)

    def test_zero_direct_support_precedes_carry_and_prediction_is_isolated(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0), ReleaseBurstAction("2", "ult", 1, 1000),
                   ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        chosen = planner.choose(actions, 100, 0)
        self.assertEqual((chosen.action.slot, chosen.action.kind), ("1", "ult"))
        self.assertAlmostEqual(chosen.damage, 2000 * (1 + .22000000476837158))
        self.assertAlmostEqual(chosen.gain, 2000 * .22000000476837158)
        self.assertFalse(planner.bonuses)

    def test_wrong_element_budget_and_expired_window_do_not_promote_support(self):
        planner = ReleaseBurstPlanner(["安塔尔", "陈千语"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0), ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        self.assertIsNone(planner.choose(actions, 100, 0))
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        self.assertIsNone(planner.choose(actions, 99, 0))
        late = (actions[0], ReleaseBurstAction("2", "battle", 12, 1000, 100, 100))
        self.assertIsNone(planner.choose(late, 100, 0, horizon=20))

    def test_source_refresh_duplicates_expiry_and_personal_filters(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫"])
        before = planner.price("1", "battle", 0)
        ally = planner.price("2", "battle", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(len(planner.bonuses), 1)
        self.assertGreater(planner.price("1", "battle", 1), before)
        self.assertEqual(planner.price("2", "battle", 1), ally)
        planner.confirm("1", "ult", 10)
        self.assertEqual(len(planner.bonuses), 1)
        self.assertGreater(planner.price("1", "battle", 15), before)
        self.assertEqual(planner.price("1", "battle", 25), before)
        zhuang = ReleaseBurstPlanner(["庄方宜"])
        ult, battle = zhuang.price("1", "ult", 0), zhuang.price("1", "battle", 0)
        zhuang.confirm("1", "ult", 0)
        self.assertEqual(zhuang.price("1", "ult", 1), ult)
        self.assertGreater(zhuang.price("1", "battle", 1), battle)

    def test_normal_only_bonus_changes_choice_without_leaking_to_battle(self):
        planner = ReleaseBurstPlanner(["莱万汀"])
        ult, normal = ReleaseBurstAction("1", "ult", 1, 1000), ReleaseBurstAction("1", "normal", 3, 1000)
        self.assertIsNone(planner.choose((ult,), 100, 0))
        self.assertEqual(planner.choose((ult, normal), 100, 0).action.kind, "ult")
        before = planner.price("1", "battle", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.price("1", "battle", 1), before)

    def test_fixed_equipment_bonus_is_not_counted_twice_in_dynamic_ratio(self):
        planner = ReleaseBurstPlanner(["艾维文娜"])
        quote = planner.quote("1", "battle")
        before = planner.price("1", "battle", 0)
        self.assertAlmostEqual(quote["bonus_pct"], 174.1)
        planner.confirm("1", "battle", 0)
        self.assertAlmostEqual(planner.price("1", "battle", 1) / before,
                               1 + .336 / (1 + 174.1 / 100))

    def test_avywenna_quote_separates_unobserved_return_hits_from_whirlwind(self):
        quote = read_snapshot()["艾维文娜"]["quotes"]["battle"]
        self.assertEqual(quote["quote_basis"]["multiplier"], 1.5)
        self.assertAlmostEqual(quote["crit_expect"], 19646.120878135916)
        normal, strong = quote["deferred_components"]
        self.assertAlmostEqual(normal["multiplier"], 1.68, places=6)
        self.assertAlmostEqual(strong["multiplier"], 4.32, places=6)
        for component in (normal, strong):
            self.assertIsNone(component["actual_count"])
            self.assertFalse(component["included_in_quote"])
            self.assertIn("return_projectile_path_hit", component["requires"])

    def test_avywenna_legacy_full_quote_cannot_reenter_price_after_buffs(self):
        planner = ReleaseBurstPlanner(["艾维文娜", "梨诺"])
        base = planner.quote("1", "battle")["crit_expect"]
        self.assertEqual(planner.price("1", "battle", 0, value=98230.6), base)
        planner.confirm("1", "battle", 0)
        planner.confirm("2", "ult", 0)
        self.assertEqual(planner.price("1", "battle", 3, value=98230.6), planner.price("1", "battle", 3))
        self.assertGreater(planner.price("1", "battle", 3), base)

    def test_avywenna_unknown_recall_does_not_lose_unbuffed_winner_to_legacy_fallback(self):
        planner = ReleaseBurstPlanner(["艾维文娜", "佩丽卡"])
        actions = (ReleaseBurstAction("1", "battle", 1, 98230.6, 100, 100),
                   ReleaseBurstAction("2", "battle", 1, planner.quote("2", "battle")["crit_expect"], 100, 100))
        decision = planner.choose(actions, 100, 0)
        self.assertEqual((decision.action.slot, decision.action.kind), ("2", "battle"))
        self.assertEqual(decision.sequence, (("2", "battle"),))
        self.assertEqual(decision.gain, 0)
        self.assertFalse(planner.last_confirmed)
        self.assertFalse(planner.bonuses)

    def test_avywenna_link_and_ultimate_acceptance_do_not_invent_field_counts(self):
        planner = ReleaseBurstPlanner(["艾维文娜"])
        base = planner.price("1", "battle", 0)
        planner.confirm("1", "link", 0)
        planner.confirm("1", "ult", 1)
        self.assertGreater(planner.price("1", "battle", 2), base)
        self.assertIsNone(planner.mechanism_quote('1', 2).normal_spears)
        self.assertIsNone(planner.mechanism_quote('1', 2).strong_spears)
        self.assertEqual(planner.mechanism_quote('1', 2).tracked_bone_stacks, 1)
        self.assertTrue(all(c["actual_count"] is None for c in planner.quote("1", "battle")["deferred_components"]))

    def test_attack_window_uses_white_attack_and_source_action_cleanup(self):
        planner = ReleaseBurstPlanner(["秋栗", "狼卫"])
        panel = planner.rows["2"]["panel"]
        before = planner.price("2", "battle", 0)
        planner.confirm("1", "ult", 0)
        expected = 1 + panel["attack_white"] * .1 / (panel["attack_white"] * (1 + panel["attack_percent"]) + panel["attack_flat"])
        self.assertAlmostEqual(planner.price("2", "battle", 1) / before, expected, places=7)
        self.assertEqual(planner.price("2", "battle", 5), before)
        planner.confirm("1", "ult", 10)
        planner.confirm("2", "battle", 11)
        self.assertGreater(planner.price("2", "battle", 11), before)
        planner.confirm("1", "battle", 12)
        self.assertEqual(planner.price("2", "battle", 12), before)

    def test_late_portrait_preserves_the_accepted_team_window(self):
        previous = ReleaseBurstPlanner(["安塔尔", "?"])
        previous.confirm("1", "ult", 0)
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        planner.preserve(previous)
        base = planner.quote("2", "battle")["crit_expect"]
        self.assertAlmostEqual(planner.price("2", "battle", 1) / base, 1.22, places=5)
        self.assertEqual(planner.price("2", "battle", 12), base)

    def test_same_actor_lock_does_not_delay_team_beneficiaries(self):
        planner = ReleaseBurstPlanner(["佩丽卡"])
        actions = (ReleaseBurstAction("1", "ult", 1, 100, same_actor_duration=20),
                   ReleaseBurstAction("1", "battle", 1, 1000, 100, 100))
        self.assertEqual(planner.choose(actions, 100, 0, horizon=25).sequence, (("1", "battle"), ("1", "ult")))
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0, same_actor_duration=20),
                   ReleaseBurstAction("2", "battle", 1, 1000, 100, 100))
        self.assertEqual(planner.choose(actions, 100, 0).action.slot, "1")

    def test_refund_cap_cannot_invent_an_affordable_beneficiary(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        actions = (ReleaseBurstAction("1", "ult", 1, 0, sp_cost=-100),
                   ReleaseBurstAction("2", "battle", 1, 1000, 350, 100))
        self.assertIsNone(planner.choose(actions, 300, 0))

    def test_support_strength_uses_rank_potential_and_unrounded_fixed_attribute(self):
        rows = read_snapshot()
        xai = rows["赛希"]["rules"][0]
        liino = next(r for r in rows["梨诺"]["rules"] if r["bucket"] == "amplification")
        evidence = {r["character"]: r for r in json.loads((SNAPSHOT / "support-evidence.json").read_text(encoding="utf8"))
                    if r['skill'].endswith('_ultimate_skill')}
        self.assertEqual(evidence["赛希"]["input_domain"], "source.native.final_nonconverted.41")
        self.assertEqual(evidence["梨诺"]["input_domain"], "source.native.final_nonconverted.42")
        self.assertEqual(evidence["赛希"]["input_value"], 366)
        self.assertEqual(evidence["梨诺"]["input_value"], 890.838)
        self.assertAlmostEqual(xai["magnitude"]["base"], (.24 + .0003 * 366) * 1.1, places=7)
        self.assertAlmostEqual(liino["magnitude"]["base"], .0004 * 890.838, places=7)
        for name in ("赛希", "梨诺"):
            block = evidence[name]
            native = load_skill_timings().record(block["skill"])
            self.assertEqual(native["source"]["sha256"], block["native_record_sha256"][block["skill"]])
            self.assertIn("without_dynamic_four_stats", block["input_policy"])
            self.assertEqual(block["profile"]["potential"], 5 if name == "赛希" else 0)

    def test_xaihi_release_delay_element_filter_and_full_duration(self):
        planner = ReleaseBurstPlanner(["赛希", "伊冯", "佩丽卡"])
        before = planner.price("2", "ult", 0)
        wrong = planner.price("3", "battle", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.price("2", "ult", 1), before)
        self.assertGreater(planner.price("2", "ult", 2), before)
        self.assertEqual(planner.price("3", "battle", 2), wrong)
        self.assertGreater(planner.price("2", "ult", 13), before)
        self.assertEqual(planner.price("2", "ult", 14), before)

    def test_xaihi_zero_damage_opener_is_selected_before_cold_carry(self):
        planner = ReleaseBurstPlanner(["赛希", "伊冯"])
        actions = (ReleaseBurstAction("1", "ult", 3, 0), ReleaseBurstAction("2", "ult", 2, 1000))
        chosen = planner.choose(actions, 0, 0)
        self.assertEqual(chosen.sequence, (("1", "ult"), ("2", "ult")))
        self.assertGreater(chosen.damage, 1380)
        self.assertFalse(planner.bonuses)

    def test_liino_team_attack_combines_with_only_matching_element_amp(self):
        planner = ReleaseBurstPlanner(["梨诺", "佩丽卡", "伊冯", "陈千语"])
        before = [planner.price(a, "battle", 0) for a in ("2", "3", "4")]
        planner.confirm("1", "ult", 0)
        ratios = [planner.price(a, "battle", 3) / value for a, value in zip(("2", "3", "4"), before)]
        self.assertGreater(ratios[0], 1.35)
        self.assertGreater(ratios[1], 1)
        self.assertLess(ratios[1], 1.2)
        self.assertGreater(ratios[2], 1)
        self.assertLess(ratios[2], 1.2)
        self.assertEqual(planner.price("2", "battle", 2), before[0])

    def test_liino_singing_starts_after_half_second_and_prices_white_attack_for_sixty_seconds(self):
        planner = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        before = planner.price('2', 'battle', 0)
        panel = planner.rows['2']['panel']
        ratio = 1 + panel['attack_white'] * planner.rules['1', 'battle'][0]['magnitude']['base'] / (
            panel['attack_white'] * (1 + panel['attack_percent']) + panel['attack_flat'])
        planner.confirm('1', 'battle', 0)
        self.assertEqual(planner.price('2', 'battle', .499), before)
        for now in (.5, 30, 60.499):
            self.assertAlmostEqual(planner.price('2', 'battle', now), before * ratio)
        self.assertEqual(planner.price('2', 'battle', 60.5), before)
        self.assertTrue(planner.action_blocked('1', 'battle', 30))
        self.assertFalse(planner.action_blocked('2', 'battle', 30))
        self.assertFalse(planner.action_blocked('1', 'battle', 60.5))

    def test_liino_singing_replaces_ultimate_attack_instead_of_stacking_twice(self):
        planner = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        base = planner.price('2', 'battle', 0)
        planner.confirm('1', 'battle', 0)
        planner.confirm('1', 'battle', 0)
        singing = planner.price('2', 'battle', 1)
        self.assertEqual(len(planner.bonuses), 2)
        planner.confirm('1', 'ult', 2)
        self.assertEqual(planner.price('2', 'battle', 2.1), base)
        self.assertEqual(sum(b.spec['bucket'] == 'attack' for b in planner.bonuses), 2)
        self.assertGreater(planner.price('2', 'battle', 5), singing)
        planner.source_unavailable('1')
        self.assertEqual(planner.price('2', 'battle', 5), base)

    def test_liino_singing_changes_burst_order_only_with_budget_for_the_beneficiary(self):
        planner = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        actions = (ReleaseBurstAction('1', 'battle', .55, 0, 25, 25),
                   ReleaseBurstAction('2', 'battle', .5, planner.quote('2', 'battle')['crit_expect'], 100, 100))
        decision = planner.choose(actions, 125, 0)
        self.assertEqual(decision.sequence, (('1', 'battle'), ('2', 'battle')))
        self.assertGreater(decision.gain, 0)
        self.assertFalse(planner.bonuses)  # Forecasting does not activate the real aura.
        under_budget = planner.choose(actions, 124, 0)
        self.assertEqual(under_budget.sequence, (('2', 'battle'),))
        self.assertEqual(under_budget.gain, 0)
        self.assertEqual(planner.price('1', 'battle', 0, value=17320.2), 0)

    def test_liino_combo_preserves_singing_without_refreshing_its_deadline(self):
        planner = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        base = planner.price('2', 'battle', 0)
        planner.confirm('1', 'battle', 0)
        boosted = planner.price('2', 'battle', 1)
        planner.confirm('1', 'link', 2)
        self.assertGreater(boosted, base)
        self.assertEqual(planner.price('2', 'battle', 3), boosted)
        self.assertEqual(planner.price('2', 'battle', 60.5), base)
        self.assertTrue(planner.action_blocked('1', 'battle', 3))

    def test_liino_combo_forecast_keeps_team_singing_but_does_not_mutate_it(self):
        planner = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        planner.confirm('1', 'battle', 0)
        before = tuple(planner.bonuses)
        link = ReleaseBurstAction('1', 'link', .4, 100, queue_position=1)
        battle = ReleaseBurstAction('2', 'battle', .5, planner.quote('2', 'battle')['crit_expect'], 100, 100)
        chosen = planner.choose((link, battle), 100, 1)
        self.assertIsNotNone(chosen)
        self.assertGreater(chosen.damage, link.value + battle.value)
        self.assertEqual(tuple(planner.bonuses), before)

    def test_liino_channel_blocks_cancel_and_normal_but_not_teammates(self):
        planner = ReleaseBurstPlanner(["梨诺", "佩丽卡"])
        opener = ReleaseBurstAction("1", "ult", 3, 0, same_actor_duration=18.25)
        cancel = ReleaseBurstAction("1", "battle", 1, 100000, 25, 25)
        carry = ReleaseBurstAction("2", "ult", 2, 1000)
        chosen = planner.choose((opener, carry), 0, 0)
        self.assertEqual(chosen.action, opener)
        planner.confirm("1", "ult", 0)
        self.assertTrue(planner.action_blocked("1", "battle", 1))
        self.assertTrue(planner.action_blocked("1", "normal", 3))
        self.assertFalse(planner.action_blocked("2", "battle", 3))
        chosen = planner.choose((cancel, carry), 25, 3)
        self.assertNotIn(("1", "battle"), chosen.sequence)
        self.assertFalse(planner.action_blocked("1", "battle", 18))

    def test_liino_source_cancel_refresh_and_unavailable_cleanup(self):
        planner = ReleaseBurstPlanner(["梨诺", "佩丽卡"])
        before = planner.price("2", "battle", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(len(planner.bonuses), 6)
        planner.confirm("2", "battle", 3)
        self.assertGreater(planner.price("2", "battle", 4), before)
        planner.confirm("1", "battle", 4)
        self.assertEqual(planner.price("2", "battle", 4), before)
        planner.confirm("1", "ult", 20)
        self.assertGreater(planner.price("2", "battle", 23), before)
        planner.source_unavailable("1")
        self.assertEqual(planner.price("2", "battle", 23), before)

    def test_liino_ongoing_and_finish_damage_not_instantly_quoted(self):
        planner = ReleaseBurstPlanner(["梨诺"])
        quote = planner.quote("1", "ult")
        self.assertEqual(quote["crit_expect"], 0)
        self.assertIn("not_forecast", quote["pricing_scope"])
        self.assertEqual(planner.price("1", "ult", 0, value=49073.9), 0)
        self.assertIsNone(planner.choose((ReleaseBurstAction("1", "ult", 3, 0),), 0, 0))

    def test_two_supports_select_matching_amp_and_refresh_without_duplicate_layers(self):
        planner = ReleaseBurstPlanner(["梨诺", "安塔尔", "佩丽卡"])
        before = planner.price("3", "battle", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("2", "ult", 0)
        panel = planner.rows["3"]["panel"]
        atk = panel["attack_white"] * (1 + panel["attack_percent"]) + panel["attack_flat"]
        atk_up = planner.rules["1", "ult"][0]["magnitude"]["base"]
        amp = max(s["magnitude"]["base"] for a in ("1", "2") for s in planner.rules[a, "ult"]
                  if s["bucket"] == "amplification" and "电磁" in s["elements"])
        self.assertAlmostEqual(planner.price("3", "battle", 3) / before,
                               (1 + panel["attack_white"] * atk_up / atk) * (1 + amp))
        planner.confirm("1", "ult", 5)
        self.assertAlmostEqual(planner.price("3", "battle", 8) / before,
                               (1 + panel["attack_white"] * atk_up / atk) * (1 + amp))

    def test_support_evidence_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ("index.json", "snapshot.json", "support-evidence.json"):
                (path / name).write_bytes((SNAPSHOT / name).read_bytes())
            (path / "support-evidence.json").write_bytes(b"[]")
            with self.assertRaisesRegex(ValueError, "Support evidence"):
                read_snapshot(path)

    def test_nature_beneficiary_selects_top_amp_and_keeps_the_attack_window(self):
        planner = ReleaseBurstPlanner(["赛希", "梨诺", "艾尔黛拉"])
        before = planner.price("3", "normal", 0)
        planner.confirm("1", "ult", 0)
        planner.confirm("2", "ult", 0)
        self.assertGreater(planner.price("3", "normal", 3) / before, 1.5)
        self.assertLess(planner.price("3", "normal", 3) / before, 1.6)
        planner.confirm("2", "battle", 4)
        panel = planner.rows['3']['panel']
        attack = panel['attack_white'] * (1 + panel['attack_percent']) + panel['attack_flat']
        singing = planner.rules['2', 'battle'][0]['magnitude']['base']
        self.assertAlmostEqual(planner.price("3", "normal", 5) / before,
                               (1 + .38478) * (1 + panel['attack_white'] * singing / attack), places=7)

    def test_zhuang_stance_replaces_base_multipliers_and_preserves_bonus_buckets(self):
        planner = ReleaseBurstPlanner(["庄方宜", "安塔尔"], store=load_skill_timings())
        spec = planner.stance_spec("1")
        self.assertAlmostEqual(spec["quotes"]["normal"]["quote_basis"]["multiplier"], 6.6, places=6)
        self.assertAlmostEqual(spec["quotes"]["battle"]["quote_basis"]["multiplier"], .81, places=6)
        self.assertIn("without_sword_count", spec["quotes"]["battle"]["quote_basis"]["scope"])
        basic = planner.price("1", "normal", 0)
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.price("1", "normal", 2.59), basic)
        enhanced = planner.price("1", "normal", 3, value=1)
        self.assertAlmostEqual(enhanced, spec["quotes"]["normal"]["crit_expect"])
        self.assertGreater(enhanced / basic, 1.77)
        self.assertAlmostEqual(planner.price("1", "battle", 3), 211550.8189915943, places=4)
        planner.confirm("2", "ult", 3)
        self.assertGreater(planner.price("1", "normal", 4), enhanced)

    def test_zhuang_stance_first_free_is_consumed_once_and_not_reopened_by_duplicate(self):
        planner = ReleaseBurstPlanner(["庄方宜"])
        planner.confirm("1", "ult", 0)
        self.assertTrue(planner.stance_at("1", 3).free_battle)
        planner.confirm("1", "battle", 3)
        self.assertFalse(planner.stance_at("1", 4).free_battle)
        planner.confirm("1", "ult", 0)
        self.assertFalse(planner.stance_at("1", 4).free_battle)
        planner.confirm("1", "ult", 20)
        self.assertTrue(planner.stance_at("1", 23).free_battle)

    def test_zhuang_stance_expiry_cleans_strength_and_unused_free_charge(self):
        planner = ReleaseBurstPlanner(["庄方宜"])
        basic = planner.price("1", "normal", 0)
        planner.confirm("1", "ult", 0)
        self.assertIsNotNone(planner.stance_at("1", 27.59))
        self.assertIsNone(planner.stance_at("1", 27.6))
        # The independent weapon still runs from cast to 25; do not conflate lifetimes.
        self.assertEqual(planner.price("1", "normal", 27.6), basic)
        planner.source_unavailable("1")
        self.assertFalse(planner.stances)

    def test_zhuang_zero_sp_forecast_promotes_ultimate_then_free_battle(self):
        planner = ReleaseBurstPlanner(["庄方宜"])
        ult = ReleaseBurstAction("1", "ult", 3.15, 0, same_actor_duration=3.15)
        battle = ReleaseBurstAction("1", "battle", 4.65, planner.quote("1", "battle")["crit_expect"], 100, 100)
        chosen = planner.choose((ult, battle), 0, 0)
        self.assertEqual(chosen.sequence, (("1", "ult"), ("1", "battle")))
        self.assertGreater(chosen.damage, planner.quote("1", "battle")["crit_expect"])
        self.assertFalse(planner.stances)
        self.assertIsNone(planner.choose((battle,), 0, 0))
        planner.confirm("1", "ult", 0)
        self.assertEqual(planner.choose((battle,), 0, 3).action.kind, "battle")
        planner.confirm("1", "battle", 3)
        self.assertIsNone(planner.choose((battle,), 0, 4))

    def test_zhuang_replacement_normal_timing_makes_ultimate_a_real_burst_candidate(self):
        planner = ReleaseBurstPlanner(["庄方宜"], store=load_skill_timings())
        normal = ReleaseBurstAction("1", "normal", planner.normal_durations["1"], planner.quote("1", "normal")["crit_expect"])
        ult = ReleaseBurstAction("1", "ult", 3.15, 0, same_actor_duration=3.15)
        chosen = planner.choose((ult, normal), 0, 0)
        self.assertEqual(chosen.action.kind, "ult")
        self.assertIn(("1", "normal"), chosen.sequence)
        planner.confirm("1", "ult", 0)
        self.assertLess(planner.normal_duration("1", 3), 4)
        self.assertGreater(planner.normal_duration("1", 27.6), 4)

    def test_zhuang_forecast_refuses_complete_enhanced_chain_across_expiry(self):
        planner = ReleaseBurstPlanner(["庄方宜"])
        planner.confirm("1", "ult", 0)
        normal = ReleaseBurstAction("1", "normal", 1, 100)
        self.assertIsNone(planner.choose((normal,), 0, 26))
        self.assertTrue(planner.stance_at("1", 26).free_battle)

    def test_zhuang_free_full_salvo_cannot_fit_the_last_seconds_of_its_stance(self):
        planner = ReleaseBurstPlanner(["庄方宜"])
        planner.confirm("1", "ult", 0)
        battle = ReleaseBurstAction("1", "battle", 4.65, planner.quote("1", "battle")["crit_expect"], 100, 100)
        chosen = planner.choose((battle,), 0, 26)
        self.assertIsNone(chosen)
        self.assertIsNotNone(planner.stance_at('1', 26))  # Still a stance, but no complete salvo fits.

    def test_zhuang_preserve_keeps_only_unchanged_actor_stance(self):
        planner = ReleaseBurstPlanner(["庄方宜", "?"])
        planner.confirm("1", "ult", 0)
        filled = ReleaseBurstPlanner(["庄方宜", "安塔尔"])
        filled.preserve(planner)
        self.assertTrue(filled.stance_at("1", 3).free_battle)
        changed = ReleaseBurstPlanner(["安塔尔", "庄方宜"])
        changed.preserve(planner)
        self.assertFalse(changed.stances)


class TestReleaseBurstDispatch(unittest.TestCase):
    def make_logic(self):
        task = FakeTask()
        task.ults = {"1", "2"}
        task.sp = 300
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["安塔尔", "莱万汀", "佩丽卡", "狼卫"], reset_runtime=True)
        return task, logic

    def test_liino_singing_is_selected_by_real_step_using_existing_low_cost_acceptance(self):
        task, logic = self.make_logic()
        logic._configure_team(['梨诺', '佩丽卡'], reset_runtime=True)
        self.assertEqual(logic.damage_quotes['梨诺'].battle, 0)
        self.assertEqual(logic.damage_quotes['梨诺'].conservative, 0)
        self.assertEqual(logic._overflow_score('1'), 0)
        task.ults = set()
        task.sp = 125
        logic.step()
        self.assertEqual(task.keys, ['1'])
        # Existing <=35SP handling accepts after its failure-feedback probe;
        # this change does not introduce stronger release-success recognition.
        self.assertIsNone(logic.pending)
        self.assertTrue(logic.release_burst.bonuses)
        task.sp = 100
        self.assertEqual(logic.release_burst.last_confirmed['1', 'battle'], .05)
        task.now = 2
        logic.step()
        self.assertEqual(task.keys, ['1', '2'])
        self.assertGreater(logic.release_burst.price('2', 'battle', 2),
                           logic.release_burst.quote('2', 'battle')['crit_expect'])
        self.assertIsNone(logic._release_battle_candidate('1', 100, 2, set()))

    def test_liino_failure_feedback_does_not_activate_the_singing_window(self):
        task, logic = self.make_logic()
        logic._configure_team(['梨诺', '佩丽卡'], reset_runtime=True)
        task.ults = set()
        task.sp = 125
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        logic.step()
        self.assertFalse(logic.release_burst.bonuses)
        self.assertFalse(logic.release_burst.last_confirmed)
        self.assertIsNone(logic.pending)

    def test_observed_electric_team_prices_minimum_untyped_recall_without_inventing_three_spears(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "艾维文娜", "梨诺"], reset_runtime=True)
        task.ults = set()
        task.sp = 100
        task.battle_pulses = {"3"}  # An entity/distance hint is not a typed count or path hit.
        logic.step()
        self.assertEqual(task.keys, ["3"])
        self.assertEqual(logic.pending[1], "3")
        self.assertTrue(logic._attempt_mechanism.minimum_recall)
        self.assertIsNone(logic._attempt_mechanism.normal_spears)
        self.assertIsNone(logic._attempt_mechanism.strong_spears)
        self.assertFalse(logic.release_burst.last_confirmed)

    def test_avywenna_cycle_and_overflow_use_the_same_cast_only_quote(self):
        task, logic = self.make_logic()
        logic._configure_team(["艾维文娜", "佩丽卡"], reset_runtime=True)
        value = logic.release_burst.quote("1", "battle")["crit_expect"]
        self.assertEqual(logic.damage_quotes["艾维文娜"].battle, value)
        self.assertEqual(logic.damage_quotes["艾维文娜"].conservative, value)
        profile = logic.store.profiles("艾维文娜", "battle")[0]
        # The attempted battle opens its own JET window; a previous battle
        # only refreshes the same window and must not add a second copy.
        self.assertAlmostEqual(logic._overflow_score("1") * max(profile.handoff, .3), 22054.401292237948)
        logic.release_burst.confirm("1", "battle", 0)
        self.assertAlmostEqual(logic._overflow_score("1") * max(profile.handoff, .3), 22054.401292237948)

    def test_cast_only_comparison_preserves_available_zero_damage_support_in_cycle(self):
        task, logic = self.make_logic()
        logic._configure_team(["赛希", "艾维文娜", "佩丽卡"], reset_runtime=True)
        logic.order = ["1", "2", "3"]
        logic.cursor = 0
        task.sp = 100
        task.ults = set()
        self.assertTrue(logic.damage_quotes["赛希"].retain)
        self.assertEqual(logic.release_burst.quote("1", "battle")["crit_expect"], 0)
        # Previously a positive battle (or normal) won each step without moving
        # the old cursor, permanently skipping this available support.
        for main in (None, 1):
            with self.subTest(main=main):
                logic._burst_main_control = str(main + 1) if main is not None else None
                self.assertIsNone(logic._release_burst_decision([], 100, task.now))
        logic.cursor = 1  # Support later in the old cycle must also remain reachable.
        self.assertIsNone(logic._release_burst_decision([], 100, task.now))
        logic.cursor = 0
        logic.step()
        self.assertEqual(task.keys, ["1"])
        task.sp = 0
        task.now += .3
        logic._confirm_battle(task.now)
        self.assertEqual(logic.cursor, 1)

    def test_unavailable_retained_support_does_not_block_corrected_damage_comparison(self):
        task, logic = self.make_logic()
        logic._configure_team(["赛希", "艾维文娜", "佩丽卡"], reset_runtime=True)
        logic.order = ["1", "2", "3"]
        logic.cursor = 0
        logic.state_until["1"] = 10
        task.sp = 100
        task.ults = set()
        logic.step()
        self.assertEqual(task.keys, ["3"])

    def test_si_real_step_selects_team_ultimate_before_cold_carry_and_no_public_sp(self):
        task, logic = self.make_logic()
        logic._configure_team(["祀", "别礼", "埃特拉", "诀"], reset_runtime=True)
        task.sp = 0
        before = logic.release_burst.price("2", "ult", 0)
        decision = logic._release_burst_decision(logic._ready_ultimate_actions(), 0, task.now)
        self.assertEqual(decision.action.slot, "1")
        self.assertGreater(decision.gain, 0)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertEqual(task.sp, 0)
        task.now = 3.1
        self.assertAlmostEqual(logic.release_burst.price("2", "ult", task.now) / before, 1.3600000143051147)
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

    def test_si_rejected_cast_or_feedback_does_not_publish_bonuses(self):
        for feedback in (False, True):
            with self.subTest(feedback=feedback):
                task, logic = self.make_logic()
                logic._configure_team(["祀", "别礼", "埃特拉", "诀"], reset_runtime=True)
                if feedback:
                    task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
                else:
                    task.use_ult = lambda **kwargs: False
                logic._use_timed_ultimate("1", logic.store.profiles("祀", "ult"), burst_selected=True)
                self.assertFalse(logic.release_burst.bonuses)
                self.assertFalse(logic.release_burst.last_confirmed)

    def test_si_repeat_guard_survives_rebase_and_excludes_only_battle_candidates(self):
        task, logic = self.make_logic()
        logic._configure_team(["祀", "别礼", "埃特拉", "诀"], reset_runtime=True)
        task.ults = {"1"}
        task.sp = 100
        self.assertTrue(logic._try_battle_token("1", 100))
        task.sp = 0
        task.now = .2
        logic._confirm_battle(task.now)
        self.assertAlmostEqual(logic.battle_retry_after["1"], logic.started + 25.6)
        task.now = 3
        self.assertIsNone(logic._release_battle_candidate("1", 100, task.now, set()))
        self.assertTrue(logic._ready_ultimate_actions())

    def test_real_step_inserts_support_before_existing_burst(self):
        task, logic = self.make_logic()
        plan = make_burst_plan("existing", (BurstAction("2", "莱万汀", "battle", "焚灭", sp_gate=100, sp_cost=100),),
                               owner_slot="2", runtime_executable=True)
        logic.phase_planner.configure((plan,))
        before = logic.release_burst.price("2", "ult", 0)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertEqual(logic.phase_planner.state.phase, CombatPhase.BURST_READY)
        self.assertAlmostEqual(logic.release_burst.price("2", "ult", task.now) / before, 1.22, places=5)
        self.assertEqual(task.sp, 300)
        logic.step()
        self.assertIn(task.keys, (["ult_1", "2"], ["ult_1", "ult_2"]))

    def test_free_team_opener_then_available_damage_ultimate_without_sp(self):
        task, logic = self.make_logic()
        task.sp = 0
        logic.step()
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

    def test_rejected_release_does_not_commit_timers_or_cooldowns(self):
        task, logic = self.make_logic()
        with patch.object(task, "use_ult", return_value=False):
            self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("安塔尔", "ult"), burst_selected=True))
        self.assertFalse(logic.release_burst.bonuses)
        self.assertFalse(logic.cooldowns)

    def test_blocked_ultimate_feedback_does_not_commit_release_or_control_windows(self):
        task, logic = self.make_logic()
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.TOO_FAR
        self.assertTrue(logic._use_timed_ultimate("1", logic.store.profiles("安塔尔", "ult"), burst_selected=True))
        self.assertEqual(task.keys, ["ult_1"])
        self.assertFalse(logic.release_burst.bonuses)
        self.assertFalse(logic.release_burst.last_confirmed)
        self.assertFalse(logic.cooldowns)
        self.assertFalse(logic.main_control.windows)
        self.assertIsNone(logic.last_action_attempt)

    def test_clean_ultimate_confirms_release_after_fresh_feedback_frames(self):
        task, logic = self.make_logic()
        events = []
        task.probe_combat_action_block_reason = lambda: events.append("probe")
        original_confirm = logic.release_burst.confirm

        def confirm(*args, **kwargs):
            events.append("confirm")
            return original_confirm(*args, **kwargs)

        with patch.object(logic.release_burst, "confirm", side_effect=confirm):
            self.assertTrue(logic._use_timed_ultimate("1", logic.store.profiles("安塔尔", "ult"), burst_selected=True))
        self.assertEqual(events[:3], ["probe", "probe", "confirm"])
        self.assertEqual(logic.release_burst.last_confirmed[("1", "ult")], 0.0)
        self.assertIsNone(logic.last_action_attempt)

    def test_unavailable_hud_or_dead_beneficiary_cannot_promote_support(self):
        task, logic = self.make_logic()
        task.sp = 0
        task.ults = {"1"}
        self.assertIsNone(logic._release_burst_decision(logic._ready_ultimate_actions(), 0, task.now))
        task.ults = {"1", "2"}
        logic.disabled_slots.add("2")
        self.assertIsNone(logic._release_burst_decision(logic._ready_ultimate_actions(), 0, task.now))

    def test_no_new_detector_or_implicit_main_control_after_ultimate(self):
        task, logic = self.make_logic()
        self.assertIsNone(logic._burst_main_control)
        logic.step()
        self.assertIsNone(logic._burst_main_control)

    def test_unknown_current_arrow_cannot_reuse_an_old_normal_beneficiary(self):
        task, logic = self.make_logic()
        logic._burst_main_control = "2"
        with patch.object(task, "detect_current_char_index", return_value=None, create=True):
            logic.step()
        self.assertIsNone(logic._burst_main_control)

    def test_existing_combo_is_not_a_release_bonus_producer(self):
        task, logic = self.make_logic()
        task.ults = set()
        task.sp = 0
        task.link = True
        logic.step()
        self.assertEqual(task.keys, ["e"])
        self.assertFalse(logic.release_burst.bonuses)

    def test_liino_real_step_opens_team_burst_without_18_second_wait(self):
        task, logic = self.make_logic()
        task.sp = 0
        logic._configure_team(["梨诺", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        ready = logic._ready_ultimate_actions()
        chosen = logic._release_burst_decision(ready, 0, task.now)
        self.assertEqual(chosen.action.slot, "1")
        self.assertLess(chosen.action.duration, 3)
        self.assertGreater(chosen.action.same_actor_duration, 18)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertFalse(logic._try_battle_token("1", 300))
        task.now = 3
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

    def test_xaihi_real_step_opens_cold_team_and_failure_does_not_apply(self):
        task, logic = self.make_logic()
        task.sp = 0
        logic._configure_team(["赛希", "伊冯", "狼卫", "陈千语"], reset_runtime=True)
        with patch.object(task, "use_ult", return_value=False):
            self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("赛希", "ult"), burst_selected=True))
        self.assertFalse(logic.release_burst.bonuses)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        task.now = 3
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

    def test_dead_liino_channel_cleanup_preserves_other_supports(self):
        task, logic = self.make_logic()
        logic._configure_team(["梨诺", "安塔尔", "佩丽卡", "狼卫"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        logic.release_burst.confirm("2", "ult", 0)
        logic.disabled_slots.add("1")
        task.ults = set()
        task.sp = 0
        logic.step()
        self.assertFalse(any(b.source == "1" for b in logic.release_burst.bonuses))
        self.assertTrue(any(b.source == "2" for b in logic.release_burst.bonuses))

    def test_zhuang_step_forecasts_free_battle_even_with_zero_shared_sp(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        task.ults = {"1", "2"}
        task.sp = 0
        chosen = logic._release_burst_decision(logic._ready_ultimate_actions(), 0, 0)
        self.assertIn(("1", "ult"), chosen.sequence)
        self.assertIn(("1", "battle"), chosen.sequence)
        task.ults = {"1"}
        chosen = logic._release_burst_decision(logic._ready_ultimate_actions(), 0, 0)
        self.assertEqual((chosen.action.slot, chosen.action.kind), ("1", "ult"))
        self.assertGreater(chosen.gain, 0)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        task.now = 3.2
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)
        task.battle_pulses.add("1")
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "1"])
        self.assertEqual(logic.active[0].skill_id, "chr_0030_zhuangfy_normal_skill_ult")
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)
        self.assertIsNotNone(logic.pending)
        task.battle_pulses.clear()
        logic.step()
        self.assertFalse(logic.release_burst.stance_at("1", task.now).free_battle)
        self.assertEqual(logic._battle_context("1")[1:], (100, 100))
        task.now = 28
        self.assertEqual(logic._battle_context("1")[0][0].skill_id, "chr_0030_zhuangfy_normal_skill")

    def test_zhuang_rejected_ultimate_creates_neither_stance_nor_free_battle(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        with patch.object(task, "use_ult", return_value=False):
            self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("庄方宜", "ult"), burst_selected=True))
        self.assertFalse(logic.release_burst.stances)
        self.assertFalse(logic.free_battle_once)

    def test_zhuang_existing_failed_cast_feedback_does_not_consume_free_charge(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.now = 3.2
        task.battle_pulses.add("1")
        self.assertTrue(logic._try_battle_token("1", 0))
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.DURING_SKILL
        self.assertEqual(logic._probe_action_feedback(), ActionBlockReason.DURING_SKILL)
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)

    def test_zhuang_free_battle_waits_for_own_pulse_without_blocking_teammate(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.now = 3.2
        task.sp = 300
        task.ults = set()
        task.battle_pulses.add("2")
        self.assertFalse(logic._try_battle_token("1", 300))
        self.assertEqual(task.keys, [])
        with patch.object(logic.release_burst, "choose", return_value=None) as choose:
            logic._release_burst_decision([], 300, task.now)
            self.assertFalse(any(a.slot == "1" and a.kind == "battle" for a in choose.call_args.args[0]))
            self.assertTrue(any(a.slot == "2" and a.kind == "battle" for a in choose.call_args.args[0]))
        self.assertTrue(logic._try_battle_token("2", 300))
        self.assertEqual(task.keys, ["2"])
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)

    def test_zhuang_white_pulse_failure_keeps_charge_until_retry_and_success(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.now = 3.2
        task.sp = 0
        task.battle_pulses.add("1")
        task.probe_combat_action_block_reason = lambda: ActionBlockReason.DURING_SKILL
        self.assertTrue(logic._try_battle_token("1", 0))
        self.assertEqual(logic._probe_action_feedback(), ActionBlockReason.DURING_SKILL)
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)
        self.assertFalse(logic._try_battle_token("1", 0))
        task.probe_combat_action_block_reason = lambda: None
        task.now = logic.battle_retry_after["1"] + .01
        task.battle_pulses.clear()
        self.assertFalse(logic._try_battle_token("1", 0))
        task.battle_pulses.add("1")
        self.assertTrue(logic._try_battle_token("1", 0))
        self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)
        task.battle_pulses.clear()
        logic._confirm_battle(task.now)
        self.assertFalse(logic.release_burst.stance_at("1", task.now).free_battle)
        self.assertFalse(logic._try_battle_token("1", 0))
        self.assertEqual(task.keys, ["1", "1"])
        self.assertEqual(logic._battle_context("1")[1:], (100, 100))

    def test_zhuang_paid_battle_also_waits_for_white_and_confirms_by_disappearance(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        task.now = 3.2
        self.assertFalse(logic._try_battle_token("1", 300))
        task.battle_pulses.add("1")
        self.assertTrue(logic._try_battle_token("1", 300))
        self.assertEqual(logic.pending, (300, "1", 100))
        task.sp = 200  # SP alone must not confirm while the button remains white.
        logic._confirm_battle(task.now)
        self.assertIsNotNone(logic.pending)
        task.battle_pulses.clear()
        with patch.object(logic, "_sample_sp") as sample:
            logic._confirm_battle(task.now)
            sample.assert_not_called()
        self.assertIsNone(logic.pending)
        self.assertIn(logic.active[0].skill_id, logic.cooldowns)
        self.assertTrue(any("白色脉冲消失，确认释放" in m for m in task.messages))

    def test_zhuang_pending_white_or_unknown_times_out_without_consuming_charge(self):
        for observed in (True, None):
            with self.subTest(observed=observed):
                task, logic = self.make_logic()
                logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
                logic.release_burst.confirm("1", "ult", 0)
                task.now = 3.2
                task.battle_pulses.add("1")
                self.assertTrue(logic._try_battle_token("1", 0))
                task.is_battle_skill_pulsing = lambda _token: observed
                task.now = 3.3
                logic._confirm_battle(task.now)
                self.assertIsNotNone(logic.pending)
                task.now = logic.started + .81
                logic._confirm_battle(task.now)
                self.assertIsNone(logic.pending)
                self.assertEqual(logic.active, ())
                self.assertFalse(logic.cooldowns)
                self.assertTrue(logic.release_burst.stance_at("1", task.now).free_battle)
                self.assertEqual(task.keys, ["1"])

    def test_zhuang_pulse_cannot_bypass_stance_start_or_expiry(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.battle_pulses.add("1")
        task.now = 2
        self.assertFalse(logic._try_battle_token("1", 0))
        task.now = 28
        self.assertFalse(logic._try_battle_token("1", 0))
        self.assertEqual(task.keys, [])

    def test_zhuang_missing_pulse_probe_does_not_fall_back_to_timer_press(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.now = 3.2
        task.is_battle_skill_pulsing = None
        self.assertFalse(logic._try_battle_token("1", 300))
        self.assertEqual(task.keys, [])

    def test_zhuang_unused_free_flag_never_survives_actual_stance_expiry(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        logic.free_battle_once.add("1")
        logic.forced_battle_token = "1"
        task.sp = 0
        task.ults = set()
        task.now = 28
        self.assertEqual(logic._battle_context("1")[1:], (100, 100))
        self.assertFalse(logic._try_battle_token("1", 0))
        logic.step()
        self.assertNotIn("1", logic.free_battle_once)
        self.assertIsNone(logic.forced_battle_token)

    def test_zhuang_cancel_ultimate_button_is_not_another_burst_opener(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        task.ults = {"1"}
        task.now = 16
        self.assertFalse(logic._ready_ultimate_actions())
        self.assertFalse(logic._use_timed_ultimate("1", logic.store.profiles("庄方宜", "ult")))
        self.assertFalse(task.keys)
        task.now = 28
        self.assertTrue(logic._ready_ultimate_actions())

    def test_zhuang_main_control_normal_replacement_affects_real_dispatch(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        task.ults = {"1"}
        task.sp = 0
        with patch.object(task, "detect_current_char_index", return_value=0, create=True):
            chosen = None
            original = logic.release_burst.choose

            def capture(*args, **kwargs):
                nonlocal chosen
                chosen = original(*args, **kwargs)
                return chosen

            with patch.object(logic.release_burst, "choose", side_effect=capture):
                logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertIn(("1", "normal"), chosen.sequence)
        self.assertGreater(chosen.gain, 0)

    def test_normal_tail_respects_same_actor_replacement_allow_next(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic.release_burst.confirm("1", "ult", 0)
        logic.release_burst.confirm("1", "battle", 3.2)
        logic._begin((logic.store.profile("chr_0030_zhuangfy_normal_skill_ult"),), 3.2, slot="1", kind="battle")
        logic._burst_main_control = "1"
        self.assertEqual(logic.release_burst.normal_entry("1", 3.8)[0].skill_id, "chr_0030_zhuangfy_attack1_ult")
        task.now = 3.8  # Other actors may act at 0.55s; own normal only at 1s.
        with patch.object(logic.release_burst, "choose", return_value=None) as choose:
            logic._release_burst_decision([], 0, task.now)
            self.assertFalse(any(a.kind == "normal" for a in choose.call_args.args[0]))
        task.now = 4.3
        with patch.object(logic.release_burst, "choose", return_value=None) as choose:
            logic._release_burst_decision([], 0, task.now)
            self.assertTrue(any(a.kind == "normal" and a.slot == "1" for a in choose.call_args.args[0]))
        self.assertEqual(logic.release_burst.normal_entry("1", 28)[0].skill_id, "chr_0030_zhuangfy_attack1")

    def test_other_actor_normal_tail_still_uses_cross_actor_handoff(self):
        task, logic = self.make_logic()
        logic._configure_team(["庄方宜", "佩丽卡", "狼卫", "陈千语"], reset_runtime=True)
        logic._begin(logic.store.profiles("佩丽卡", "battle"), 0, slot="2", kind="battle")
        logic._burst_main_control = "1"
        task.now = .6
        with patch.object(logic.release_burst, "choose", return_value=None) as choose:
            logic._release_burst_decision([], 0, task.now)
            self.assertTrue(any(a.kind == "normal" and a.slot == "1" for a in choose.call_args.args[0]))


class TestUltimateOutputWindow(unittest.TestCase):
    def test_selected_free_cast_pending_stance_does_not_fall_through_to_paid_overflow(self):
        task, logic = self.make_logic(sp=267, ready=False)
        logic.release_burst.confirm('1', 'ult', 0)
        logic.free_battle_once.add('1')
        task.battle_pulses = {'1'}
        task.now = 2.395  # Real log: 7.255 wall seconds minus 4.86 confirmed HUD freeze.
        decision = ReleaseBurstDecision(ReleaseBurstAction('1', 'battle', .55, 211550, 0, 0),
                                         (('1', 'battle'),), 211550, 211550)
        with patch.object(logic, '_release_burst_decision', return_value=decision):
            logic.step()
            self.assertEqual(task.keys, [], 'A selected free cast must not silently spend 100SP on another slot')
            self.assertIsNone(logic.pending)
            task.now = 2.7
            logic.step()
        self.assertEqual(task.keys, ['1'])
        self.assertEqual(logic.pending[2], 0)

    def make_logic(self, *, sp=225, ready=True, white=True):
        task = FakeTask()
        task.sp = sp
        task.ults = {'1', '2', '3', '4'} if ready else set()
        task.battle_pulses = {'1', '3'} if white else set()
        task.detect_current_char_index = lambda: 0
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(['庄方宜', '佩丽卡', '艾维文娜', '梨诺'], reset_runtime=True)
        logic._observe_battle_highlights()
        logic._burst_main_control = '1'
        return task, logic

    def test_no_ultimate_has_local_mode_ready_and_live_stance_expand_output_mode(self):
        _, logic = self.make_logic()
        p = logic.release_burst
        self.assertEqual(p.planning_horizon([], 0), 12)
        self.assertAlmostEqual(p.planning_horizon(['1'], 0), 27.6)
        p.confirm('1', 'ult', 0)
        self.assertAlmostEqual(p.planning_horizon([], 3), 24.6)
        self.assertEqual(p.planning_horizon([], 30), 12)

    def test_ultimate_and_plain_routes_use_the_same_extended_clock(self):
        _, logic = self.make_logic()
        p = logic.release_burst
        with patch.object(p, '_search', wraps=p._search) as search:
            decision = logic._release_burst_decision(logic._ready_ultimate_actions(), 225, 0)
        self.assertEqual(decision.action.slot, '1')
        self.assertEqual(decision.action.kind, 'ult')
        self.assertEqual({call.kwargs['horizon'] for call in search.call_args_list}, {27.6})
        self.assertEqual({call.kwargs['releases'] for call in search.call_args_list}, {True, False})
        self.assertTrue(all(call.kwargs['repeat_normals'] for call in search.call_args_list))
        self.assertGreater(decision.sequence.count(('1', 'normal')), 1)
        self.assertLess(decision.sequence.index(('1', 'ult')), decision.sequence.index(('1', 'battle')))
        self.assertLess(decision.sequence.index(('4', 'ult')), decision.sequence.index(('1', 'battle')))

    def test_real_all_ult_step_opens_stance_then_waits_for_its_real_free_button(self):
        task, logic = self.make_logic()
        logic.step()
        self.assertEqual(task.keys[0], 'ult_1')
        self.assertTrue(logic.release_burst.stance_at('1', 3).free_battle)
        self.assertFalse(logic.release_burst.mechanism_state.requests)
        task.now = 3.3
        self.assertTrue(logic._try_battle_token('1', task.sp))
        self.assertEqual(logic.pending[2], 0)
        self.assertEqual(logic._attempt_mechanism.same_cast_requested_swords, 3)
        self.assertFalse(logic.release_burst.mechanism_state.requests)  # White acknowledgement not yet observed.

    def test_future_free_candidate_cannot_execute_in_the_ordinary_pre_ultimate_state(self):
        _, logic = self.make_logic(sp=0, white=False)
        p = logic.release_burst
        action = logic._release_battle_candidate('1', 0, 0, {'1'})
        self.assertTrue(action.requires_free_battle)
        node = (0., 0., 0., (), [], {}, {}, logic._scene_conditions(), 0., p.mechanism_state, p.normal_state)
        self.assertIsNone(p._forecast_step(node, action, 0, 0, releases=True, horizon=27.6, predecessors={0: ()}))
        ultimate = logic._release_ultimate_candidate('1', logic.store.profiles('庄方宜', 'ult'))
        node = p._forecast_step(node, ultimate, 1, 0, releases=True, horizon=27.6, predecessors={1: ()})
        after = p._forecast_step(node, action, 0, 0, releases=True, horizon=27.6, predecessors={0: ()})
        self.assertIsNotNone(after)
        self.assertEqual(after[2], 0)
        self.assertGreater(after[0], 200000)

    def test_ultimate_recovery_does_not_force_the_free_cast_ahead_of_planned_support(self):
        task, logic = self.make_logic()
        original = task.use_ult
        def recover_after_stance_starts(**kwargs):
            result = original(**kwargs)
            task.now += 1.2
            return result
        task.use_ult = recover_after_stance_starts
        self.assertTrue(logic._use_timed_ultimate('1', logic.store.profiles('庄方宜', 'ult'), burst_selected=True))
        self.assertEqual(task.keys, ['ult_1'])
        self.assertTrue(logic.release_burst.stance_at('1', task.now).free_battle)
        self.assertEqual(logic.forced_battle_token, '1')  # Still available to the next real planning step.
        logic.step()
        self.assertEqual(task.keys[1], 'ult_4')

    def test_no_ready_ultimate_and_no_sp_cannot_invent_a_free_action(self):
        task, logic = self.make_logic(sp=0, ready=False)
        self.assertIsNone(logic._release_battle_candidate('1', 0, 0, set()))
        logic.step()
        self.assertEqual(task.keys, [])
        self.assertFalse(logic.release_burst.stances)
        self.assertFalse(logic.release_burst.mechanism_state.requests)

    def test_failed_ultimate_does_not_unlock_a_free_cast_or_requests(self):
        task, logic = self.make_logic(sp=0, white=False)
        task.ults = {'1'}
        task.use_ult = lambda **kwargs: False
        logic.step()
        self.assertFalse(logic.release_burst.stances)
        self.assertFalse(logic.release_burst.mechanism_state.requests)
        self.assertEqual(task.keys, [])

    def test_normal_repeats_are_timed_and_restore_base_duration_after_stance(self):
        _, logic = self.make_logic()
        p = logic.release_burst
        normal = ReleaseBurstAction('1', 'normal', p.normal_durations['1'], p.quote('1', 'normal')['crit_expect'])
        node = p._search((normal,), 0, 0, releases=True, bonuses=[], horizon=27.6, width=16,
                         conditions=logic._scene_conditions(), repeat_normals=True)
        self.assertEqual(len(node[3]), 4)
        self.assertLessEqual(node[1], 27.6)
        self.assertEqual(node[2], 0)
        p.confirm('1', 'ult', 0)
        enhanced = p._stance_action(normal, 3, p.stances)
        restored = p._stance_action(enhanced, 28, p.stances)
        self.assertAlmostEqual(restored.duration, p.normal_durations['1'])


class TestBurstDecisionTable(unittest.TestCase):
    @staticmethod
    def generator():
        script = Path(__file__).resolve().parents[1]/'scripts/skill-data/prepare_burst_decision_table.py'
        spec = importlib.util.spec_from_file_location('prepare_burst_table_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def example(self):
        p = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        p.decision_table = BurstDecisionTable()
        actions = (ReleaseBurstAction('1', 'battle', .55, 0, 25, 25),
                   ReleaseBurstAction('2', 'battle', .5, 1000, 100, 100))
        return p, actions

    def test_identical_inputs_hit_and_replay_without_beam_search(self):
        p, actions = self.example()
        first = p.choose(actions, 125, 0)
        with patch.object(p, '_search', side_effect=AssertionError('Unexpected full search')):
            cached = p.choose(actions, 125, 0)
        self.assertEqual(cached, first)
        self.assertEqual(p.decision_table.hits, 1)
        self.assertFalse(p.bonuses)

    def test_sp_feasibility_cell_reuses_only_the_same_affordable_routes(self):
        p, actions = self.example()
        first = p.choose(actions, 125, 0)
        self.assertEqual(budget_cell(actions, 125), budget_cell(actions, 149.9))
        self.assertNotEqual(budget_cell(actions, 124.9), budget_cell(actions, 125))
        fractional = (replace(actions[0], sp_cost=25.5), actions[1])
        self.assertNotEqual(budget_cell(fractional, 126), budget_cell(fractional, 127))
        with patch.object(p, '_search', side_effect=AssertionError('Unexpected full search')):
            self.assertEqual(p.choose(actions, 149.9, 0).sequence, first.sequence)
        with patch.object(p, '_search', wraps=p._search) as search:
            below = p.choose(actions, 124.9, 0)
        self.assertTrue(search.called)
        self.assertEqual(below.sequence, (('2', 'battle'),))

    def test_translated_empty_scene_with_fresh_observations_can_hit(self):
        task, logic = TestUltimateOutputWindow().make_logic()
        p = logic.release_burst
        p.decision_table = BurstDecisionTable()
        first = logic._release_burst_decision(logic._ready_ultimate_actions(), 225, 0)
        task.now = 100
        logic._observe_battle_highlights()
        with patch.object(p, '_search', side_effect=AssertionError('Unexpected full search')):
            second = logic._release_burst_decision(logic._ready_ultimate_actions(), 225, 100)
        self.assertEqual(second.sequence, first.sequence)
        self.assertAlmostEqual(second.damage, first.damage)

    def test_ring_changes_do_not_hit_an_old_scene(self):
        task, logic = TestUltimateOutputWindow().make_logic(ready=False)
        p = logic.release_burst
        p.decision_table = BurstDecisionTable()
        first = logic._release_burst_decision([], 225, 0)
        task.battle_pulses.clear()
        logic._observe_battle_highlights()
        with patch.object(p, '_search', wraps=p._search) as search:
            second = logic._release_burst_decision([], 225, 0)
        self.assertTrue(search.called)
        self.assertNotEqual(first.damage, second.damage)

    def test_active_window_aging_and_rule_changes_do_not_hit(self):
        p, actions = self.example()
        p.confirm('1', 'battle', 0)
        p.choose(actions, 100, 2)
        with patch.object(p, '_search', wraps=p._search) as search:
            p.choose(actions, 100, 2.1)
        self.assertTrue(search.called)
        p.rules['2', 'battle'] = p.rules['1', 'battle']
        with patch.object(p, '_search', wraps=p._search) as search:
            p.choose(actions, 100, 2.1)
        self.assertTrue(search.called)

    def test_offline_table_load_has_no_runtime_file_read_and_validates_its_path(self):
        p, actions = self.example()
        first = p.choose(actions, 125, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'policy.json'
            p.decision_table.save(path)
            table = BurstDecisionTable.load(path)
            self.assertEqual(len(table.entries), 1)
            p.decision_table = table
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('No per-frame disk I/O')):
                with patch.object(p, '_search', side_effect=AssertionError('No search on a hit')):
                    self.assertEqual(p.choose(actions, 125, 0), first)
            data = json.loads(path.read_bytes())
            data['algorithm_sha256'] = 'changed'
            path.write_text(json.dumps(data), encoding='utf8')
            self.assertFalse(BurstDecisionTable.load(path).entries)

    def test_corrupt_checksum_and_impossible_cached_sequence_fall_back(self):
        p, actions = self.example()
        first = p.choose(actions, 125, 0)
        key = next(iter(p.decision_table.entries))
        original = dict(p.decision_table.entries[key])
        p.decision_table.entries[key] = {**original, 'sequence': [('4', 'battle')]}
        with patch.object(p, '_search', wraps=p._search) as search:
            result = p.choose(actions, 125, 0)
        self.assertTrue(search.called)
        self.assertEqual(result.sequence, first.sequence)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'policy.json'
            p.decision_table.save(path)
            data = json.loads(path.read_bytes())
            data['entries'][key]['damage'] += 1
            path.write_text(json.dumps(data), encoding='utf8')
            self.assertFalse(BurstDecisionTable.load(path).entries)

    def test_numeric_corruption_cannot_override_the_replayed_route(self):
        p, actions = self.example()
        first = p.choose(actions, 125, 0)
        key = next(iter(p.decision_table.entries))
        p.decision_table.entries[key]['damage'] += 1000000
        with patch.object(p, '_search', wraps=p._search) as search:
            second = p.choose(actions, 125, 0)
        self.assertTrue(search.called)
        self.assertEqual(second.damage, first.damage)

    def test_local_table_is_bounded_and_does_not_publish_scene_state(self):
        p, actions = self.example()
        for i in range(300):
            p.decision_table.put(f'{i:064x}', {'not': 'an executable policy'})
        self.assertEqual(len(p.decision_table.entries), 256)
        before = p.mechanism_state
        p.choose(actions, 125, 0)
        p.choose(actions, 125, 0)
        self.assertEqual(p.mechanism_state, before)
        self.assertFalse(p.bonuses)

    def test_offline_opening_generator_produces_a_table_for_actual_runtime_inputs(self):
        module = self.generator()
        scenario = {'team': ['庄方宜', '佩丽卡', '艾维文娜', '梨诺'], 'sp': 225,
                    'ultimate_slots': ['1', '2', '3', '4'], 'rings': {'1': True, '3': True}, 'main_slot': '1'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'table.json'
            result = module.prepare([scenario], path)
            self.assertEqual(result['stored_results'], 1)
            _, logic = TestUltimateOutputWindow().make_logic()
            p = logic.release_burst
            p.decision_table = BurstDecisionTable.load(path)
            with patch.object(p, '_search', side_effect=AssertionError('Precomputed scene must hit')):
                decision = logic._release_burst_decision(logic._ready_ultimate_actions(), 225, 0)
            self.assertEqual((decision.action.slot, decision.action.kind), ('1', 'ult'))
            self.assertEqual(p.decision_table.hits, 1)

    def test_actual_roster_size_and_order_select_independent_shards(self):
        roster = ['庄方宜', '佩丽卡', '艾维文娜', '梨诺']
        paths = {team_path(roster[:size]) for size in range(1, 5)}
        paths.add(team_path(roster[::-1]))
        self.assertEqual(len(paths), 5)
        for invalid in ([], roster+['安塔尔'], [None], ['']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                team_path(invalid)
        self.assertFalse(BurstDecisionTable.for_team([]).entries)

    def test_team_shards_load_independently_and_missing_shard_keeps_legacy_fallback(self):
        p, actions = self.example()
        p.choose(actions, 125, 0)
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory)/'legacy.json'
            p.decision_table.save(legacy)
            shard = team_path(['庄方宜'], directory)
            BurstDecisionTable().save(shard)
            self.assertEqual(len(BurstDecisionTable.for_team(['庄方宜'], directory=directory,
                                                            fallback=legacy).entries), 1)
            p.decision_table.save(shard)
            with patch.object(BurstDecisionTable, 'load', wraps=BurstDecisionTable.load) as load:
                self.assertEqual(len(BurstDecisionTable.for_team(['庄方宜'], directory=directory,
                                                                fallback=legacy).entries), 1)
            load.assert_called_once_with(shard)
            self.assertFalse(BurstDecisionTable.for_team(['佩丽卡'], directory=directory,
                                                         fallback=Path(directory)/'missing.json').entries)

    def test_one_to_four_member_offline_policies_hit_real_unpadded_candidates(self):
        module = self.generator()
        roster = ['庄方宜', '佩丽卡', '艾维文娜', '梨诺']
        scenarios = [{'team': roster[:size], 'sp': 225,
                      'ultimate_slots': [str(i+1) for i in range(size)],
                      'rings': {str(i+1): True for i in range(size)}, 'main_slot': '1'} for size in range(1, 5)]
        with tempfile.TemporaryDirectory() as directory:
            result = module.prepare_teams(scenarios, directory)
            self.assertEqual(result['team_sizes'], [1, 2, 3, 4])
            self.assertEqual(result['team_shards'], 4)
            for scenario in scenarios:
                team = scenario['team']
                slots = {str(i+1) for i in range(len(team))}
                with self.subTest(size=len(team)), patch.object(BurstDecisionTable, 'for_team',
                        side_effect=lambda current: BurstDecisionTable.load(team_path(current, directory))):
                    logic = TimedCombatLogic(module.PlanningInputs(scenario['ultimate_slots'], scenario['rings']),
                                             load_skill_timings(), clock=lambda: 0.)
                    self.assertTrue(logic._configure_team(team, reset_runtime=True))
                    p = logic.release_burst
                    self.assertEqual(set(p.rows), slots)
                    logic._observe_battle_highlights()
                    logic._burst_main_control = '1'
                    with patch.object(p, '_search', side_effect=AssertionError('Prepared roster must hit')):
                        decision = logic._release_burst_decision(logic._ready_ultimate_actions(), 225, 0)
                    self.assertEqual(p.decision_table.hits, 1)
                    self.assertTrue(all(slot in slots for slot, _ in decision.sequence))
                    for slot in slots:
                        p.confirm(slot, 'ult', 0)
                    self.assertTrue(all(b.recipient in slots for b in p.bonuses))

    def test_single_support_cannot_gain_from_missing_allies_or_ready_slots(self):
        module = self.generator()
        scenario = {'team': ['梨诺'], 'sp': 225, 'ultimate_slots': ['1'], 'rings': {}, 'main_slot': None}
        with tempfile.TemporaryDirectory() as directory:
            result = module.prepare_teams([scenario], directory)
            self.assertEqual(result['outputs'][0]['stored_results'], 0)
            for field, value in (('ultimate_slots', ['2']), ('rings', {'2': True}), ('main_slot', '2')):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'slots'):
                    module.prepare([{**scenario, field: value}], Path(directory)/'invalid.json')

    def test_multi_team_batches_do_not_evict_other_rosters_and_reject_oversized_groups(self):
        module = self.generator()
        scenario = {'team': ['庄方宜'], 'sp': 100, 'ultimate_slots': ['1'], 'rings': {'1': True}, 'main_slot': '1'}
        with tempfile.TemporaryDirectory() as directory:
            result = module.prepare_teams([scenario, {**scenario, 'team': ['庄方宜', '佩丽卡']}], directory)
            self.assertEqual(result['team_shards'], 2)
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 2)
            self.assertTrue(all(row['stored_results'] == 1 for row in result['outputs']))
            with self.assertRaisesRegex(ValueError, '256'):
                module.prepare_teams([scenario]*257, directory)


class TestBurstSpValue(unittest.TestCase):
    def test_real_step_prices_actual_paid_profiles_and_dispatches_the_higher_value(self):
        task = FakeTask()
        task.sp = 200
        task.ults = set()
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(['余烬', '佩丽卡'], reset_runtime=True)
        with patch.object(task, 'log_info') as log:
            logic.step()
        self.assertEqual(task.keys, ['2'])
        rows = [json.loads(call.args[0].split(': ', 1)[1]) for call in log.call_args_list
                if call.args[0].startswith('时间排轴单位技力收益: ')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sp_cost'], 100)
        self.assertEqual(rows[0]['followup_contribution'], 0)
        self.assertAlmostEqual(rows[0]['direct_damage'], 24472.4)
        self.assertAlmostEqual(rows[0]['damage_per_sp'], 244.724)

    def test_more_damage_per_sp_moves_interchangeable_paid_cast_first(self):
        p = ReleaseBurstPlanner(['佩丽卡', '余烬'])
        actions = (ReleaseBurstAction('1', 'battle', 1, 1500, 100, 100),
                   ReleaseBurstAction('2', 'battle', 1, 1000, 25, 25))
        decision = p.choose(actions, 125, 0)
        self.assertEqual(decision.sequence, (('2', 'battle'), ('1', 'battle')))
        self.assertEqual(decision.damage, 2500)
        self.assertEqual(decision.sp_value.direct_damage, 1000)
        self.assertEqual(decision.sp_value.followup_contribution, 0)
        self.assertEqual(decision.sp_value.damage_per_sp, 40)

    def test_support_gets_only_its_marginal_followup_gain_not_all_recipient_damage(self):
        p = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        support = ReleaseBurstAction('1', 'battle', .55, 0, 25, 25)
        recipient = ReleaseBurstAction('2', 'battle', .5, 1000, 100, 100)
        decision = p.choose((support, recipient), 125, 0)
        self.assertEqual(decision.sequence, (('1', 'battle'), ('2', 'battle')))
        self.assertEqual(decision.sp_value.direct_damage, 0)
        self.assertEqual(decision.sp_value.sp_cost, 25)
        self.assertAlmostEqual(decision.sp_value.followup_contribution, decision.damage-1000)
        self.assertAlmostEqual(decision.sp_value.damage_per_sp, (decision.damage-1000)/25)
        self.assertGreater(decision.sp_value.damage_per_sp, 0)
        self.assertLess(decision.sp_value.followup_contribution, recipient.value)
        self.assertFalse(p.bonuses)

    def test_no_recipient_no_invented_future_sp_or_sixty_seconds_of_damage(self):
        p = ReleaseBurstPlanner(['梨诺', '佩丽卡'])
        support = ReleaseBurstAction('1', 'battle', .55, 0, 25, 25)
        recipient = ReleaseBurstAction('2', 'battle', .5, 1000, 100, 100)
        self.assertIsNone(p.choose((support,), 25, 0))
        decision = p.choose((support,recipient), 100, 0)
        self.assertEqual(decision.sequence, (('2', 'battle'),))
        self.assertEqual(decision.sp_value.followup_contribution, 0)

    def test_stance_cancel_zero_sp_is_blocked_and_does_not_refresh_the_team_buff(self):
        task = FakeTask()
        task.sp, task.now = 100, 2
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(['梨诺', '佩丽卡'], reset_runtime=True)
        p = logic.release_burst
        p.confirm('1', 'battle', 0)
        before = tuple(p.bonuses)
        self.assertIsNone(logic._release_battle_candidate('1', 100, 2, set()))
        cancel = ReleaseBurstAction('1', 'battle', .55, 0, 0, 0)
        recipient = ReleaseBurstAction('2', 'battle', .5, 1000, 100, 100)
        decision = p.choose((cancel, recipient), 100, 2)
        self.assertEqual(decision.action.slot, '2')
        self.assertEqual(tuple(p.bonuses), before)
        self.assertEqual(decision.sp_value.sp_cost, 100)

    def test_same_actor_lock_prevents_crediting_an_unreachable_followup(self):
        p = ReleaseBurstPlanner(['佩丽卡'])
        ult = ReleaseBurstAction('1', 'ult', .5, 500, same_actor_duration=10)
        battle = ReleaseBurstAction('1', 'battle', 1, 1000, 100, 100)
        decision = p.choose((ult,battle), 100, 0, horizon=4)
        self.assertEqual(decision.sequence, (('1', 'battle'), ('1', 'ult')))
        self.assertAlmostEqual(decision.sp_value.followup_contribution, 0)

    def test_true_free_battle_is_finite_and_keeps_time_and_stance_constraints(self):
        p = ReleaseBurstPlanner(['庄方宜', '佩丽卡'])
        p.confirm('1', 'ult', 0)
        free = ReleaseBurstAction('1', 'battle', .55, 1, 100, 100)
        paid = ReleaseBurstAction('2', 'battle', .5, 1000, 100, 100)
        decision = p.choose((free, paid), 100, 3)
        self.assertEqual(decision.action.slot, '1')
        self.assertEqual(decision.sp_value.sp_cost, 0)
        self.assertIsNone(decision.sp_value.damage_per_sp)
        self.assertGreater(decision.sp_value.direct_damage, 21000)

    def test_queue_predecessor_value_is_not_replayed_as_if_the_head_were_cleared(self):
        p = ReleaseBurstPlanner(['佩丽卡', '余烬'])
        actions = (ReleaseBurstAction('1', 'link', .5, 500, queue_position=1),
                   ReleaseBurstAction('2', 'link', .5, 1000, queue_position=2))
        decision = p.choose(actions, 0, 0)
        self.assertEqual(decision.sequence, (('1', 'link'), ('2', 'link')))
        self.assertEqual(decision.sp_value.direct_damage, 500)
        self.assertEqual(decision.sp_value.followup_contribution, 1000)
        self.assertIsNone(decision.sp_value.damage_per_sp)
        self.assertEqual(decision.damage, 1500)


class TestReleaseSnapshotExportBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        directory = cls.root / "scripts/skill-data"
        sys.path.insert(0, str(directory))
        try:
            spec = importlib.util.spec_from_file_location("reviewed_release_export", directory / "export_release_burst_snapshot.py")
            cls.exporter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.exporter)
        finally:
            sys.path.remove(str(directory))

    def test_rangliu_contract_digest_survives_git_lf_normalization(self):
        raw = (SNAPSHOT/'rangliu-contract.json').read_bytes()
        self.assertNotIn(b'\r', raw)
        digest = hashlib.sha256(raw).hexdigest()
        manifest = json.loads((SNAPSHOT/'index.json').read_bytes())
        self.assertEqual(manifest['supplement_source_hashes']['assets/data/release_burst/rangliu-contract.json'], digest)
        self.assertEqual(self.exporter.RANGLIU_SHA256, digest)
        self.assertEqual(read_snapshot()['庄方宜']['mechanism']['consumption_equipment']['contract_sha256'], digest)

    def test_revision_option_and_non_sha_rejected_before_git_execution(self):
        for revision in ("--help", "master", "a" * 39, "a" * 40 + "^{tree}"):
            with self.subTest(revision=revision), patch.object(self.exporter.subprocess, "check_output") as git:
                with self.assertRaisesRegex(ValueError, "full lowercase"):
                    self.exporter.export(self.root, revision, SNAPSHOT)
                git.assert_not_called()

    def test_si_rank_and_current_native_provenance_are_distinct_from_old_export(self):
        actor, proof = self.exporter.si_release_actor(self.root)
        contract = json.loads((SNAPSHOT / "si-release-contract.json").read_bytes())
        self.assertEqual(actor["profile"]["potential"], 0)
        self.assertEqual(actor["profile"]["skill_rank"], 12)
        self.assertEqual(proof["native_inputs"], contract["native_inputs"])
        self.assertEqual(proof["contract_sha256"], hashlib.sha256(
            (SNAPSHOT / "si-release-contract.json").read_bytes()).hexdigest())
        self.assertEqual(len(proof["native_record_sha256"]), 4)
        self.assertEqual(proof["input_policy"], "rank12_constant_no_dynamic_attribute_input")
        self.assertEqual(len(actor["rules"]), 2)
        self.assertEqual(actor["quotes"]["link"]["quote_basis"]["multiplier"], 7.5)

    def test_si_export_refuses_stale_source_and_changed_recipient_contract(self):
        contract_path = SNAPSHOT / "si-release-contract.json"
        contract = json.loads(contract_path.read_bytes())
        read_bytes = Path.read_bytes
        for change, message in (("source", "Stale Si release source"),
                                ("recipient", "review before exporting"),
                                ("magnitude", "amplification disagree")):
            with self.subTest(change=change):
                changed = json.loads(json.dumps(contract))
                if change == "source":
                    changed["source_hashes"]["assets/data/character_skills/si.json"] = "0" * 64
                elif change == "recipient":
                    changed["buff_policy"]["recipient"] = "self"
                else:
                    changed["parameters"][-1]["enhance_final"] = .5

                def substitute(path):
                    return json.dumps(changed).encode() if path == contract_path else read_bytes(path)

                with patch.object(Path, "read_bytes", substitute), self.assertRaisesRegex(ValueError, message):
                    self.exporter.si_release_actor(self.root)

    def test_only_registered_checkouts_and_named_repo_outputs_are_selected(self):
        listing = f"worktree {self.root.as_posix()}\nHEAD {'a' * 40}\n"
        with tempfile.TemporaryDirectory() as directory, patch.object(self.exporter.subprocess, "check_output", return_value=listing):
            with self.assertRaisesRegex(ValueError, "registered checkout"):
                self.exporter.reviewed_locations(directory, SNAPSHOT)
            with self.assertRaisesRegex(ValueError, "Output must"):
                self.exporter.reviewed_locations(self.root, directory)
            source, output = self.exporter.reviewed_locations(self.root, SNAPSHOT)
            self.assertEqual(source, self.root)
            self.assertEqual(output.resolve(), SNAPSHOT)
