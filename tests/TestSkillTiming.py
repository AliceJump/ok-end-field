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
            if cid not in ("chr_0031_mifu", "chr_0034_typhoea"):
                self.assertTrue(store.profiles(cid, "battle"), cid)
            else:
                self.assertEqual(store.profiles(cid, "battle"), ())
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

    def test_window_whitelist_and_actionable_boundaries(self):
        (profile,) = load_skill_timings().profiles("佩丽卡", "battle")
        (other,) = load_skill_timings().profiles("狼卫", "battle")
        self.assertFalse(profile.allows(profile.hard_lock - 0.01, (profile,)))
        self.assertTrue(profile.allows(1.1, (profile,)))  # explicit allow-next window
        self.assertLess(profile.actionable, profile.duration)
        self.assertFalse(profile.allows(profile.actionable - 0.01, (other,)))
        self.assertTrue(profile.allows(profile.actionable + 0.01, (other,)))
        self.assertTrue(profile.allows(profile.actionable + 0.01, ()))

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
        self.keys = []
        self.messages = []
        self.mouse = []
        self.link = False
        self.ults = set()
        self.exits = []
        self.exit_check_count = 0
        self.monitored = 0
        self._battle_team = None

    def active_time(self):
        return self.now

    def next_frame(self):
        self.now += 0.05

    def sleep(self, seconds):
        self.now += seconds

    def log_info(self, message, **kwargs):
        self.messages.append(message)

    log_warning = log_info

    def get_battle_config(self, key, default=None):
        if key == KEY_TIMING_ROTATION:
            return True
        if key == "完成通知":
            return False
        raise AssertionError(f"Read legacy combat strategy: {key}")

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
        return ["佩丽卡", "狼卫", "陈千语", "管理员"], True


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

    def test_monitor_ready_link_and_nonblocking_alt_ult(self):
        task = FakeTask()
        logic = logic_for(task)
        task.link = True
        task.ults = {"1"}
        logic.step()
        self.assertEqual(task.keys, ["e"])
        task.now = 100
        logic.step()
        self.assertEqual(task.keys, ["e", "ult_1"])
        self.assertEqual(logic.started, 100)
        self.assertEqual(task.now, 100)

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

    def test_fractional_cost_channel_waits_for_a_detectable_skill_bar(self):
        task = FakeTask()
        task.points = 0
        logic = logic_for(task)
        logic.team[0] = "梨诺"
        logic.step()
        self.assertEqual(task.keys, [])
        task.points = 1
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertIsNotNone(logic.pending)
        task.points = 0
        task.now = 0.1
        logic.step()
        self.assertIsNone(logic.pending)
        self.assertEqual(logic.cursor, 1)
        task.now = 1
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(task.mouse[-1], "down")

    def test_detection_exception_releases_held_mouse(self):
        task = FakeTask()
        with patch.object(task, "detect_team_stable", side_effect=ValueError("bad frame")):
            self.assertFalse(TimedCombatLogic(task).run(deadline=2))
        self.assertEqual(task.mouse[-1], "up")

    def test_missing_state_mapping_preserves_other_team_slots(self):
        task = FakeTask()
        task.detect_team_stable = lambda **kwargs: (["弭弗", "佩丽卡", "提弗洛斯", "洛茜"], True)
        logic = TimedCombatLogic(task, load_skill_timings())
        logic._detect_team(1)
        self.assertEqual(set(logic.order), {"2", "4"})
        task.link = True
        logic.step()
        self.assertNotIn("e", task.keys)  # Unknown staged link owner must not get a guessed timer.
