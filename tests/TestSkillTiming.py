import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.BattleConfig import DEFAULT_BATTLE_CONFIG, KEY_TIMING_ROTATION
from src.data.skill_timing import SNAPSHOT, SkillTiming, SkillTimingStore, load_skill_timings
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class TestSkillTiming(unittest.TestCase):
    def test_all_native_characters_have_data_and_explicit_runtime_coverage(self):
        store = load_skill_timings()
        with_skills = 0
        for cid in store.index["characters"]:
            if cid == "chr_9000_endmin":
                self.assertEqual(store.profiles(cid, "battle"), ())
                continue
            self.assertTrue(any(skill.startswith(cid + "_") for skill in store.index["skills"]), cid)
            self.assertTrue(store.profiles(cid, "ult"), cid)
            self.assertTrue(store.profiles(cid, "battle"), cid)
            if cid != "chr_0028_wulfa":
                self.assertTrue(store.profiles(cid, "link"), cid)
            else:
                self.assertEqual(store.profiles(cid, "link"), ())
            with_skills += 1
        self.assertEqual(with_skills, 33)
        self.assertEqual(store.profiles("?", "battle"), ())

    def test_patch_cost_and_battle_semantics(self):
        (profile,) = load_skill_timings().profiles("佩丽卡", "battle")
        self.assertEqual(profile.skill_id, "chr_0004_pelica_normal_skill")
        self.assertEqual(profile.skill_points, 1)
        self.assertEqual(profile.cooldown, 0)
        self.assertAlmostEqual(profile.duration, 155 / 30)

    def test_admin_gender_is_not_guessed(self):
        profiles = load_skill_timings().profiles("管理员", "ult")
        self.assertEqual(len(profiles), 2)
        self.assertEqual({profile.duration for profile in profiles}, {215 / 30, 250 / 30})

    def test_window_whitelist_and_effect_handoff_boundaries(self):
        (profile,) = load_skill_timings().profiles("佩丽卡", "battle")
        (other,) = load_skill_timings().profiles("狼卫", "battle")
        self.assertAlmostEqual(profile.effect_start, 13 / 30)
        self.assertAlmostEqual(profile.handoff, 13 / 30 + 0.05)
        self.assertFalse(profile.committed(profile.handoff - 0.01))
        self.assertTrue(profile.committed(profile.handoff + 0.01))
        self.assertTrue(profile.allows(1.1, (profile,)))  # explicit same-actor allow-next window
        self.assertLess(profile.handoff, profile.hard_lock)
        self.assertFalse(profile.allows(profile.actionable - 0.01, (other,)))
        self.assertTrue(profile.allows(profile.actionable + 0.01, (other,)))

    def test_normal_attack_finisher_sp_gain_comes_from_lossless_records(self):
        store = load_skill_timings()
        self.assertEqual(store.normal_attack_sp_gain("庄方宜"), 20)
        self.assertEqual(store.normal_attack_sp_gain("诀"), 20)
        self.assertEqual(store.normal_attack_sp_gain("梨诺"), 18)
        self.assertGreaterEqual(store.global_normal_attack_sp_gain(), 20)

    def test_state_skill_is_derived_from_end_variant_and_native_actions(self):
        store = load_skill_timings()
        spec = store.battle_state("梨诺")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.base_skill_id, "chr_0035_liino_normal_skill")
        self.assertEqual(spec.end_skill_id, "chr_0035_liino_normal_skill_end")
        self.assertAlmostEqual(spec.duration, 60.0)
        self.assertEqual(spec.end_cooldown, 3)

        ultimate = store.ultimate_state("梨诺")
        self.assertIsNotNone(ultimate)
        self.assertEqual(ultimate.base_skill_id, "chr_0035_liino_ultimate_skill")
        self.assertEqual(ultimate.end_skill_id, "chr_0035_liino_normal_skill_end")
        self.assertGreater(ultimate.duration, 0)

    def test_full_record_is_lazy_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "index.json").write_bytes((SNAPSHOT / "index.json").read_bytes())
            store = SkillTimingStore(path)
            self.assertTrue(store.profiles("perlica", "battle"))
            (path / "records.json.gz").write_bytes(b"broken")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                store.record("chr_0004_pelica_normal_skill")

    def test_lossless_record_preserves_empty_string_and_raw_curve(self):
        # Check the packaged records themselves, not a hand-written fixture.
        compressed = (SNAPSHOT / "records.json.gz").read_bytes()
        index = load_skill_timings().index
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), index["records_sha256"])
        records = json.loads(gzip.decompress(compressed))
        self.assertEqual(len(records), 1348)
        text = json.dumps(records)
        self.assertIn('"rawHex"', text)
        self.assertIn('"blackboardKey": ""', text)


