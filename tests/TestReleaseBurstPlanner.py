"""Release gains reach actual scheduler dispatch without the research simulator."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data.release_burst_planner import SNAPSHOT, ReleaseBurstAction, ReleaseBurstPlanner, read_snapshot
from src.data.skill_timing import load_skill_timings
from src.data.team_phase_planner import BurstAction, CombatPhase, make_burst_plan
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class TestReleaseBurstPlanner(unittest.TestCase):
    def test_export_scope_and_selected_builds(self):
        rows = read_snapshot()
        self.assertEqual(len(rows), 32)
        self.assertEqual(sum(len(r["rules"]) for r in rows.values()), 8)
        self.assertEqual(len([r for r in rows.values() if r["rules"]]), 7)
        self.assertEqual(rows["管理员"]["profile"]["potential"], 3)
        self.assertEqual(rows["余烬"]["profile"]["character_level"], 80)
        self.assertEqual(rows["余烬"]["profile"]["skill_rank"], 9)
        for name in ("赛希", "梨诺", "洁尔佩塔", "噗切娜", "伊冯"):
            self.assertFalse(rows[name]["rules"])
        for row in rows.values():
            self.assertNotIn("link", row["quotes"])

    def test_changed_snapshot_fails_hash_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "index.json").write_bytes((SNAPSHOT / "index.json").read_bytes())
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


class TestReleaseBurstDispatch(unittest.TestCase):
    def make_logic(self):
        task = FakeTask()
        task.ults = {"1", "2"}
        task.sp = 300
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(["安塔尔", "莱万汀", "佩丽卡", "狼卫"], reset_runtime=True)
        return task, logic

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
