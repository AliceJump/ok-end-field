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
BAR_BGR = np.array([220, 220, 220], dtype=np.uint8)


class _ProbeHarness:
    def __init__(self, frame):
        self.frame = frame


def _frame(width=1920, height=1080):
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestEnemyHealthProbe(unittest.TestCase):
    def test_accepts_minimum_residual_hp_bar_at_1080p_with_context(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:110] = HP_BGR
        roi[29:32, 80:170] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_rejects_too_short_residual_hp_bar_at_1080p(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:109] = HP_BGR
        roi[29:32, 80:170] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_long_hp_run_skips_secondary_context_at_1080p(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:140] = HP_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_short_hp_colored_vfx_without_context_is_rejected(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:130] = HP_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_short_hp_run_with_horizontal_ui_context_is_accepted(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:121] = HP_BGR
        roi[29:32, 70:180] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_rejects_thin_pink_particle(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:23, 100:120] = HP_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_horizontal_and_vertical_evidence_must_be_same_pixel_run(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[9, 100:110] = HP_BGR
        roi[12:16, 105] = HP_BGR
        roi[20:23, 80:170] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_scales_geometry_and_context_to_4k(self):
        roi = np.zeros((200, 800, 3), dtype=np.uint8)
        roi[40:50, 200:220] = HP_BGR
        roi[58:64, 160:300] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 3840, 2160))

    def test_rejects_4k_run_below_scaled_threshold(self):
        roi = np.zeros((200, 800, 3), dtype=np.uint8)
        roi[40:50, 200:219] = HP_BGR
        roi[58:64, 160:300] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 3840, 2160))

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
