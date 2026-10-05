import json
import queue
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_diagnostics import _queue_direction_artifact
from src.image.enemy_direction_probe import (
    EnemyDirectionMarker,
    EnemyDirectionObservation,
    _direction_to_parameter_deg,
    _ellipse_axes,
    probe_enemy_direction_fast,
)
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.patches.enemy_direction_recovery_patch import (
    _recover_direction_fail_soft,
    recover_enemy_direction_if_needed,
)


def _marker_frame_many(angles_deg, width: int = 1920, height: int = 1080, *, ellipse_scale: float = 1.0):
    frame = np.full((height, width, 3), 60, dtype=np.uint8)
    semi_axis_x, semi_axis_y = _ellipse_axes(width, height)
    axes = (
        int(round(semi_axis_x * ellipse_scale)),
        int(round(semi_axis_y * ellipse_scale)),
    )
    thickness = max(8, int(round(16 * height / 1080.0)))
    for angle_deg in angles_deg:
        parameter = _direction_to_parameter_deg(angle_deg, semi_axis_x, semi_axis_y)
        cv2.ellipse(
            frame,
            (width // 2, height // 2),
            axes,
            0,
            parameter - 4,
            parameter + 4,
            (45, 45, 230),
            thickness,
            cv2.LINE_AA,
        )
    return frame


def _marker_frame(angle_deg: float, width: int = 1920, height: int = 1080):
    return _marker_frame_many((angle_deg,), width, height)


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
    def test_detects_fixed_ellipse_marker(self):
        observation = probe_enemy_direction_fast(_marker_frame(170))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, 170, delta=3)
        self.assertGreater(observation.score, 18)
        self.assertEqual(len(observation.markers), 1)

    def test_scales_to_4k(self):
        observation = probe_enemy_direction_fast(_marker_frame(-160, 3840, 2160))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -160, delta=3)

    def test_red_arc_outside_ellipse_annulus_is_rejected(self):
        observation = probe_enemy_direction_fast(
            _marker_frame_many((170,), ellipse_scale=0.90)
        )
        self.assertIsNone(observation)

    def test_overlapping_long_run_splits_into_two_fixed_markers(self):
        observation = probe_enemy_direction_fast(_marker_frame_many((30, 34, 80)))
        self.assertIsNotNone(observation)
        self.assertEqual(len(observation.markers), 3)
        angles = sorted(marker.angle_deg for marker in observation.markers)
        for actual, expected in zip(angles, (30, 34, 80), strict=True):
            self.assertAlmostEqual(actual, expected, delta=2)
        for marker in observation.markers:
            self.assertAlmostEqual(marker.arc_width_deg, 8.0)
        best_marker = max(observation.markers, key=lambda marker: marker.score)
        self.assertEqual(observation.angle_deg, best_marker.angle_deg)

    def test_target_lock_ignores_small_score_flip(self):
        task = _DirectionTask()
        first_markers = (
            EnemyDirectionMarker(30.0, 30.0, 30.0),
            EnemyDirectionMarker(34.0, 34.0, 29.0),
        )
        second_markers = (
            EnemyDirectionMarker(30.0, 30.0, 30.0),
            EnemyDirectionMarker(34.0, 34.0, 31.0),
        )
        observations = (
            EnemyDirectionObservation(30.0, 30.0, first_markers),
            EnemyDirectionObservation(34.0, 31.0, second_markers),
        )
        with patch(
            "src.patches.enemy_direction_recovery_patch.probe_enemy_direction_fast",
            side_effect=observations,
        ):
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)
            task.now += 0.05
            recover_enemy_direction_if_needed(task, EnemyPresence.UNKNOWN)

        self.assertAlmostEqual(task._enemy_direction_last_angle, 30.0)
        self.assertEqual(task._enemy_direction_streak, 2)
        self.assertEqual(len(task.moves), 1)

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

    def test_live_overlay_contains_ellipse_scan_and_hit(self):
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
        self.assertEqual(len(boxes), 2)
        names = [box.name for box in boxes]
        self.assertTrue(any("enemy_direction_scan:ellipse_annulus" in name for name in names))
        self.assertTrue(any("enemy_direction_hit:1:25.0deg" in name for name in names))

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
            raw_images = list(output_dir.glob("enemy_direction_*.raw.png"))
            images = [
                path
                for path in output_dir.glob("enemy_direction_*.png")
                if not path.name.endswith(".raw.png")
            ]
            informs = list(output_dir.glob("enemy_direction_*.inform.json"))
            self.assertEqual(len(raw_images), 1)
            self.assertEqual(len(images), 1)
            self.assertEqual(len(informs), 1)

            payload = json.loads(informs[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "enemy_direction_probe/v2")
            self.assertTrue(payload["detected"])
            self.assertEqual(payload["result"], "hit")
            self.assertEqual(payload["action"], "tracking")
            self.assertEqual(payload["streak"], 1)
            self.assertAlmostEqual(payload["observation"]["angle_deg"], 30.0)
            self.assertAlmostEqual(payload["observation"]["score"], 24.5)
            self.assertEqual(len(payload["observation"]["markers"]), 1)
            self.assertAlmostEqual(payload["scan_annulus"]["inner_scale"], 0.98)
            self.assertAlmostEqual(payload["scan_annulus"]["outer_scale"], 1.02)
            self.assertEqual(payload["image"], images[0].name)
            self.assertEqual(payload["annotated_image"], images[0].name)
            self.assertEqual(payload["raw_image"], raw_images[0].name)

            raw = cv2.imread(str(raw_images[0]))
            self.assertIsNotNone(raw)
            self.assertTrue(np.array_equal(raw, task.frame))

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
            self.assertEqual(payload["schema"], "enemy_direction_probe/v2")
            self.assertFalse(payload["detected"])
            self.assertEqual(payload["result"], "miss")
            self.assertEqual(payload["action"], "miss")
            self.assertIsNone(payload["observation"])

    def test_async_queue_owns_frame_snapshot(self):
        task = _DirectionTask()
        task._enemy_direction_artifact_queue = queue.Queue(maxsize=1)
        frame = np.full((8, 8, 3), 7, dtype=np.uint8)
        captured_at = datetime.now().astimezone()
        observation = EnemyDirectionObservation(angle_deg=15.0, score=12.0)

        _queue_direction_artifact(
            task,
            Path("."),
            "sample",
            frame,
            observation,
            EnemyPresence.UNKNOWN,
            1,
            "tracking",
            None,
            None,
            1,
            captured_at,
        )

        item = task._enemy_direction_artifact_queue.get_nowait()
        queued_frame = item[3]
        self.assertIsNot(queued_frame, frame)
        self.assertTrue(np.array_equal(queued_frame, frame))
        frame[:] = 99
        self.assertTrue(np.all(queued_frame == 7))

    def test_full_async_queue_drops_before_copy(self):
        class _CopyMustNotRun:
            def copy(self):
                raise AssertionError("full queue must be rejected before frame.copy()")

        task = _DirectionTask()
        task._enemy_direction_artifact_queue = queue.Queue(maxsize=1)
        task._enemy_direction_artifact_queue.put_nowait(object())
        captured_at = datetime.now().astimezone()

        _queue_direction_artifact(
            task,
            Path("."),
            "sample",
            _CopyMustNotRun(),
            None,
            EnemyPresence.UNKNOWN,
            0,
            "miss",
            None,
            None,
            1,
            captured_at,
        )

        self.assertEqual(task._enemy_direction_artifact_queue.qsize(), 1)
        self.assertTrue(task.debug)
        self.assertIn("队列已满", task.debug[0])

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
