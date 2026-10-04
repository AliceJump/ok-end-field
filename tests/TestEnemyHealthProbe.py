"""Enemy HP-bar presence probe tests."""

import unittest

import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_health_probe import (
    ENEMY_ABSENT_CONFIRM_ROUNDS,
    ENEMY_HP_BGR_LOWER,
    ENEMY_NORMAL_HP_SLICES,
    _has_enemy_hp_run,
    probe_enemy_presence_fast,
)

HP_BGR = np.array([102, 68, 255], dtype=np.uint8)


class _ProbeHarness:
    def __init__(self, frame):
        self.frame = frame


def _frame(width=1920, height=1080):
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestEnemyHealthProbe(unittest.TestCase):
    def test_accepts_small_residual_hp_bar_at_1080p(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:104] = HP_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_rejects_thin_pink_particle(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:23, 100:107] = HP_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_scales_geometry_to_4k(self):
        roi = np.zeros((200, 800, 3), dtype=np.uint8)
        roi[40:50, 200:208] = HP_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 3840, 2160))

    def test_normal_enemy_returns_present_and_reuses_slice(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame)

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        cached = getattr(task, "_enemy_hp_last_slice", None)
        self.assertIsInstance(cached, int)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertEqual(getattr(task, "_enemy_hp_last_slice", None), cached)

    def test_boss_region_returns_present(self):
        frame = _frame()
        frame[59:72, 722:1090] = HP_BGR
        task = _ProbeHarness(frame)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertIsNone(getattr(task, "_enemy_hp_last_slice", None))

    def test_absent_requires_two_complete_normal_rounds(self):
        task = _ProbeHarness(_frame())
        checks = ENEMY_NORMAL_HP_SLICES * ENEMY_ABSENT_CONFIRM_ROUNDS

        for _ in range(checks - 1):
            self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.ABSENT)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.ABSENT)

    def test_out_of_range_pink_is_ignored(self):
        frame = _frame()
        frame[900:910, 500:700] = ENEMY_HP_BGR_LOWER
        task = _ProbeHarness(frame)
        checks = ENEMY_NORMAL_HP_SLICES * ENEMY_ABSENT_CONFIRM_ROUNDS
        state = EnemyPresence.UNKNOWN
        for _ in range(checks):
            state = probe_enemy_presence_fast(task)
        self.assertEqual(state, EnemyPresence.ABSENT)


if __name__ == "__main__":
    unittest.main()
