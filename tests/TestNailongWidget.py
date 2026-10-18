"""Focused, display-independent Nailong eye-movement and live-task tests."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from src.gui.NailongWidget import EYES, MAX_PUPIL_TRAVEL, eye_offset, task_is_running


class TestNailongWidget(unittest.TestCase):
    def test_stationary_cursor_returns_centered_pupil(self):
        """Avoid drift when the pointer lies at the center of an iris."""
        for eye in EYES:
            self.assertEqual(eye_offset(eye, eye), (0.0, 0.0))

    def test_off_window_cursor_stays_inside_iris(self):
        """Keep pupil centers bounded even for very distant pointers."""
        for eye in EYES:
            for pointer in ((-10000, -10000), (15000, 15000), (-9999, 5000)):
                dx, dy = eye_offset(eye, pointer)
                self.assertLessEqual(math.hypot(dx, dy), MAX_PUPIL_TRAVEL + 1e-8)

    def test_working_avatar_reflects_real_executor_state(self):
        """Never pretend to run a task when the executor is idle or paused."""
        self.assertFalse(task_is_running(None))
        self.assertFalse(task_is_running(SimpleNamespace(current_task=None, paused=False)))
        self.assertFalse(task_is_running(SimpleNamespace(current_task=object(), paused=True)))
        self.assertTrue(task_is_running(SimpleNamespace(current_task=object(), paused=False)))


if __name__ == "__main__":
    unittest.main()
