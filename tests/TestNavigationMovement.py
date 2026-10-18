"""Coordinate semantics, accepted input and fresh detection remain independent."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.interaction.target_movement import (
    calc_direction_step,
    ellipse_geometry,
    hud_direction_step,
    resolve_move_profile,
)
from src.tasks.mixin.navigation_detection_scope import NavigationDetectionScope
from tests.TestNavigationMixin import NavigationMixin


class TestNavigationMovement(unittest.TestCase):
    def task(self, detections):
        now = [0.0]

        def target(x, y):
            return SimpleNamespace(x=x, y=y, width=0, height=0)

        task = SimpleNamespace(
            width=1920,
            height=1080,
            screen_center=lambda: (960, 540),
            scale_distance=lambda value: value,
            box_of_screen=lambda *args: None,
            active_time=lambda: now[0],
            next_frame=Mock(return_value=object()),
            find_feature=Mock(side_effect=detections),
            yolo_detect=Mock(side_effect=detections),
            move_to_target_once=Mock(return_value=(10, 0)),
            active_and_send_mouse_delta=Mock(return_value=True),
            log_info=Mock(),
        )
        task.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        return task, target

    def test_semantics_defaults_and_yolo_override(self):
        for ocr, yolo, expected in (
            (False, False, "hud_direction"),
            (False, True, "world_projection"),
            (True, False, "world_projection"),
        ):
            self.assertEqual(resolve_move_profile(None, ocr=ocr, use_yolo=yolo), expected)
        self.assertEqual(resolve_move_profile("hud_direction", ocr=False, use_yolo=True), "world_projection")
        with self.assertRaises(ValueError):
            resolve_move_profile("typo", ocr=False, use_yolo=False)

    def test_ellipse_axes_outer_boost_and_capture_width_scaling(self):
        for width, height in ((1920, 1080), (2560, 1440), (2560, 1080)):
            center = width // 2, height // 2
            geometry = ellipse_geometry(center, min(width / 1920, height / 1080))
            cx, cy, rx, ry = geometry
            horizontal = hud_direction_step((cx + rx, cy), geometry, width)[0]
            vertical = hud_direction_step((cx, cy + ry), geometry, width)[1]
            self.assertEqual(horizontal, vertical)
            inner = hud_direction_step((cx + rx / 2, cy), geometry, width)[0]
            self.assertGreater(horizontal, inner)
            self.assertEqual(hud_direction_step(center, geometry, width), (0, 0))

    def test_feature_alignment_uses_ellipse_and_preserves_detection(self):
        a = SimpleNamespace(x=1600, y=800, width=0, height=0)
        task, _ = self.task([a, SimpleNamespace(x=960, y=540, width=0, height=0)])
        self.assertEqual(
            NavigationMixin.align_ocr_or_find_target_to_center(task, "hud", ocr=False, only_x=True, max_time=1),
            (960, 540),
        )
        task.move_to_target_once.assert_not_called()
        task.active_and_send_mouse_delta.assert_called_once()
        self.assertEqual(task.active_and_send_mouse_delta.call_args.args[1], 0)
        self.assertEqual(a.y, 800)

    def test_yolo_uses_normal_even_with_hud_override(self):
        task, _ = self.task(
            [SimpleNamespace(x=1600, y=800, width=0, height=0), SimpleNamespace(x=960, y=540, width=0, height=0)]
        )
        self.assertTrue(
            NavigationMixin.align_ocr_or_find_target_to_center(
                task, "entity", ocr=False, use_yolo=True, move_profile="hud_direction", max_time=1
            )
        )
        task.move_to_target_once.assert_called_once()
        task.active_and_send_mouse_delta.assert_not_called()

    def test_hud_ghost_cannot_report_alignment_success(self):
        task, target = self.task([None, None])
        detector = task.find_feature
        detector.side_effect = None
        detector.return_value = target(1600, 800)
        with NavigationDetectionScope(task) as scope:
            task.find_feature("hud", threshold=0.8)
            detector.return_value = None
            self.assertFalse(
                NavigationMixin.align_ocr_or_find_target_to_center(
                    task, "hud", ocr=False, only_x=True, allow_random_move=False, max_time=1, raise_if_fail=False
                )
            )
            self.assertTrue(task.active_and_send_mouse_delta.called)
            self.assertTrue(all(call.args[1] == 0 for call in task.active_and_send_mouse_delta.call_args_list))
            self.assertEqual(scope.latest("feature", "hud", threshold=0.8).boxes[0].y, 800)

    def test_rejected_movement_does_not_manufacture_success(self):
        task, _ = self.task([SimpleNamespace(x=1600, y=800, width=0, height=0)] * 2)
        task.active_and_send_mouse_delta.return_value = False
        self.assertFalse(
            NavigationMixin.align_ocr_or_find_target_to_center(
                task, "hud", ocr=False, only_y=True, max_time=1, raise_if_fail=False
            )
        )
        self.assertTrue(all(call.args[0] == 0 for call in task.active_and_send_mouse_delta.call_args_list))

    def test_normal_mover_uses_trackable_sender_and_preserves_steps(self):
        from src.core.base_mixin.runtime_mixin import RuntimeMixin

        task = SimpleNamespace(
            scale_distance=lambda v: v,
            screen_center=lambda: (960, 540),
            active_and_send_mouse_delta=Mock(return_value=True),
        )
        target = SimpleNamespace(x=1600, y=800, width=0, height=0)
        expected = calc_direction_step((960, 540), (1600, 800), max_step=100, min_step=20, slow_radius=200, deadzone=4)
        self.assertEqual(RuntimeMixin.move_to_target_once(task, target), expected)
        task.active_and_send_mouse_delta.assert_called_once_with(*expected, steps=5, delay=0.005)
        task.active_and_send_mouse_delta.return_value = False
        self.assertEqual(RuntimeMixin.move_to_target_once(task, target), (0, 0))

    def test_directional_ocr_opt_in_and_character_name_default(self):
        for profile, uses_hud in ((None, False), ("hud_direction", True), ("world_projection", False)):
            with self.subTest(profile=profile):
                task, _ = self.task([])
                task.ocr = Mock(
                    side_effect=[
                        [SimpleNamespace(x=1600, y=800, width=0, height=0)],
                        [SimpleNamespace(x=960, y=540, width=0, height=0)],
                    ]
                )
                self.assertTrue(
                    NavigationMixin.align_ocr_or_find_target_to_center(
                        task, "name_or_marker", move_profile=profile, max_time=1
                    )
                )
                self.assertEqual(task.active_and_send_mouse_delta.called, uses_hud)
                self.assertEqual(task.move_to_target_once.called, not uses_hud)
