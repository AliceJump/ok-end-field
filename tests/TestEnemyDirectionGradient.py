import unittest

import cv2
import numpy as np

from src.image.enemy_direction_probe import (
    _direction_to_parameter_deg,
    _ellipse_axes,
    probe_enemy_direction_fast,
)


def _blank_frame(width: int = 1920, height: int = 1080) -> np.ndarray:
    return np.full((height, width, 3), 60, dtype=np.uint8)


def _draw_direction_arc(
    frame: np.ndarray,
    angle_deg: float,
    scale: float,
    color: tuple[int, int, int],
    *,
    thickness: int,
) -> None:
    height, width = frame.shape[:2]
    semi_axis_x, semi_axis_y = _ellipse_axes(width, height)
    parameter = _direction_to_parameter_deg(angle_deg, semi_axis_x, semi_axis_y)
    axes = (
        int(round(semi_axis_x * scale)),
        int(round(semi_axis_y * scale)),
    )
    cv2.ellipse(
        frame,
        (width // 2, height // 2),
        axes,
        0,
        parameter - 4,
        parameter + 4,
        color,
        max(1, int(round(thickness * height / 1080.0))),
        cv2.LINE_AA,
    )


def _draw_marker_with_outward_gradient(frame: np.ndarray, angle_deg: float) -> None:
    _draw_direction_arc(frame, angle_deg, 1.0, (45, 45, 230), thickness=16)
    for scale, red in zip(
        (1.03, 1.05, 1.07, 1.09, 1.11),
        (190, 165, 140, 115, 90),
        strict=True,
    ):
        _draw_direction_arc(frame, angle_deg, scale, (55, 55, red), thickness=12)


class TestEnemyDirectionGradient(unittest.TestCase):
    def test_outward_gradient_beats_stronger_ring_only_effect(self):
        frame = _blank_frame()
        _draw_direction_arc(frame, 180.0, 1.0, (35, 35, 255), thickness=16)
        _draw_marker_with_outward_gradient(frame, 45.0)

        observation = probe_enemy_direction_fast(frame)

        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, 45.0, delta=3.0)
        self.assertEqual(len(observation.markers), 1)
        self.assertAlmostEqual(observation.markers[0].angle_deg, 45.0, delta=3.0)

    def test_symmetric_radial_effect_is_rejected_when_marker_gradient_exists(self):
        frame = _blank_frame()
        _draw_marker_with_outward_gradient(frame, -45.0)
        for scale in (0.92, 0.94, 0.96, 1.0, 1.03, 1.05, 1.07, 1.09, 1.11):
            _draw_direction_arc(
                frame,
                135.0,
                scale,
                (35, 35, 245),
                thickness=16 if scale == 1.0 else 12,
            )

        observation = probe_enemy_direction_fast(frame)

        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -45.0, delta=3.0)
        self.assertEqual(len(observation.markers), 1)
        self.assertAlmostEqual(observation.markers[0].angle_deg, -45.0, delta=3.0)

    def test_ring_only_candidate_remains_recall_fallback(self):
        frame = _blank_frame()
        _draw_direction_arc(frame, -135.0, 1.0, (35, 35, 255), thickness=16)

        observation = probe_enemy_direction_fast(frame)

        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -135.0, delta=3.0)


if __name__ == "__main__":
    unittest.main()
