"""Regression tests for enemy-presence live overlay clearing."""

import unittest

import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES, probe_enemy_presence_fast

HP_BGR = np.array([102, 68, 255], dtype=np.uint8)


class _OverlayHarness:
    def __init__(self, frame):
        self.frame = frame
        self.config = {KEY_SAVE_ENEMY_PRESENCE_FRAMES: False}
        self.draw_calls = []
        self.clear_box_calls = 0

    def _is_debug_overlay_enabled(self):
        return True

    def draw_boxes(self, feature_name=None, boxes=None, color="red", debug=True):
        self.draw_calls.append((feature_name, boxes or [], color, debug))

    def clear_box(self):
        self.clear_box_calls += 1


class TestEnemyHealthProbeOverlayClear(unittest.TestCase):
    def test_consecutive_misses_clear_global_overlay_only_once_after_hit(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[250:255, 800:920] = HP_BGR
        task = _OverlayHarness(frame)

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertTrue(getattr(task, "_enemy_presence_live_hit_drawn", False))

        task.frame[:] = 0
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)
        self.assertEqual(task.clear_box_calls, 1)
        self.assertFalse(getattr(task, "_enemy_presence_live_hit_drawn", False))

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)
        self.assertEqual(task.clear_box_calls, 1)


if __name__ == "__main__":
    unittest.main()
