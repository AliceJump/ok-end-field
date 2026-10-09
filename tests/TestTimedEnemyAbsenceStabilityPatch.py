import unittest

from src.data.combat_observation import EnemyPresence
from src.patches.timed_enemy_absence_stability_patch import (
    _ABSENT_CONFIRM_SECONDS,
    _enemy_operation_paused_with_stability,
)
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class _Task:
    def __init__(self):
        self.state = EnemyPresence.UNKNOWN
        self.logs = []

    def probe_enemy_presence(self):
        return self.state

    def log_info(self, message):
        self.logs.append(message)


class _Logic(TimedCombatLogic):
    def __init__(self):
        self.task = _Task()
        self.now = 1.0
        self.enemy_pause_started = None
        self._enemy_presence_confirmed = True

    def _clock(self):
        return self.now


class TestTimedEnemyAbsenceStabilityPatch(unittest.TestCase):
    def test_absent_must_persist_for_confirmation_window_before_pause(self):
        logic = _Logic()
        logic.task.state = EnemyPresence.ABSENT

        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        self.assertEqual(logic._enemy_absent_candidate_since, 1.0)
        self.assertIsNone(logic.enemy_pause_started)

        logic.now += _ABSENT_CONFIRM_SECONDS - 0.01
        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        self.assertIsNone(logic.enemy_pause_started)
        self.assertEqual(logic.task.logs, [])

        logic.now += 0.01
        self.assertTrue(_enemy_operation_paused_with_stability(logic))
        self.assertAlmostEqual(logic.enemy_pause_started, 1.0 + _ABSENT_CONFIRM_SECONDS)
        self.assertEqual(len(logic.task.logs), 1)
        self.assertIn("连续未见敌人证据", logic.task.logs[0])

    def test_unknown_breaks_pending_absence_confirmation(self):
        logic = _Logic()
        logic.task.state = EnemyPresence.ABSENT
        self.assertFalse(_enemy_operation_paused_with_stability(logic))

        logic.now += 0.30
        logic.task.state = EnemyPresence.UNKNOWN
        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        self.assertIsNone(logic._enemy_absent_candidate_since)

        logic.now += 0.10
        logic.task.state = EnemyPresence.ABSENT
        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        restarted_at = logic.now

        logic.now = restarted_at + _ABSENT_CONFIRM_SECONDS - 0.01
        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        logic.now += 0.01
        self.assertTrue(_enemy_operation_paused_with_stability(logic))

    def test_present_resets_candidate_and_resumes_immediately(self):
        logic = _Logic()
        logic.task.state = EnemyPresence.ABSENT
        self.assertFalse(_enemy_operation_paused_with_stability(logic))

        logic.now += _ABSENT_CONFIRM_SECONDS
        self.assertTrue(_enemy_operation_paused_with_stability(logic))
        paused_at = logic.enemy_pause_started

        logic.now += 0.20
        logic.task.state = EnemyPresence.UNKNOWN
        self.assertTrue(_enemy_operation_paused_with_stability(logic))
        self.assertEqual(logic.enemy_pause_started, paused_at)

        logic.now += 0.05
        logic.task.state = EnemyPresence.PRESENT
        self.assertFalse(_enemy_operation_paused_with_stability(logic))
        self.assertIsNone(logic.enemy_pause_started)
        self.assertIsNone(logic._enemy_absent_candidate_since)
        self.assertIn("敌人重新出现", logic.task.logs[-1])


if __name__ == "__main__":
    unittest.main()
