import unittest

import cv2
import numpy as np

from src.image.target_lock_pointer_probe import probe_target_lock_pointer

_POINTER_CENTER_Y_RATIO = 381.0 / 864.0


def _pointer_frame(
    angle_deg: float,
    width: int = 1920,
    height: int = 1080,
    *,
    pointer: bool = True,
    white_distractor: bool = False,
):
    frame = np.full((height, width, 3), 60, dtype=np.uint8)
    scale = height / 1080.0
    center = np.array([width / 2.0, height * _POINTER_CENTER_Y_RATIO], dtype=np.float32)
    center_point = tuple(np.rint(center).astype(int))

    if pointer:
        radians = np.deg2rad(angle_deg)
        direction = np.array([np.cos(radians), np.sin(radians)], dtype=np.float32)
        tangent = np.array([-direction[1], direction[0]], dtype=np.float32)
        tip = center + direction * (40.0 * scale)
        base_center = center + direction * (17.0 * scale)
        triangle = np.rint(
            np.vstack(
                (
                    tip,
                    base_center + tangent * (11.0 * scale),
                    base_center - tangent * (11.0 * scale),
                )
            )
        ).astype(np.int32)
        cv2.fillConvexPoly(frame, triangle, (235, 235, 235), cv2.LINE_AA)

    cv2.circle(frame, center_point, max(1, int(round(20.0 * scale))), (12, 12, 12), -1, cv2.LINE_AA)
    if pointer:
        cv2.circle(frame, center_point, max(1, int(round(4.0 * scale))), (245, 245, 245), -1, cv2.LINE_AA)

    if white_distractor:
        distractor_angle = np.deg2rad(angle_deg + 90.0)
        direction = np.array([np.cos(distractor_angle), np.sin(distractor_angle)], dtype=np.float32)
        start = tuple(np.rint(center - direction * (48.0 * scale)).astype(int))
        end = tuple(np.rint(center + direction * (48.0 * scale)).astype(int))
        cv2.line(frame, start, end, (250, 250, 250), max(2, int(round(5.0 * scale))), cv2.LINE_AA)
        if pointer:
            cv2.circle(frame, center_point, max(1, int(round(4.0 * scale))), (245, 245, 245), -1, cv2.LINE_AA)

    return frame


class TestTargetLockPointerProbe(unittest.TestCase):
    def test_detects_eight_pointer_directions(self):
        for expected in (0.0, 45.0, 90.0, 135.0, 180.0, -135.0, -90.0, -45.0):
            with self.subTest(expected=expected):
                observation = probe_target_lock_pointer(_pointer_frame(expected))
                self.assertIsNotNone(observation)
                delta = abs(((observation.angle_deg - expected + 180.0) % 360.0) - 180.0)
                self.assertLessEqual(delta, 2.0)
                self.assertGreater(observation.score, 60.0)
                self.assertGreater(observation.center_contrast, 35.0)

    def test_scales_to_4k(self):
        observation = probe_target_lock_pointer(_pointer_frame(-58.0, 3840, 2160))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -58.0, delta=2.0)

    def test_white_outline_crossing_pointer_does_not_steal_direction(self):
        observation = probe_target_lock_pointer(_pointer_frame(-50.0, white_distractor=True))
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation.angle_deg, -50.0, delta=3.0)

    def test_missing_pointer_is_rejected(self):
        self.assertIsNone(probe_target_lock_pointer(_pointer_frame(0.0, pointer=False)))

    def test_center_dot_without_direction_wedge_is_rejected(self):
        frame = _pointer_frame(0.0)
        center_x = frame.shape[1] // 2
        center_y = int(round(frame.shape[0] * _POINTER_CENTER_Y_RATIO))
        cv2.circle(frame, (center_x, center_y), 42, (60, 60, 60), -1, cv2.LINE_AA)
        cv2.circle(frame, (center_x, center_y), 20, (12, 12, 12), -1, cv2.LINE_AA)
        cv2.circle(frame, (center_x, center_y), 4, (245, 245, 245), -1, cv2.LINE_AA)
        self.assertIsNone(probe_target_lock_pointer(frame))

    def test_invalid_frames_fail_soft(self):
        self.assertIsNone(probe_target_lock_pointer(None))
        self.assertIsNone(probe_target_lock_pointer(np.empty((0, 0, 3), dtype=np.uint8)))
        self.assertIsNone(probe_target_lock_pointer(np.zeros((10, 10), dtype=np.uint8)))
