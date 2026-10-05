import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.patches.enemy_direction_recovery_patch import (
    _recover_direction_fail_soft,
    recover_enemy_direction_if_needed,
)


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
        self.config = {KEY_SAVE_ENEMY_PRESENCE_FRAMES: False}
        self.overlay = False
        self.draw_calls = []

    def active_time(self):
        return self.now

    def scale_distance(self, value):
        return value

    def active_and_send_mouse_delta(self, **kwargs):
        self.moves.append(kwargs)
        return True

    def log_debug(self, message):
        self.debug.append(message)

    def _is_debug_overlay_enabled(self):
        return self.overlay

    def draw_boxes(self, name, boxes, **kwargs):
        self.draw_calls.append((name, boxes, kwargs))


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
        self.assertGreater(task.moves[0]["dx"], 0)
        self.assertEqual(task.moves[0]["dy"], 0)
        self.assertEqual(task.moves[0]["steps"], 1)
        self.assertEqual(task.moves[0]["delay"], 0.0)

    def test_screen_right_marker_uses_negative_dx(self):
        task = _DirectionTask()
        observation = EnemyDirectionObservation(angle_deg=0.0, score=30.0)
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            return_value=observation,
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.PRESENT)
            task.now += 0.05
            recover_enemy_direction_if_needed(task, EnemyPresence.PRESENT)
        self.assertEqual(len(task.moves), 1)
        self.assertLess(task.moves[0]["dx"], 0)
        self.assertEqual(task.moves[0]["dy"], 0)

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

    def test_live_overlay_contains_scan_bounds_and_hit_box(self):
        task = _DirectionTask()
        task.overlay = True
        observation = EnemyDirectionObservation(angle_deg=25.0, score=22.0)
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            return_value=observation,
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)

        self.assertEqual(len(task.draw_calls), 1)
        layer, boxes, kwargs = task.draw_calls[0]
        self.assertEqual(layer, "enemy_direction_debug")
        self.assertEqual(kwargs["color"], "blue")
        self.assertEqual(len(boxes), 4)
        names = [box.name for box in boxes]
        self.assertTrue(any("enemy_direction_scan:target" in name for name in names))
        self.assertTrue(any("enemy_direction_hit:25.0deg" in name for name in names))

    def test_save_direction_hit_png_and_inform(self):
        task = _DirectionTask()
        task.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES] = True
        observation = EnemyDirectionObservation(angle_deg=30.0, score=24.5)

        with TemporaryDirectory() as directory:
            task._enemy_direction_debug_folder = directory
            task._enemy_direction_debug_sync_save = True
            with patch(
                "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
                return_value=observation,
            ):
                recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)

            output_dir = Path(directory)
            images = list(output_dir.glob("enemy_direction_*.png"))
            informs = list(output_dir.glob("enemy_direction_*.inform.json"))
            self.assertEqual(len(images), 1)
            self.assertEqual(len(informs), 1)

            payload = json.loads(informs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "enemy_direction_probe/v1")
            self.assertTrue(payload["detected"])
            self.assertEqual(payload["result"], "hit")
            self.assertEqual(payload["action"], "tracking")
            self.assertEqual(payload["streak"], 1)
            self.assertAlmostEqual(payload["observation"]["angle_deg"], 30.0)
            self.assertAlmostEqual(payload["observation"]["score"], 24.5)
            self.assertEqual(len(payload["scan_rings"]), 3)

            annotated = cv2.imread(str(images[0]))
            self.assertIsNotNone(annotated)
            exact_green = np.all(annotated == np.array([0, 255, 0], dtype=np.uint8), axis=2)
            self.assertTrue(np.any(exact_green))

    def test_save_direction_miss_png_and_inform(self):
        task = _DirectionTask()
        task.frame = np.full((1080, 1920, 3), 60, dtype=np.uint8)
        task.config[KEY_SAVE_ENEMY_PRESENCE_FRAMES] = True

        with TemporaryDirectory() as directory:
            task._enemy_direction_debug_folder = directory
            task._enemy_direction_debug_sync_save = True
            with patch(
                "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
                return_value=None,
            ):
                self.assertFalse(recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN))

            informs = list(Path(directory).glob("enemy_direction_*.inform.json"))
            self.assertEqual(len(informs), 1)
            payload = json.loads(informs[0].read_text(encoding="utf-8"))
            self.assertFalse(payload["detected"])
            self.assertEqual(payload["result"], "miss")
            self.assertEqual(payload["action"], "miss")
            self.assertIsNone(payload["observation"])

    def test_recovery_failure_never_changes_presence_even_if_logging_fails(self):
        task = _DirectionTask()
        task.log_debug = lambda _message: (_ for _ in ()).throw(RuntimeError("logger failed"))
        with patch(
            "src.patches.enemy_direction_recovery_patch.recover_enemy_direction_if_needed",
            side_effect=RuntimeError("mouse recovery failed"),
        ):
            result = _recover_direction_fail_soft(task, EnemyPresence.PRESENT)
        self.assertEqual(result, EnemyPresence.PRESENT)


if __name__ == "__main__":
    unittest.main()