class FakeTask:
    def __init__(self):
        self.now = 0.0
        self.points = 1
        self.sp = None
        self.keys = []
        self.messages = []
        self.mouse = []
        self.link = False
        self.ults = set()
        self.exits = []
        self.exit_check_count = 0
        self.monitored = 0
        self._battle_team = None
        self._battle_team_disabled_slots = set()
        self.detected_team = ["佩丽卡", "狼卫", "陈千语", "管理员"]

    def active_time(self):
        return self.now

    def next_frame(self):
        self.now += 0.05

    def sleep(self, seconds):
        self.now += seconds

    def log_info(self, message, **kwargs):
        self.messages.append(message)

    log_warning = log_info
    log_debug = log_info

    def get_battle_config(self, key, default=None):
        if key == KEY_TIMING_ROTATION:
            return True
        if key == "完成通知":
            return False
        raise AssertionError(f"Read legacy combat strategy: {key}")

    def get_skill_bar_sp(self):
        return self.points * 100.0 if self.sp is None else self.sp

    def get_skill_bar_count(self):
        return self.points

    def send_key(self, token):
        self.keys.append(token)

    def is_link_skill_ready(self):
        return self.link

    def use_link_skill(self):
        self.link = False
        self.keys.append("e")
        return True

    def _find_battle_ult(self, name):
        return name[4:] in self.ults

    def use_ult(self, ult_sequence=None, wait_for_team_recovery=True):
        self.keys.append("ult_" + ult_sequence)
        self.ults.remove(ult_sequence)
        if wait_for_team_recovery:
            self.now += 2
        return True

    def mouse_up(self, key):
        self.mouse.append("up")

    def mouse_down(self, key):
        self.mouse.append("down")

    def active_and_send_mouse_delta(self, **kwargs):
        pass

    def click(self, **kwargs):
        pass

    def in_combat(self, **kwargs):
        return True

    def _check_single_exit_condition(self):
        self.monitored += 1
        return self.exits.pop(0) if self.exits else False

    def is_combat_ended(self, condition):
        self.exit_check_count = self.exit_check_count + 1 if condition else 0
        return self.exit_check_count >= 2

    def detect_team_stable(self, **kwargs):
        return list(self.detected_team), True


def logic_for(task):
    logic = TimedCombatLogic(task, load_skill_timings())
    logic.team = ["佩丽卡", "狼卫", "陈千语", "管理员"]
    logic.order = ["1", "2", "3", "4"]
    return logic


