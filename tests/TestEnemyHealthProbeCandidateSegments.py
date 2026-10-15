"""Regression coverage for multiple HP-colored candidates on one sampled row."""

import unittest

import numpy as np

from src.image.enemy_health_probe import _find_enemy_hp_run

HP_BGR = np.array([102, 68, 255], dtype=np.uint8)
BAR_BGR = np.array([220, 220, 220], dtype=np.uint8)


class TestEnemyHealthProbeCandidateSegments(unittest.TestCase):
    def test_connected_vertical_noise_does_not_hide_valid_bar(self):
        for scale in (1, 2):
            with self.subTest(scale=scale):
                roi = np.zeros((120 * scale, 400 * scale, 3), dtype=np.uint8)
                roi[21 * scale : 26 * scale, 100 * scale : 150 * scale] = HP_BGR
                # The first erosion/discovery column intersects a tall VFX strip.
                roi[0 : 100 * scale, 104 * scale : 105 * scale] = HP_BGR

                self.assertEqual(
                    _find_enemy_hp_run(roi, 1920 * scale, 1080 * scale),
                    (100 * scale, 21 * scale, 50 * scale, 5 * scale),
                )

    def test_connected_noise_can_require_fallback_after_context_rejection(self):
        roi = np.zeros((120, 400, 3), dtype=np.uint8)
        roi[21:26, 100:150] = HP_BGR
        # The first column still meets 2x height but wrongly needs UI context;
        # an unaffected column meets the direct 10x boundary.
        roi[12:32, 104] = HP_BGR

        self.assertEqual(_find_enemy_hp_run(roi, 1920, 1080), (100, 21, 50, 5))

    def test_last_representative_column_survives_noise_at_first_and_middle(self):
        roi = np.zeros((120, 400, 3), dtype=np.uint8)
        roi[21:26, 100:150] = HP_BGR
        roi[0:100, 104] = HP_BGR
        roi[0:100, 125] = HP_BGR

        self.assertEqual(_find_enemy_hp_run(roi, 1920, 1080), (100, 21, 50, 5))

    def test_repeated_context_rejections_still_consume_geometry_row_budget(self):
        roi = np.zeros((180, 400, 3), dtype=np.uint8)
        # This box qualifies for context checks in twenty sampled rows. Reusing
        # its failed context result must still stop at the sixteen-row quota.
        roi[0:60, 20:170] = HP_BGR
        roi[75:80, 200:250] = HP_BGR

        self.assertIsNone(_find_enemy_hp_run(roi, 1920, 1080))

    def test_thin_noise_still_respects_raw_discovery_row_budget(self):
        roi = np.zeros((250, 400, 3), dtype=np.uint8)
        for y in range(0, 192, 3):
            roi[y, 20:28] = HP_BGR
        roi[201:206, 100:150] = HP_BGR

        self.assertIsNone(_find_enemy_hp_run(roi, 1920, 1080))

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
