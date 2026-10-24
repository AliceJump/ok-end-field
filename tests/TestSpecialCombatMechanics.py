import unittest

from src.data.special_combat_mechanics import SPECIAL_COMBAT_MECHANIC_DETECTOR_PAIRS
from src.image.special_combat_mechanics_probe import (
    probe_shield_guard_end,
    probe_shield_guard_start,
)
from src.patches.special_combat_mechanics_patch import SpecialCombatMechanicRuntime


class _Task:
    def __init__(self):
        self.now = 0.0
        self.dodges = []
        self.logs = []
        self.warnings = []

    def active_time(self):
        return self.now

    def _dodge_with_direction(self, direction, **kwargs):
        self.dodges.append((direction, kwargs))

    def log_info(self, message):
        self.logs.append(message)

    def log_warning(self, message):
        self.warnings.append(message)


class TestSpecialCombatMechanics(unittest.TestCase):
    def test_default_config_is_start_end_detector_pair(self):
        self.assertEqual(
            SPECIAL_COMBAT_MECHANIC_DETECTOR_PAIRS,
            (("shield_guard", ("shield_guard_start", "shield_guard_end")),),
        )

    def test_placeholder_detectors_are_inert(self):
        task = _Task()
        self.assertFalse(probe_shield_guard_start(task, object()))
        self.assertFalse(probe_shield_guard_end(task, object()))

    def test_shield_guard_lifecycle_and_left_dodge_throttle(self):
        task = _Task()
        probes = {
            "start": lambda _task, frame: frame == "start",
            "end": lambda _task, frame: frame == "end",
        }
        runtime = SpecialCombatMechanicRuntime(
            task,
            detector_pairs=(("shield_guard", ("start", "end")),),
            probes=probes,
            clock=task.active_time,
        )

        self.assertEqual(runtime.observe("start"), "shield_guard")
        self.assertTrue(runtime.perform_active_action())
        self.assertEqual(len(task.dodges), 1)
        direction, kwargs = task.dodges[0]
        self.assertEqual(direction, "a")
        self.assertGreater(kwargs["pre_hold"], 0)
        self.assertGreater(kwargs["dodge_down_time"], 0)
        self.assertGreaterEqual(kwargs["after_sleep"], 0)

        task.now = 0.5
        self.assertEqual(runtime.observe("hold"), "shield_guard")
        self.assertTrue(runtime.perform_active_action())
        self.assertEqual(len(task.dodges), 1)

        task.now = 1.0
        self.assertEqual(runtime.observe("hold"), "shield_guard")
        self.assertTrue(runtime.perform_active_action())
        self.assertEqual(len(task.dodges), 2)

        self.assertIsNone(runtime.observe("end"))
        self.assertIsNone(runtime.active_name)
        self.assertTrue(any("特殊战斗机制开始" in message for message in task.logs))
        self.assertTrue(any("特殊战斗机制结束" in message for message in task.logs))

    def test_end_detector_failure_fails_open(self):
        task = _Task()

        def broken_end(_task, _frame):
            raise RuntimeError("boom")

        runtime = SpecialCombatMechanicRuntime(
            task,
            detector_pairs=(("shield_guard", ("start", "end")),),
            probes={
                "start": lambda _task, frame: frame == "start",
                "end": broken_end,
            },
            clock=task.active_time,
        )

        self.assertEqual(runtime.observe("start"), "shield_guard")
        self.assertIsNone(runtime.observe("hold"))
        self.assertIsNone(runtime.active_name)
        self.assertTrue(any("恢复普通战斗" in message for message in task.warnings))


if __name__ == "__main__":
    unittest.main()