class TestTimedCombat(unittest.TestCase):
    def test_opt_in_entry_bypasses_legacy_config(self):
        self.assertFalse(DEFAULT_BATTLE_CONFIG[KEY_TIMING_ROTATION])
        task = FakeTask()
        self.assertFalse(AutoCombatLogic(task).run(start_sleep=0, deadline=1))
        self.assertTrue(task.keys)
        self.assertEqual(task.mouse[-1], "up")

    def test_consumption_confirmation_and_resource_driven_next_skill(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(logic.cursor, 0)
        task.now = 0.1
        task.points = 0
        logic.step()
        self.assertEqual(logic.cursor, 1)
        task.now = logic.started + max(profile.actionable for profile in logic.active) + 0.01
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(task.mouse[-1], "down")
        task.points = 1
        logic.step()
        self.assertEqual(task.keys, ["1", "2"])
        self.assertLess(task.now, 6)

    def test_failed_attempt_preserves_sequence_and_guard(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.step()
        task.now = 1
        logic.step()
        self.assertEqual(logic.cursor, 0)
        self.assertEqual(task.keys, ["1"])  # Pending timeout is not an early cancel proof.
        task.now = logic.started + max(profile.actionable for profile in logic.active) + 0.01
        logic.step()
        self.assertEqual(task.keys, ["1", "1"])

    def test_different_slot_can_handoff_after_effect_before_exclusive(self):
        task = FakeTask()
        logic = logic_for(task)

        logic.step()
        self.assertEqual(task.keys, ["1"])
        task.points = 0
        task.now = 0.1
        logic.step()
        self.assertEqual(logic.cursor, 1)

        first = logic.active[0]
        self.assertLess(first.handoff, first.actionable)
        task.points = 1
        task.now = logic.started + first.handoff + 0.01
        logic.step()
        self.assertEqual(task.keys, ["1", "2"])

    def test_same_slot_stays_conservative_after_effect_start(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.order = ["1"]

        logic.step()
        task.points = 0
        task.now = 0.1
        logic.step()
        task.points = 1
        first = logic.active[0]
        task.now = logic.started + first.handoff + 0.01
        logic.step()
        self.assertEqual(task.keys, ["1"])

        task.now = logic.started + first.actionable + 0.01
        logic.step()
        self.assertEqual(task.keys, ["1", "1"])

    def test_skill_timeline_does_not_release_normal_attack(self):
        task = FakeTask()
        logic = logic_for(task)
        logic._hold(True)
        self.assertEqual(task.mouse[-1], "down")
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(task.mouse[-1], "down")
        self.assertNotIn("up", task.mouse)

    def test_force_hold_reasserts_normal_attack(self):
        task = FakeTask()
        logic = logic_for(task)
        logic._hold(True)
        logic._hold(True)
        self.assertEqual(task.mouse.count("down"), 1)
        logic._hold(True, force=True)
        self.assertEqual(task.mouse.count("down"), 2)

    def test_full_skill_points_preempt_link_and_ult_with_planned_battle(self):
        task = FakeTask()
        task.points = 3
        task.link = True
        task.ults = {"1"}
        logic = logic_for(task)

        logic.step()

        self.assertEqual(task.keys, ["1"])
        self.assertTrue(any("技力已满，防溢出抢占尝试战技 1" in message for message in task.messages))
        self.assertTrue(task.link)
        self.assertIn("1", task.ults)

    def test_below_full_skill_points_keeps_link_priority(self):
        task = FakeTask()
        task.points = 2
        task.link = True
        task.ults = {"1"}
        logic = logic_for(task)

        logic.step()

        self.assertEqual(task.keys, ["e"])

    def test_monitor_ready_link_and_hud_blocked_alt_ult(self):
        task = FakeTask()
        logic = logic_for(task)
        task.link = True
        task.ults = {"1"}
        logic.step()
        self.assertEqual(task.keys, ["e"])
        task.now = 100
        logic.step()
        self.assertEqual(task.keys, ["e", "ult_1"])
        self.assertEqual(task.now, 102)
        self.assertEqual(logic.active, ())
        self.assertTrue(any("HUD 动画锁 2.00s" in message for message in task.messages))

    def test_no_battle_sends_no_inputs(self):
        task = FakeTask()
        self.assertFalse(TimedCombatLogic(task).run(no_battle=True, deadline=1))
        self.assertEqual(task.keys, [])
        self.assertNotIn("down", task.mouse)

    def test_long_channel_keeps_exit_monitor_and_deadline(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.active = (SkillTiming("channel", 70, 60, 0, 0, ()),)
        self.assertFalse(logic.run(deadline=2))
        self.assertGreaterEqual(task.monitored, 3)
        self.assertEqual(task.keys, [])
        self.assertGreaterEqual(task.mouse.count("down"), 2)
        self.assertEqual(task.mouse[-1], "up")

    def test_exit_confirmation_resets_and_blocks_actions(self):
        task = FakeTask()
        task.exit_check_count = 1  # A previous run must not count towards this combat's confirmation.
        task.exits = [True, False, True, True]
        self.assertTrue(TimedCombatLogic(task).run(deadline=3))
        self.assertGreaterEqual(task.monitored, 4)
        self.assertEqual(task.mouse[-1], "up")

    def test_low_cost_skill_is_assumed_success_after_short_pause(self):
        task = FakeTask()
        task.points = 0
        logic = logic_for(task)
        logic.team[0] = "梨诺"
        logic.step()
        self.assertEqual(task.keys, [])

        task.points = 1
        before = task.now
        logic.step()

        self.assertEqual(task.keys, ["1"])
        self.assertIsNone(logic.pending)
        self.assertEqual(logic.cursor, 1)
        self.assertAlmostEqual(task.now - before, 0.1)
        self.assertTrue(
            any("消耗证据 25 SP <= 25，按键后直接视为成功" in message for message in task.messages)
        )

    def test_precise_sp_readiness_uses_partial_bar_value(self):
        task = FakeTask()
        task.sp = 24.0
        logic = logic_for(task)
        logic.team[0] = "梨诺"

        logic.step()
        self.assertEqual(task.keys, [])

        task.sp = 25.0
        logic.step()
        self.assertEqual(task.keys, ["1"])

    def test_state_skill_is_not_pressed_again_while_state_is_active(self):
        task = FakeTask()
        task.sp = 25.0
        logic = logic_for(task)
        logic.team[0] = "梨诺"
        logic.order = ["1"]
        logic.state_specs["1"] = logic.store.battle_state("梨诺")

        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertGreater(logic.state_until["1"], task.now)

        task.sp = 300.0
        task.now += 1.0
        logic.step()
        self.assertEqual(task.keys, ["1"])

    def test_ultimate_state_prevents_auto_pressing_its_end_button(self):
        task = FakeTask()
        task.sp = 100.0
        task.ults = {"1"}
        logic = logic_for(task)
        logic.team = ["梨诺", "庄方宜", "诀", "佩丽卡"]
        logic.order = ["1"]
        logic.ult_order = ["1"]
        logic.ult_state_specs["1"] = logic.store.ultimate_state("梨诺")

        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertGreater(logic.state_until["1"], task.now)

        task.sp = 100.0
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])

    def test_dead_slot_is_masked_by_position_without_renumbering_team(self):
        task = FakeTask()
        logic = logic_for(task)
        task._battle_team = list(logic.team)
        logic.order = ["2", "1"]
        logic.ult_order = ["2", "1"]
        task.detected_team = ["佩丽卡", "?", "陈千语", "管理员"]

        logic._refresh_team_slots(1)

        self.assertEqual(logic.team, ["佩丽卡", "狼卫", "陈千语", "管理员"])
        self.assertEqual(logic.disabled_slots, {"2"})
        self.assertEqual(task._battle_team_disabled_slots, {1})

        task.sp = 100
        logic.step()
        self.assertEqual(task.keys, ["1"])

    def test_partial_scan_with_known_slot_mismatch_does_not_mask_anyone(self):
        task = FakeTask()
        logic = logic_for(task)
        task.detected_team = ["佩丽卡", "?", "狼卫", "管理员"]

        logic._refresh_team_slots(1)

        self.assertEqual(logic.disabled_slots, set())
        self.assertTrue(any("位置不匹配" in message for message in task.messages))

    def test_dead_slot_cancels_its_pending_skill_confirmation(self):
        task = FakeTask()
        logic = logic_for(task)
        logic.pending = (100.0, "2", 100.0)
        logic.active_slot = "2"
        task.detected_team = ["佩丽卡", "?", "陈千语", "管理员"]

        logic._refresh_team_slots(1)

        self.assertIsNone(logic.pending)
        self.assertEqual(logic.active, ())
        self.assertIsNone(logic.active_slot)

    def test_detection_exception_releases_held_mouse(self):
        task = FakeTask()
        with patch.object(task, "detect_team_stable", side_effect=ValueError("bad frame")):
            self.assertFalse(TimedCombatLogic(task).run(deadline=2))
        self.assertEqual(task.mouse[-1], "up")

    def test_mechanic_team_keeps_native_entry_slots_and_disables_flat_optimizer(self):
        task = FakeTask()
        task.detect_team_stable = lambda **kwargs: (["弭弗", "佩丽卡", "提弗洛斯", "洛茜"], True)
        logic = TimedCombatLogic(task, load_skill_timings())
        logic._detect_team(1)

        self.assertEqual(set(logic.order), {"1", "2", "3", "4"})
        self.assertIsNone(logic.plan)
        self.assertEqual(logic.battle_phase_indices["1"], 0)
        self.assertTrue(any("不能压成单战技循环" in message for message in task.messages))

    def test_mifu_first_cast_uses_net_cost_then_prioritizes_second_phase(self):
        task = FakeTask()
        task.sp = 100.0
        logic = TimedCombatLogic(task, load_skill_timings())
        logic.team = ["弭弗", "佩丽卡", "诀", "洛茜"]
        logic.order = ["1"]
        logic.ult_order = []
        logic.team_mechanics = {"1": logic.mechanics["弭弗"]}
        logic.battle_phase_indices = {"1": 0}

        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(logic.pending, (100.0, "1", 50.0))
        self.assertEqual(logic.active[0].skill_id, "chr_0031_mifu_normalskill_1")

        task.now = 0.1
        task.sp = 50.0
        logic.step()
        self.assertEqual(logic.battle_phase_indices["1"], 1)
        self.assertEqual(logic.forced_battle_token, "1")
        self.assertEqual(task.keys, ["1"])

        task.now = 0.5
        logic.step()
        self.assertEqual(task.keys, ["1", "1"])
        self.assertEqual(logic.active[0].skill_id, "chr_0031_mifu_normalskill_2")
        self.assertEqual(logic.pending, (50.0, "1", 50.0))

    def test_zhuang_ultimate_prioritizes_free_first_battle(self):
        task = FakeTask()
        task.sp = 0.0
        task.ults = {"1"}
        logic = TimedCombatLogic(task, load_skill_timings())
        logic.team = ["庄方宜", "佩丽卡", "诀", "洛茜"]
        logic.order = ["1"]
        logic.ult_order = ["1"]
        logic.team_mechanics = {"1": logic.mechanics["庄方宜"]}

        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertIn("1", logic.free_battle_once)
        self.assertEqual(logic.forced_battle_token, "1")

        logic.step()
        self.assertEqual(task.keys, ["ult_1", "1"])
        self.assertNotIn("1", logic.free_battle_once)
        self.assertIsNone(logic.forced_battle_token)

    def test_yvonne_ultimate_protects_forced_main_control_window_from_other_ults(self):
        task = FakeTask()
        task.sp = 0.0
        task.ults = {"1", "2"}
        logic = TimedCombatLogic(task, load_skill_timings())
        logic.team = ["伊冯", "佩丽卡", "诀", "洛茜"]
        logic.order = ["3"]
        logic.ult_order = ["1", "2"]
        logic.team_mechanics = {"1": logic.mechanics["伊冯"]}

        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertEqual(logic.forced_main_control_slot, "1")
        self.assertGreater(logic.forced_main_control_until, task.now)

        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertIn("2", task.ults)

        task.now = logic.forced_main_control_until + 0.01
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "ult_2"])

