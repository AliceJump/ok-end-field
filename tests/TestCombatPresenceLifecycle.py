import unittest
from unittest.mock import patch

import numpy as np
from ok import Box

from src.image.enemy_health_probe import probe_enemy_presence_fast
from src.patches.timed_enemy_absence_stability_patch import _enemy_operation_paused_with_stability
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class _SpawnTask(FakeTask):
    def __init__(self, spawn_at=None):
        super().__init__()
        self.spawn_at = spawn_at
        self.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.points = 3
        self.link = True
        self.ults = {"1", "2", "3", "4"}
        self.cast_times = []

    def next_frame(self):
        super().next_frame()
        self.frame.fill(0)
        if self.spawn_at is not None and self.now >= self.spawn_at:
            self.frame[50:58, 800:900] = (102, 68, 255)
        return self.frame

    def probe_enemy_presence(self):
        return probe_enemy_presence_fast(self)

    def send_key(self, token):
        self.cast_times.append(self.now)
        super().send_key(token)

    def use_link_skill(self):
        self.cast_times.append(self.now)
        return super().use_link_skill()

    def use_ult(self, **kwargs):
        self.cast_times.append(self.now)
        return super().use_ult(**kwargs)


class _TeamHudTask:
    def __init__(self, member_count, visible_slots, disabled_slots=()):
        self.boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 5)]
        self.member_count = member_count
        self.visible_slots = set(visible_slots)
        self._battle_member_count = 0
        self._battle_team = ["known"] * member_count
        self._battle_team_disabled_slots = set(disabled_slots)

    def _battle_feature_boxes(self, prefix):
        return self.boxes

    def detect_team(self, frame=None):
        return ["?"] * 4

    def find_one(self, feature, box):
        slot = int(feature.split("_")[1])
        index = self.boxes.index(box)
        if slot in self.visible_slots and index == 4 - self.member_count + slot - 1:
            return Box(box.x, box.y, 10, 10, confidence=0.99)
        return None

    def log_debug(self, message):
        pass


class _PortraitTask:
    _is_detected_team_frame_matched = BattleMixin._is_detected_team_frame_matched
    _log_detected_team_member_result = BattleMixin._log_detected_team_member_result

    def __init__(self, frames):
        self.portraits = iter(frames)
        self._battle_member_count = 3
        self._battle_team = ["佩丽卡", "艾维文娜", "梨诺"]
        self.now = 0.0

    def active_time(self):
        return self.now

    def next_frame(self):
        self.now += 0.1
        return np.zeros((10, 10, 3), dtype=np.uint8)

    def detect_team(self, frame):
        return next(self.portraits)

    def sleep(self, seconds):
        self.now += seconds

    def log_info(self, message):
        pass


class TestCombatPresenceLifecycle(unittest.TestCase):
    def test_blocking_team_detection_ignores_unused_right_portrait_slots(self):
        task = _PortraitTask([["佩丽卡", "艾维文娜", "梨诺", "狼卫"], ["佩丽卡", "艾维文娜", "梨诺", "洛茜"]])
        self.assertEqual(
            BattleMixin.detect_team_stable(task, max_attempts=2),
            (["佩丽卡", "艾维文娜", "梨诺"], True),
        )

    def test_native_three_person_ultimate_animation_and_recovery_use_only_active_portraits(self):
        for hidden in (True, False):
            with self.subTest(hidden=hidden):
                portraits = ["?", "?", "?"] if hidden else ["佩丽卡", "艾维文娜", "梨诺"]
                task = _PortraitTask([[*portraits, "狼卫"], [*portraits, "洛茜"]])
                self.assertTrue(BattleMixin._has_detected_team_member(task, require_four_unknown=hidden))

    def test_ready_skills_wait_through_initial_partial_scans_and_confirmed_absence(self):
        for patched in (False, True):
            for kind in ("battle", "link", "ult"):
                with self.subTest(patched=patched, kind=kind):
                    task = _SpawnTask()
                    task.points = 3 if kind == "battle" else 0
                    task.link = kind == "link"
                    task.ults = {"1"} if kind == "ult" else set()
                    logic = TimedCombatLogic(task, clock=task.active_time)
                    method = (
                        _enemy_operation_paused_with_stability if patched else TimedCombatLogic._enemy_operation_paused
                    )
                    with patch.object(TimedCombatLogic, "_enemy_operation_paused", method):
                        self.assertFalse(logic.run(deadline=2.0))
                    self.assertEqual(task.keys, [])
                    self.assertIn("down", task.mouse)
                    self.assertIn("middle", task.clicks)
                    self.assertEqual(task.mouse[-1], "up")

    def test_first_positive_enemy_observation_releases_ready_skills_without_fixed_delay(self):
        task = _SpawnTask(spawn_at=0.8)
        logic = TimedCombatLogic(task, clock=task.active_time)
        with patch.object(TimedCombatLogic, "_enemy_operation_paused", _enemy_operation_paused_with_stability):
            self.assertFalse(logic.run(deadline=1.5))
        self.assertTrue(task.keys)
        self.assertTrue(all(timestamp >= task.spawn_at for timestamp in task.cast_times))
        self.assertLess(task.cast_times[0], task.spawn_at + 0.2)

    def test_reused_scheduler_requires_fresh_enemy_evidence_for_each_run(self):
        task = _SpawnTask()
        logic = TimedCombatLogic(task, clock=task.active_time)
        logic._enemy_presence_confirmed = True
        with patch.object(TimedCombatLogic, "_enemy_operation_paused", _enemy_operation_paused_with_stability):
            self.assertFalse(logic.run(deadline=0.6))
        self.assertEqual(task.keys, [])

    def test_missing_first_skill_keeps_original_team_and_ultimate_positions(self):
        for member_count in (3, 4):
            with self.subTest(member_count=member_count):
                task = _TeamHudTask(member_count, visible_slots=(2, 3))
                self.assertTrue(BattleMixin.in_team(task))
                self.assertEqual(task._battle_member_count, member_count)
                ultimate = BattleMixin._find_battle_ult(task, "ult_3")
                self.assertIsNotNone(ultimate)
                self.assertEqual(ultimate.x, task.boxes[4 - member_count + 2].x)

    def test_last_survivor_keeps_original_slot_number(self):
        task = _TeamHudTask(4, visible_slots=(4,), disabled_slots=(0, 1, 2))
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 4)
        ultimate = BattleMixin._find_battle_ult(task, "ult_4")
        self.assertIsNotNone(ultimate)
        self.assertEqual(ultimate.x, task.boxes[-1].x)

    def test_native_short_team_ultimates_use_right_aligned_skill_boxes(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                task = _TeamHudTask(count, visible_slots=range(1, count + 1))
                task._battle_team = None
                self.assertTrue(BattleMixin.in_team(task))
                self.assertEqual(task._battle_member_count, count)
                for slot in range(1, count + 1):
                    ultimate = BattleMixin._find_battle_ult(task, f"ult_{slot}")
                    self.assertIsNotNone(ultimate)
                    self.assertEqual(ultimate.x, task.boxes[4 - count + slot - 1].x)


if __name__ == "__main__":
    unittest.main()
