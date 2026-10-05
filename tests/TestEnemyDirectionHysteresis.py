import unittest
from unittest.mock import patch

import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_probe import (
    EnemyDirectionMarker,
    EnemyDirectionObservation,
    _DIRECTION_BINS,
    _DIRECTION_BIN_DEG,
    _ellipse_sample,
    _marker_candidates,
    probe_enemy_direction_fast,
)
from src.patches.enemy_direction_recovery_patch import recover_enemy_direction_if_needed


class _DirectionTask:
    def __init__(self):
        self.now = 1.0
        self.height = 1080
        self.frame = np.zeros((8, 8, 3), dtype=np.uint8)
        self._enemy_hp_last_slice = None
        self.moves = []
        self.debug = []
        self.config = {}

    def active_time(self):
        return self.now

    def scale_distance(self, value):
        return value

    def active_and_send_mouse_delta(self, **kwargs):
        self.moves.append(kwargs)
        return True

    def log_debug(self, message):
        self.debug.append(message)


def _marker(angle_deg: float, score: float) -> EnemyDirectionMarker:
    return EnemyDirectionMarker(
        angle_deg=angle_deg,
        parameter_angle_deg=angle_deg % 360.0,
        score=score,
    )


def _observation(*markers: EnemyDirectionMarker) -> EnemyDirectionObservation:
    best = max(markers, key=lambda marker: marker.score)
    return EnemyDirectionObservation(
        angle_deg=best.angle_deg,
        score=best.score,
        markers=tuple(markers),
    )


def _profile_with_runs(*runs: tuple[float, float, float]) -> np.ndarray:
    profile = np.zeros(_DIRECTION_BINS, dtype=np.float32)
    for start_deg, span_deg, score in runs:
        start = int(round(start_deg / _DIRECTION_BIN_DEG)) % _DIRECTION_BINS
        length = int(round(span_deg / _DIRECTION_BIN_DEG))
        indices = (start + np.arange(length, dtype=np.int32)) % _DIRECTION_BINS
        profile[indices] = np.maximum(profile[indices], score)
    return profile


class TestEnemyDirectionMarkerSplitting(unittest.TestCase):
    def test_short_clipped_run_does_not_make_normal_run_split(self):
        profile = _profile_with_runs((20.0, 5.0, 40.0), (80.0, 8.0, 40.0))
        markers = _marker_candidates(profile, _ellipse_sample(1920, 1080))
        self.assertEqual(len(markers), 2)

    def test_two_overlapping_pairs_each_split(self):
        profile = _profile_with_runs((20.0, 12.0, 40.0), (100.0, 12.0, 40.0))
        markers = _marker_candidates(profile, _ellipse_sample(1920, 1080))
        self.assertEqual(len(markers), 4)
        for marker in markers:
            self.assertAlmostEqual(marker.arc_width_deg, 8.0)

    def test_probe_primary_angle_matches_highest_score_marker(self):
        markers = (_marker(20.0, 24.0), _marker(40.0, 31.0), _marker(80.0, 27.0))
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        with patch("src.image.enemy_direction_probe._marker_candidates", return_value=markers):
            observation = probe_enemy_direction_fast(frame)
        self.assertIsNotNone(observation)
        best = max(observation.markers, key=lambda marker: marker.score)
        self.assertEqual(observation.angle_deg, best.angle_deg)
        self.assertEqual(observation.score, best.score)


class TestEnemyDirectionTargetLock(unittest.TestCase):
    def test_small_score_flip_keeps_previous_nearby_marker(self):
        task = _DirectionTask()
        first = _observation(_marker(30.0, 30.0), _marker(34.0, 29.0))
        second = _observation(_marker(30.0, 30.0), _marker(34.0, 31.0))

        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            side_effect=(first, second),
        ):
            self.assertTrue(recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN))
            task.now += 0.05
            self.assertTrue(recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN))

        self.assertAlmostEqual(task._enemy_direction_last_angle, 30.0)
        self.assertEqual(task._enemy_direction_streak, 2)
        self.assertEqual(len(task.moves), 1)

    def test_clearly_stronger_nearby_marker_can_take_over(self):
        task = _DirectionTask()
        first = _observation(_marker(30.0, 30.0), _marker(34.0, 29.0))
        second = _observation(_marker(30.0, 30.0), _marker(34.0, 40.0))

        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            side_effect=(first, second),
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)
            task.now += 0.05
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)

        self.assertAlmostEqual(task._enemy_direction_last_angle, 34.0)
        self.assertEqual(task._enemy_direction_streak, 2)
        self.assertEqual(len(task.moves), 1)

    def test_disappeared_locked_direction_switches_and_restarts_stability(self):
        task = _DirectionTask()
        first = _observation(_marker(30.0, 30.0), _marker(34.0, 29.0))
        second = _observation(_marker(80.0, 35.0))

        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            side_effect=(first, second),
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)
            task.now += 0.05
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)

        self.assertAlmostEqual(task._enemy_direction_last_angle, 80.0)
        self.assertEqual(task._enemy_direction_streak, 1)
        self.assertEqual(task.moves, [])


if __name__ == "__main__":
    unittest.main()
