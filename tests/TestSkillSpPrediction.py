import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class _Task:
    def __init__(self):
        self.frame = np.zeros((4, 4, 3), dtype=np.uint8)

    def is_skill_bar_full_fast(self):
        return False

    def get_skill_bar_sp(self):
        return -1.0


class TestSkillSpPrediction(unittest.TestCase):
    @staticmethod
    def _logic(scheduler_now=100.0, wall_now=11.5):
        logic = TimedCombatLogic.__new__(TimedCombatLogic)
        logic.task = _Task()
        logic._clock = lambda: scheduler_now
        logic._sp_wall_clock = lambda: wall_now
        logic.cached_sp = 160.0
        logic.expected_sp = 160.0
        logic.last_observed_sp = 235.0
        logic.last_visual_sp_wall_time = 10.0
        logic.last_sp_probe_at = 90.0
        logic.next_sp_probe_at = 0.0
        logic.sp_pressure_threshold = 265.0
        return logic

    def test_probe_expected_uses_visual_to_visual_wall_clock_window(self):
        logic = self._logic(scheduler_now=100.0, wall_now=11.5)

        self.assertEqual(logic._project_probe_expected(), 172.0)

    def test_assumed_spend_updates_prediction_not_visual_anchor(self):
        logic = self._logic(scheduler_now=100.0, wall_now=11.5)

        logic._note_assumed_sp_spend(160.0, 25.0)

        self.assertEqual(logic.expected_sp, 135.0)
        self.assertEqual(logic.cached_sp, 135.0)
        self.assertEqual(logic.last_observed_sp, 235.0)
        self.assertEqual(logic.last_visual_sp_wall_time, 10.0)

    def test_visual_probe_receives_regen_projected_expected_and_reanchors(self):
        logic = self._logic(scheduler_now=100.0, wall_now=11.5)

        with patch("src.tasks.onetime.TimedCombatLogic.read_expected_skill_bar_sp", return_value=176.0) as read:
            result = logic._sample_sp(force=True)

        self.assertEqual(result, 176.0)
        self.assertEqual(logic.cached_sp, 176.0)
        self.assertEqual(logic.last_observed_sp, 176.0)
        self.assertEqual(logic.expected_sp, 176.0)
        self.assertEqual(logic.last_visual_sp_wall_time, 11.5)
        passed_expected = read.call_args.kwargs.get("expected_sp", read.call_args.args[1])
        self.assertEqual(passed_expected, 172.0)

    def test_failed_visual_probe_keeps_prediction_and_wall_anchor(self):
        logic = self._logic(scheduler_now=100.0, wall_now=11.5)

        with patch("src.tasks.onetime.TimedCombatLogic.read_expected_skill_bar_sp", return_value=-1.0):
            result = logic._sample_sp(force=True)

        self.assertEqual(result, -1.0)
        self.assertEqual(logic.cached_sp, 160.0)
        self.assertEqual(logic.expected_sp, 160.0)
        self.assertEqual(logic.last_observed_sp, 235.0)
        self.assertEqual(logic.last_visual_sp_wall_time, 10.0)

    def test_scheduler_clock_does_not_drive_natural_regen(self):
        logic = self._logic(scheduler_now=9999.0, wall_now=11.0)

        self.assertEqual(logic._project_probe_expected(), 168.0)


if __name__ == "__main__":
    unittest.main()
