import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.core.BattleConfig import BATTLE_CONFIG_TYPE, DEFAULT_BATTLE_CONFIG, KEY_TIMING_ROTATION
from src.data.combat_observation import ActionBlockReason, EnemyPresence
from src.data.endaxis_timeline import EndAxisAction, EndAxisScenario
from src.data.skill_timing import load_skill_timings
from src.patches.endaxis_timed_combat_patch import (
    KEY_TIMING_ENDAXIS,
    _commit_action,
    _plan_elapsed,
    _sync_due_action,
    install_endaxis_timed_combat_patch,
    install_endaxis_timed_config,
)
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from src.tasks.mixin.battle_mixin import BattleMixin, ULT_RELEASE_MODE_ALT, ULT_RELEASE_MODE_HOLD


class _ClockLogic:
    def __init__(self, actions):
        self.now = 0.0
        self._clock = lambda: self.now
        self._endaxis_scenario = EndAxisScenario("clock", "Clock", tuple(actions))
        self._endaxis_cursor = 0
        self._endaxis_attempt_index = None
        self._endaxis_frozen = False
        self._endaxis_frozen_elapsed = 0.0
        self._endaxis_frozen_wall = None
        self._endaxis_not_before = 0.0
        self._endaxis_clock_elapsed = 0.0
        self._endaxis_clock_wall = 0.0
        self.task = _RuntimeTask()


class _RuntimeTask:
    def __init__(self):
        self.now = 0.0
        self.sp = 300.0
        self.keys = []
        self.messages = []
        self.mouse = []
        self.block_reason = None
        self.ult_success = True
        self._battle_team = None
        self._battle_member_count = 1
        self._battle_team_disabled_slots = set()
        self._squad_dead_slots = set()

    def active_time(self):
        return self.now

    def log_info(self, message, **kwargs):
        self.messages.append(message)

    log_warning = log_info
    log_debug = log_info

    def active_and_send_mouse_delta(self, **kwargs):
        pass

    def mouse_down(self, key):
        self.mouse.append(("down", key))

    def mouse_up(self, key):
        self.mouse.append(("up", key))

    def send_key(self, token):
        self.keys.append(token)

    def sleep(self, seconds):
        self.now += float(seconds)

    def next_frame(self):
        pass

    def arm_combat_action_feedback_probe(self):
        pass

    def reset_combat_action_feedback_probe(self):
        self.block_reason = None

    def probe_combat_action_block_reason(self):
        return self.block_reason

    def recover_target_too_far(self):
        return False

    def _find_battle_ult(self, _key):
        return True

    def use_ult(self, ult_sequence=None, wait_for_team_recovery=True):
        return self.ult_success


class TestEndAxisPlanClock(unittest.TestCase):
    def test_timed_config_is_registered_under_timing_mode(self):
        install_endaxis_timed_config()
        self.assertEqual(DEFAULT_BATTLE_CONFIG[KEY_TIMING_ENDAXIS], "")
        self.assertIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE)
        self.assertIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"][True])
        self.assertNotIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"][False])

    def test_due_action_freezes_clock_until_commit(self):
        logic = _ClockLogic(
            [
                EndAxisAction("skill", 1, 360, 0),
                EndAxisAction("ultimate", 2, 480, 0),
            ]
        )

        logic.now = 0.5
        self.assertIsNone(_sync_due_action(logic))
        self.assertAlmostEqual(_plan_elapsed(logic), 0.5)

        logic.now = 1.2
        first = _sync_due_action(logic)
        self.assertEqual((first.kind, first.slot), ("skill", 1))
        self.assertAlmostEqual(_plan_elapsed(logic), 1.0)

        logic.now = 5.0
        self.assertAlmostEqual(_plan_elapsed(logic), 1.0)
        self.assertTrue(_commit_action(logic, 0))

        logic.now = 6.0
        self.assertIsNone(_sync_due_action(logic))
        self.assertAlmostEqual(_plan_elapsed(logic), 2.0)

        logic.now = 7.0
        second = _sync_due_action(logic)
        self.assertEqual((second.kind, second.slot), ("ultimate", 2))
        self.assertAlmostEqual(_plan_elapsed(logic), 3.0)


class _RecoveringRuntimeTask(_RuntimeTask):
    use_ult = BattleMixin.use_ult
    _has_detected_team_member = BattleMixin._has_detected_team_member
    _is_detected_team_frame_matched = BattleMixin._is_detected_team_frame_matched
    _log_detected_team_member_result = BattleMixin._log_detected_team_member_result
    arm_combat_action_feedback_probe = BattleMixin.arm_combat_action_feedback_probe
    reset_combat_action_feedback_probe = BattleMixin.reset_combat_action_feedback_probe
    probe_combat_action_block_reason = BattleMixin.probe_combat_action_block_reason

    def __init__(self, release_mode):
        super().__init__()
        self.release_mode = release_mode
        self.refreshes = 0
        self.frame = np.zeros((10, 10, 3), dtype=np.uint8)
        self.feedback_seen = []

    def get_battle_config(self, _key, _default):
        return self.release_mode

    def send_key_down(self, token):
        self.keys.append(("down", token))

    def send_key_up(self, token):
        self.keys.append(("up", token))

    def next_frame(self):
        self.refreshes += 1
        self.now += 0.05
        return self.frame

    def detect_team(self, _frame):
        return ["?"] if self.refreshes <= 2 else list(self._battle_team)

    def _read_combat_too_far_text_band(self):
        visible = 1 <= self.refreshes <= 2
        self.feedback_seen.append(visible)
        return (0.5, 0.5, 0.5, 0.5) if visible else None


class TestEndAxisTimedRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_endaxis_timed_combat_patch()

    def _logic(self, kind="skill", task=None):
        task = task if task is not None else _RuntimeTask()
        logic = TimedCombatLogic(
            task,
            load_skill_timings(),
            clock=lambda: task.now,
            wall_clock=lambda: task.now,
        )
        logic.team = ["佩丽卡"]
        logic.order = ["1"]
        task._battle_team = list(logic.team)
        logic.main_control.update = lambda *args, **kwargs: False
        logic.main_control.record_cast = lambda *args, **kwargs: None
        logic._sample_sp = lambda force=False: task.sp
        logic._endaxis_scenario = EndAxisScenario(
            "runtime",
            "Runtime",
            (EndAxisAction(kind, 1, 300, 30),),
        )
        logic._endaxis_cursor = 0
        logic._endaxis_attempt_index = None
        logic._endaxis_frozen = False
        logic._endaxis_frozen_elapsed = 0.0
        logic._endaxis_frozen_wall = None
        logic._endaxis_not_before = 0.0
        logic._endaxis_clock_elapsed = 0.0
        logic._endaxis_clock_wall = 0.0
        return task, logic

    def test_high_cost_battle_action_commits_only_after_sp_drop_confirmation(self):
        task, logic = self._logic()

        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(logic._endaxis_cursor, 0)
        self.assertEqual(logic._endaxis_attempt_index, 0)
        self.assertFalse(logic._endaxis_frozen)
        self.assertIsNotNone(logic.pending)

        task.now = 0.1
        task.sp = 190.0
        logic.step()

        self.assertEqual(logic._endaxis_cursor, 1)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertFalse(logic._endaxis_frozen)
        self.assertIsNone(logic.pending)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.1)

    def test_cancelled_battle_attempt_keeps_due_action_unconsumed_and_refreezes_clock(self):
        task, logic = self._logic()
        task.now = 0.25
        logic._endaxis_frozen = False
        logic._endaxis_clock_elapsed = 0.0
        logic._endaxis_clock_wall = 0.0
        logic._endaxis_attempt_index = 0
        logic.pending = (300.0, "1", 100.0)
        logic.active_slot = "1"
        logic.active_kind = "battle"

        logic._cancel_unstarted_action("battle", "1", task.now)

        self.assertEqual(logic._endaxis_cursor, 0)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertTrue(logic._endaxis_frozen)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.0)
        self.assertIsNone(logic.pending)

    def test_unconfirmed_frames_and_enemy_pause_preserve_running_attempt_clock(self):
        task, logic = self._logic()
        logic.step()
        for now in (0.05, 0.1):
            task.now = now
            logic.step()
            self.assertIsNotNone(logic.pending)
            self.assertFalse(logic._endaxis_frozen)
            self.assertAlmostEqual(_plan_elapsed(logic), now)
        task.now = 0.15
        task.probe_enemy_presence = lambda: EnemyPresence.ABSENT
        self.assertTrue(logic._enemy_operation_paused())
        self.assertFalse(logic._endaxis_frozen)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.15)
        task.now = 0.2
        task.sp = 190.0
        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(logic._endaxis_cursor, 1)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.2)

    def test_ultimate_wait_retains_transient_block_after_prompt_disappears(self):
        for release_mode in (ULT_RELEASE_MODE_ALT, ULT_RELEASE_MODE_HOLD):
            with self.subTest(release_mode=release_mode):
                task, logic = self._logic("ultimate", _RecoveringRuntimeTask(release_mode))
                logic.step()
                self.assertGreaterEqual(task.refreshes, 4)
                self.assertEqual(task.feedback_seen[:3], [False, True, True])
                self.assertIsNone(task._read_combat_too_far_text_band())
                self.assertEqual(logic._endaxis_cursor, 0)
                self.assertIsNone(logic._endaxis_attempt_index)
                self.assertTrue(logic._endaxis_frozen)
                self.assertAlmostEqual(_plan_elapsed(logic), 0.0)
                self.assertFalse(task._combat_action_feedback_armed)
                self.assertIsNone(task._combat_action_wait_block_reason)
                self.assertIsNone(logic.last_action_attempt)

    def test_blocked_ultimate_does_not_commit_and_refreezes_due_action(self):
        task, logic = self._logic("ultimate")
        task.block_reason = ActionBlockReason.TOO_FAR

        logic.step()

        self.assertEqual(logic._endaxis_cursor, 0)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertTrue(logic._endaxis_frozen)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.0)

    def test_clean_ultimate_feedback_commits_after_confirmation_frames(self):
        task, logic = self._logic("ultimate")

        logic.step()

        self.assertEqual(logic._endaxis_cursor, 1)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertFalse(logic._endaxis_frozen)
        self.assertGreater(_plan_elapsed(logic), 0.0)

    def test_optional_release_model_only_receives_confirmed_ultimate_start(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                task, logic = self._logic("ultimate")
                task.now = 0.25
                if blocked:
                    task.block_reason = ActionBlockReason.TOO_FAR
                release = SimpleNamespace(confirm=Mock())
                logic.release_burst = release
                logic.step()
                if blocked:
                    release.confirm.assert_not_called()
                    self.assertEqual(logic._endaxis_cursor, 0)
                else:
                    release.confirm.assert_called_once_with("1", "ult", 0.25)
                    self.assertGreater(task.now, 0.25)
                    self.assertEqual(logic._endaxis_cursor, 1)


if __name__ == "__main__":
    unittest.main()
