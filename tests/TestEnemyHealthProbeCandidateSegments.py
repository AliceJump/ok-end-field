"""Regression coverage for multiple HP-colored candidates on one sampled row."""

import unittest

import numpy as np

from src.image.enemy_health_probe import _find_enemy_hp_run

HP_BGR = np.array([102, 68, 255], dtype=np.uint8)
BAR_BGR = np.array([220, 220, 220], dtype=np.uint8)


class TestEnemyHealthProbeCandidateSegments(unittest.TestCase):
    def test_checks_later_candidate_on_same_sampled_row(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)

        # Left candidate looks HP-colored but has no supporting bar context.
        roi[21:26, 20:50] = HP_BGR

        # A real low-HP candidate starts later on the same sampled rows and has
        # the expected horizontal HP/stagger-bar structure underneath it.
        roi[21:26, 200:221] = HP_BGR
        roi[30:33, 170:280] = BAR_BGR

        self.assertEqual(_find_enemy_hp_run(roi, 1920, 1080), (200, 21, 21, 5))


if __name__ == "__main__":
    unittest.main()
