"""Camera preparation must release held inputs and never take or submit a photo."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok import Box, TaskDisabledException
from ok.util.config import Config
from qfluentwidgets import FluentIcon

from src.config import config as app_config
from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask


class TestEnvironmentMonitoringPhotoFlow(unittest.TestCase):
    def test_daily_group_keeps_preview_out_of_daily_completion_plan(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            task = EnvironmentMonitoringPhotoTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
        self.assertEqual(task.group_name, "日常任务")
        self.assertEqual(task.group_icon, FluentIcon.CALENDAR)
        self.assertEqual(task.default_config, {})
        registration = ["src.tasks.onetime.EnvironmentMonitoringPhotoTask", "EnvironmentMonitoringPhotoTask"]
        self.assertEqual(app_config["onetime_tasks"].count(registration), 1)
        self.assertNotIn("环境监测", DailyTask.OTHER_TASKS)

    def task(self):
        task = Mock()
        task.box = SimpleNamespace(right=Box(960, 0, 960, 1080))
        task.box_of_screen.side_effect = lambda x1, y1, x2, y2: Box(
            round(task.frame.shape[1] * x1),
            round(task.frame.shape[0] * y1),
            round(task.frame.shape[1] * (x2 - x1)),
            round(task.frame.shape[0] * (y2 - y1)),
        )
        task.ocr.return_value = []
        task.normalize_title.side_effect = EnvironmentMonitoringPhotoTask.normalize_title
        task._resolve_config_key.side_effect = lambda key, category: {"r": "f6", "w": "up", "d": "right"}.get(key, key)
        task.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.next_frame.side_effect = lambda: task.frame
        task._camera_open.return_value = False
        task.screenshot = Mock()
        task.normalize_pos.side_effect = lambda point: (round(1920 * point[0]), round(1080 * point[1]))
        return task

    def test_movement_cancellation_or_exception_always_releases_resolved_key(self):
        for error in (TaskDisabledException(), RuntimeError("input interrupted")):
            with self.subTest(error=type(error).__name__):
                task = self.task()
                task.sleep.side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask._hold_route_key(task, "w", 0.5)
                task.send_key_down.assert_called_once_with("up")
                task.send_key_up.assert_called_once_with("up")

    def test_failed_focus_prevents_movement_key_down(self):
        task = self.task()
        task._require_input_focus.side_effect = RuntimeError("no focus")
        with self.assertRaisesRegex(RuntimeError, "no focus"):
            EnvironmentMonitoringPhotoTask._hold_route_key(task, "d", 3)
        task.send_key_down.assert_not_called()
        task.sleep.assert_not_called()

    def test_fixed_tool_selection_holds_key_through_hover_without_ocr(self):
        task = self.task()
        events = Mock()
        for method in ("send_key_down", "sleep", "smooth_drag", "send_key_up"):
            events.attach_mock(getattr(task, method), method)
        EnvironmentMonitoringPhotoTask.open_camera(task)
        self.assertEqual(
            events.mock_calls,
            [
                call.send_key_down("f6"),
                call.sleep(1),
                call.smooth_drag((0.5, 0.5), (0.396, 0.312), duration=0.3),
                call.sleep(0.2),
                call.send_key_up("f6"),
            ],
        )
        task.wait_ocr.assert_not_called()
        task.ocr.assert_not_called()

    def test_tool_selection_cancellation_and_drag_failure_release_tool(self):
        for failing_method, error in (
            ("sleep", TaskDisabledException()),
            ("smooth_drag", RuntimeError("drag interrupted")),
        ):
            with self.subTest(failing_method=failing_method):
                task = self.task()
                getattr(task, failing_method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.open_camera(task)
                task.send_key_down.assert_called_once_with("f6")
                task.send_key_up.assert_called_once_with("f6")
                task.press_key.assert_not_called()

    def test_selecting_camera_waits_after_tool_release_before_short_press(self):
        task = self.task()
        task.wait_until.side_effect = [False, True]
        events = Mock()
        events.attach_mock(task.send_key_up, "release")
        events.attach_mock(task.wait_until, "observe")
        events.attach_mock(task.press_key, "press")
        EnvironmentMonitoringPhotoTask.open_camera(task)
        tail = events.mock_calls
        self.assertEqual(tail[0], call.release("f6"))
        self.assertIs(tail[1].args[0], task._camera_open)
        self.assertGreater(tail[1].kwargs["time_out"], 0)
        self.assertEqual(tail[2], call.press("r", down_time=0.15))
        self.assertIs(tail[3].args[0], task._camera_open)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.click.assert_not_called()

    def test_existing_camera_ui_never_reopens_tool_wheel_or_presses_r(self):
        task = self.task()
        task._camera_open.return_value = True
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task._require_input_focus.assert_not_called()
        task.send_key_down.assert_not_called()
        task.wait_ocr.assert_not_called()
        task.press_key.assert_not_called()

    def test_camera_ui_appearing_after_selection_does_not_press_r_again(self):
        task = self.task()
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_camera_layout_requires_both_icons_in_the_supplied_frame(self):
        for escape_present, zoom_present in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(escape=escape_present, zoom=zoom_present):
                task = self.task()
                supplied_frame = np.full_like(task.frame, 170)
                present = {fL.esc: escape_present, fL.plus_button: zoom_present}
                task.find_one.side_effect = lambda feature, present=present, **kwargs: present[feature]
                self.assertEqual(
                    EnvironmentMonitoringPhotoTask._camera_open(task, frame=supplied_frame),
                    escape_present and zoom_present,
                )
                for detection in task.find_one.call_args_list:
                    self.assertIs(detection.kwargs["frame"], supplied_frame)
                    self.assertNotIn("threshold", detection.kwargs)
                task.ocr.assert_not_called()

    def test_failed_camera_entry_is_not_reported_as_open(self):
        task = self.task()
        task.wait_until.side_effect = [False, False]
        with self.assertRaisesRegex(RuntimeError, "未进入拍照界面"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.send_key_up.assert_called_once_with("f6")

    def test_camera_ui_is_required_before_checking_the_marker(self):
        task = self.task()
        task._camera_open.return_value = False
        self.assertFalse(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
        task.find_one.assert_not_called()
        task.ocr.assert_not_called()

    def test_photo_condition_uses_checked_marker_without_ocr(self):
        for marker_found, yellow_ratio, expected in ((False, 1.0, False), (True, 0.0, False), (True, 0.1, True)):
            with self.subTest(marker=marker_found, yellow=yellow_ratio):
                task = self.task()
                task._camera_open.return_value = True
                task.find_one.return_value = Box(52, 726, 26, 24) if marker_found else None
                with patch(
                    "src.tasks.onetime.EnvironmentMonitoringPhotoTask.calculate_color_percentage",
                    return_value=yellow_ratio,
                ) as color:
                    self.assertEqual(EnvironmentMonitoringPhotoTask.photo_condition_met(task), expected)
                self.assertEqual(color.call_count, int(marker_found))
                self.assertEqual(task.find_one.call_args.args[0], fL.photo_target_checked)
                task.ocr.assert_not_called()

    def test_marker_keeps_the_camera_detection_frame(self):
        task = self.task()
        original_frame = task.frame

        def camera_detected(**kwargs):
            self.assertIs(kwargs["frame"], original_frame)
            task.frame = np.full_like(original_frame, 255)
            return True

        task._camera_open.side_effect = camera_detected
        task.find_one.return_value = Box(52, 726, 26, 24)
        with patch(
            "src.tasks.onetime.EnvironmentMonitoringPhotoTask.calculate_color_percentage", return_value=0.1
        ) as color:
            self.assertTrue(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
        self.assertIs(task.find_one.call_args.kwargs["frame"], original_frame)
        self.assertIs(color.call_args.args[0], original_frame)
        task.ocr.assert_not_called()

    def test_route_requires_world_and_does_not_move_in_a_menu(self):
        task = self.task()
        task.in_world.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未处于大世界"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_not_called()
        task.press_key.assert_not_called()

    def test_unstable_world_after_route_prevents_tracking_key(self):
        task = self.task()
        task.in_world.return_value = True
        task.wait_until.return_value = False
        with self.assertRaisesRegex(RuntimeError, "路线结束后未恢复大世界"):
            EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_has_calls([call("w", 0.5), call("d", 3)])
        self.assertGreater(task.wait_until.call_args.kwargs["settle_time"], 0)
        task.press_key.assert_not_called()

    def test_route_uses_world_state_without_mode_checks(self):
        task = self.task()
        task.in_world.return_value = True
        task.wait_until.return_value = True
        EnvironmentMonitoringPhotoTask.walk_pool_lotus_route(task)
        task._hold_route_key.assert_has_calls([call("w", 0.5), call("d", 3)])
        task.sleep.assert_not_called()
        self.assertGreater(task.wait_until.call_args.kwargs["settle_time"], 0)
        task.press_key.assert_called_once_with("v", after_sleep=0.6)
        task.ocr.assert_not_called()

    def test_camera_lost_prevents_any_rotation_gesture(self):
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, "相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
        task.mouse_down.assert_not_called()
        task.smooth_drag.assert_not_called()
        task.mouse_up.assert_not_called()

    def test_rotation_cancellation_or_drag_failure_releases_mouse(self):
        for method, error in (("sleep", TaskDisabledException()), ("smooth_drag", RuntimeError("drag failed"))):
            with self.subTest(method=method):
                task = self.task()
                task._camera_open.return_value = True
                getattr(task, method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
                task.mouse_down.assert_called_once()
                task.mouse_up.assert_called_once_with(key="left")

    def test_initially_satisfied_condition_does_not_rotate(self):
        task = self.task()
        task.photo_condition_met.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        task.next_frame.assert_called_once_with()
        task.photo_condition_met.assert_called_once_with()
        task.rotate_photo_view.assert_not_called()

    def test_rotation_stops_immediately_when_condition_becomes_satisfied(self):
        task = self.task()
        task.photo_condition_met.side_effect = [False, False, True]
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_count, 2)
        self.assertEqual(task.next_frame.call_count, 3)
        self.assertEqual(task.photo_condition_met.call_count, 3)

    def test_unsatisfied_condition_has_at_most_eight_rotations(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        self.assertFalse(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 9)
        task.press_key.assert_not_called()
        task.send_key_down.assert_not_called()

    def test_full_run_selects_only_pool_lotus_and_stops_without_shutter(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                task = self.task()
                task.prepare_observation.return_value = {"name": "“小池荷”"}
                task.adjust_photo_view.return_value = ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = ready
                task.prepare_photo.side_effect = lambda task=task: EnvironmentMonitoringPhotoTask.prepare_photo(task)
                stages = Mock()
                for method in ("prepare_observation", "walk_pool_lotus_route", "prepare_photo"):
                    stages.attach_mock(getattr(task, method), method)
                result = EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
                task.prepare_observation.assert_called_once_with()
                task.walk_pool_lotus_route.assert_called_once_with()
                self.assertEqual(
                    stages.mock_calls,
                    [call.prepare_observation(), call.walk_pool_lotus_route(), call.prepare_photo()],
                )
                task.open_camera.assert_called_once_with()
                task.adjust_photo_view.assert_called_once_with()
                self.assertEqual(
                    result, {"name": "小池荷", "camera_open": True, "photo_ready": ready, "photo_taken": False}
                )
                task.press_key.assert_not_called()
                task.click.assert_not_called()
                task.click_relative.assert_not_called()
                task.send_key_down.assert_not_called()

    def test_navigation_failure_stops_before_camera(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "小池荷"}
        task.walk_pool_lotus_route.side_effect = RuntimeError("navigation failed")
        with self.assertRaisesRegex(RuntimeError, "navigation failed"):
            EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        task.prepare_photo.assert_not_called()

    def test_registered_route_is_selected_from_observation_name(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "“古树”"}
        with patch.dict(
            "src.tasks.onetime.EnvironmentMonitoringEntryTask.OBSERVATION_ROUTES",
            {"古树": "walk_other_route"},
        ):
            EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        task.walk_other_route.assert_called_once_with()
        task.walk_pool_lotus_route.assert_not_called()
        task.prepare_photo.assert_called_once_with()

    def test_unregistered_observation_stops_before_camera(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "未知点位"}
        with self.assertRaisesRegex(RuntimeError, "未支持观察点导航"):
            EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        task.prepare_photo.assert_not_called()

    def test_camera_lost_after_final_rotation_cannot_be_reported_as_open(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.adjust_photo_view.side_effect = lambda: EnvironmentMonitoringPhotoTask.adjust_photo_view(task)
        with self.assertRaisesRegex(RuntimeError, "取景结束时相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 10)
        task._camera_open.assert_called_once_with(frame=task.frame)
        task.ocr.assert_not_called()
        task.log_info.assert_not_called()

    def test_final_frame_decides_ready_instead_of_the_previous_search_result(self):
        for search_ready, final_ready in ((True, False), (False, True)):
            with self.subTest(search_ready=search_ready, final_ready=final_ready):
                task = self.task()
                task.adjust_photo_view.return_value = search_ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = final_ready
                result = EnvironmentMonitoringPhotoTask.prepare_photo(task)
                self.assertEqual(result["photo_ready"], final_ready)
                task._camera_open.assert_called_once_with(frame=task.frame)
                task.photo_condition_met.assert_called_once_with(frame=task.frame)
                task.walk_pool_lotus_route.assert_not_called()
                task.press_key.assert_not_called()
                task.click.assert_not_called()

    def test_camera_failure_prevents_success_result_and_view_adjustment(self):
        task = self.task()
        task.open_camera.side_effect = RuntimeError("camera failed")
        with self.assertRaisesRegex(RuntimeError, "camera failed"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        task.adjust_photo_view.assert_not_called()
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
