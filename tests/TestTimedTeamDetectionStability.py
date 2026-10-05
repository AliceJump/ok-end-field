import unittest
from types import SimpleNamespace

from src.patches.timed_team_detection_patch import _TimedTeamStability, _observe_once


class _SlowTeamTask:
    def __init__(self, team):
        self.team = list(team)
        self.frame = object()
        self.now = 0.0
        self.calls = 0

    def detect_team(self, _frame=None):
        self.calls += 1
        # Reproduce the software/debug path where the first complete template
        # scan can take longer than TimedCombatLogic's old 0.2/0.3s deadline.
        self.now += 0.6
        return list(self.team)

    def active_time(self):
        return self.now

    def next_frame(self):
        return self.frame


class TimedTeamDetectionStabilityTest(unittest.TestCase):
    def test_slow_correct_observations_confirm_across_ticks(self):
        expected = ["庄方宜", "佩丽卡", "诀", "梨诺"]
        task = _SlowTeamTask(expected)
        logic = SimpleNamespace(task=task)

        def fallback(**_kwargs):
            self.fail("timed incremental detection must not fall back to blocking detect_team_stable")

        first, first_stable = _observe_once(logic, "initial", fallback, deadline=0.3)
        second, second_stable = _observe_once(logic, "initial", fallback, deadline=0.3)

        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertFalse(first_stable)
        self.assertTrue(second_stable)
        self.assertEqual(task.calls, 2)
        self.assertGreater(task.now, 0.3)

    def test_missing_frame_breaks_confirmation_streak(self):
        expected = ["庄方宜", "佩丽卡", "诀", "梨诺"]
        task = _SlowTeamTask(expected)
        logic = SimpleNamespace(task=task)

        def fallback(**_kwargs):
            self.fail("timed incremental detection must not fall back to blocking detect_team_stable")

        first, first_stable = _observe_once(logic, "initial", fallback, deadline=0.3)
        self.assertEqual(first, expected)
        self.assertFalse(first_stable)

        task.frame = None
        missing, missing_stable = _observe_once(logic, "initial", fallback, deadline=0.3)
        self.assertEqual(missing, ["?"])
        self.assertFalse(missing_stable)

        task.frame = object()
        third, third_stable = _observe_once(logic, "initial", fallback, deadline=0.3)
        self.assertEqual(third, expected)
        self.assertFalse(third_stable)

    def test_initial_and_refresh_streaks_are_independent(self):
        tracker = _TimedTeamStability()
        team = ["A", "B", "C", "D"]

        self.assertFalse(tracker.observe("initial", team, 0.0)[1])
        self.assertFalse(tracker.observe("refresh", team, 0.1)[1])
        self.assertTrue(tracker.observe("initial", team, 1.0)[1])
        self.assertTrue(tracker.observe("refresh", team, 1.1)[1])

    def test_changed_or_expired_candidate_restarts_confirmation(self):
        tracker = _TimedTeamStability()
        first = ["A", "B", "C", "D"]
        changed = ["A", "B", "?", "D"]

        self.assertFalse(tracker.observe("initial", first, 0.0)[1])
        self.assertFalse(tracker.observe("initial", changed, 1.0)[1])
        self.assertTrue(tracker.observe("initial", changed, 2.0)[1])
        self.assertFalse(tracker.observe("initial", changed, 5.0)[1])

    def test_all_unknown_does_not_build_a_streak(self):
        tracker = _TimedTeamStability()
        unknown = ["?", "?", "?", "?"]

        self.assertFalse(tracker.observe("initial", unknown, 0.0)[1])
        self.assertFalse(tracker.observe("initial", unknown, 1.0)[1])


if __name__ == "__main__":
    unittest.main()
