import unittest
from unittest.mock import patch

import numpy as np
from ok import Box

from src.data.combat_observation import EnemyPresence
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


class _ExitTask(FakeTask):
    _check_single_exit_condition = BattleMixin._check_single_exit_condition
    is_combat_ended = BattleMixin.is_combat_ended
    ULT_EXIT_DELAY = BattleMixin.ULT_EXIT_DELAY

    def __init__(self):
        super().__init__()
        self.now = 20.0
        self.has_lv = False
        self.team_visible = False
        self.settlement = False
        self.presence = EnemyPresence.ABSENT
        self.points = -1

    def find_feature(self, **kwargs):
        return self.settlement

    def ocr_lv(self):
        return self.has_lv

    def in_team(self):
        return self.team_visible

    def probe_enemy_presence(self):
        return self.presence


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

    def find_one(self, feature, box):
        slot = int(feature.split("_")[1])
        index = self.boxes.index(box)
        if slot in self.visible_slots and index == 4 - self.member_count + slot - 1:
            return Box(box.x, box.y, 10, 10, confidence=0.99)
        return None

    def log_debug(self, message):
        pass


class TestCombatPresenceLifecycle(unittest.TestCase):
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

    def test_missing_team_icons_do_not_end_combat_while_sp_hud_remains(self):
        task = _ExitTask()
        for points in (0, 1, 3):
            with self.subTest(points=points):
                task.points = points
                self.assertFalse(task._check_single_exit_condition())

    def test_level_template_does_not_end_combat_while_sp_hud_remains(self):
        task = _ExitTask()
        task.has_lv = True
        task.team_visible = True
        task.points = 0
        self.assertFalse(task._check_single_exit_condition())

    def test_visible_enemy_blocks_exit_when_team_and_sp_hud_temporarily_disappear(self):
        task = _ExitTask()
        task.presence = EnemyPresence.PRESENT
        self.assertFalse(task._check_single_exit_condition())

    def test_real_exit_still_requires_two_consecutive_observations(self):
        task = _ExitTask()
        task.has_lv = True
        task.team_visible = True
        self.assertFalse(task.is_combat_ended(task._check_single_exit_condition()))
        task.points = 1
        self.assertFalse(task.is_combat_ended(task._check_single_exit_condition()))
        task.points = -1
        self.assertFalse(task.is_combat_ended(task._check_single_exit_condition()))
        self.assertTrue(task.is_combat_ended(task._check_single_exit_condition()))

    def test_settlement_wins_over_lingering_enemy_and_combat_hud(self):
        task = _ExitTask()
        task.settlement = True
        task.points = 3
        task.presence = EnemyPresence.PRESENT
        task._last_ult_release_time = task.now
        self.assertTrue(task._check_single_exit_condition())


if __name__ == "__main__":
    unittest.main()
