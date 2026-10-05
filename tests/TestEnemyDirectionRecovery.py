import unittest
from unittest.mock import patch

import cv2
import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast
from src.patches.enemy_direction_recovery_patch import recover_enemy_direction_if_needed


def _marker_frame(angle_deg: float, width: int = 1920, height: int = 1080):
    frame = np.full((height, width, 3), 60, dtype=np.uint8)
    radius = int(round(352 * height / 1080.0))
    thickness = max(8, int(round(16 * height / 1080.0)))
    cv2.ellipse(
        frame,
        (width // 2, height // 2),
        (radius, radius),
        0,
        angle_deg - 5,
        angle_deg + 5,
        (45, 45, 230),
        thickness,
        cv2.LINE_AA,
    )
    return frame


def _broad_red_sector(width: int = 1920, height: int = 1080):
    frame = np.full((height, width, 3), 60, dtype=np.uint8)
    yy, xx = np.indices((height, width))
    cx = width / 2.0
    cy = height / 2.0
    radius = np.hypot(xx - cx, yy - cy)
    angle = (np.degrees(np.arctan2(yy - cy, xx - cx)) + 360.0) % 360.0
    angle_delta = np.abs((angle - 170.0 + 180.0) % 360.0 - 180.0)
    mask = (radius >= 275 * height / 1080.0) & (radius <= 430 * height / 1080.0) & (angle_delta <= 8)
    frame[mask] = (45, 45, 230)
    return frame


class _DirectionTask:
    def __init__(self):
        self.now = 1.0
        self.height = 1080
        self.frame = _marker_frame(180)
        self._enemy_hp_last_slice = None
        self.moves = []
        self.debug = []

    def active_time(self):
        return self.now

    def scale_distance(self, value):
        return value

    def active_and_send_mouse_delta(self, **kwargs):
        self.moves.append(kwargs)
        return True

    def log_debug(self, message):
        self.debug.append(message)


class TestEnemyDirectionProbe(unittest.TestCase):
    def test_detects_fixed_ring_marker(self):
        observation = probe_enemy_direction_fast(_marker_frame(170))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, 170, delta=4)
        self.assertGreater(observation.score, 10)

    def test_scales_to_4k(self):
        observation = probe_enemy_direction_fast(_marker_frame(-160, 3840, 2160))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -160, delta=4)

    def test_broad_red_sector_is_rejected_by_neighbor_ring_contrast(self):
        self.assertIsNone(probe_enemy_direction_fast(_broad_red_sector()))

    def test_normal_enemy_hp_hit_skips_direction_probe(self):
        task = _DirectionTask()
        task._enemy_hp_last_slice = 2
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            side_effect=AssertionError("direction detector should stay idle"),
        ):
            self.assertFalse(recover_enemy_direction_if_needed(task, EnemyPresence.PRESENT))
        self.assertEqual(task.moves, [])

    def test_boss_present_still_recovers_after_two_stable_frames(self):
        task = _DirectionTask()
        observation = EnemyDirectionObservation(angle_deg=180.0, score=30.0)
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            return_value=observation,
        ):
            self.assertTrue(recover_enemy_direction_if_needed(task, EnemyPresence.PRESENT))
            self.assertEqual(task.moves, [])
            task.now += 0.05
            self.assertTrue(recover_enemy_direction_if_needed(task, EnemyPresence.PRESENT))

        self.assertEqual(len(task.moves), 1)
        self.assertLess(task.moves[0]["dx"], 0)
        self.assertEqual(task.moves[0]["dy"], 0)
        self.assertEqual(task.moves[0]["steps"], 1)
        self.assertEqual(task.moves[0]["delay"], 0.0)

    def test_unknown_presence_can_start_recovery(self):
        task = _DirectionTask()
        observation = EnemyDirectionObservation(angle_deg=-90.0, score=20.0)
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            return_value=observation,
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)
            task.now += 0.05
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)
        self.assertEqual(len(task.moves), 1)
        self.assertLess(task.moves[0]["dy"], 0)


if __name__ == "__main__":
    unittest.main()
