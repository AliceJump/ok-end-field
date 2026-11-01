"""Camera preparation must release held inputs and never take or submit a photo."""

import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok import Box, TaskDisabledException

from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask


class TestEnvironmentMonitoringPhotoFlow(unittest.TestCase):
    def task(self):
        task = Mock()
        task.lang = SimpleNamespace(
            environment_monitoring=SimpleNamespace(
                industrial=re.compile("工业"),
                tool_wheel=re.compile("选择工具"),
                camera_tool=re.compile("^拍照$"),
                camera_exit=re.compile("退出拍照"),
                pool_lotus_photo=re.compile("拍摄.*小池荷"),
            )
        )
        task.box = SimpleNamespace(right=Box(960, 0, 960, 1080))
        task.box_of_screen.side_effect = lambda x1, y1, x2, y2: Box(
            round(1920 * x1), round(1080 * y1), round(1920 * (x2 - x1)), round(1080 * (y2 - y1))
        )
        task.ocr.return_value = []
        task.wait_ocr.return_value = [Box(1500, 800, 150, 40)]
        task._resolve_config_key.side_effect = lambda key, category: {"r": "f6", "w": "up", "d": "right"}.get(key, key)
        task.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
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

    def test_missing_wheel_releases_tool_without_cursor_move_or_short_press(self):
        task = self.task()
        task.wait_ocr.return_value = []
        diagnostics = Mock()
        diagnostics.attach_mock(task.screenshot, "capture")
        diagnostics.attach_mock(task.send_key_up, "release")
        with self.assertRaisesRegex(RuntimeError, "轮盘未打开"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_down.assert_called_once_with("f6")
        task.send_key_up.assert_called_once_with("f6")
        task.smooth_drag.assert_not_called()
        task.press_key.assert_not_called()
        task.log_info.assert_called_once()
        self.assertEqual(diagnostics.mock_calls[0].args, ("environment-monitoring-tool-wheel-missing",))
        self.assertIs(diagnostics.mock_calls[0].kwargs["frame"], task.frame)
        self.assertEqual(diagnostics.mock_calls[1], call.release("f6"))

    def test_failed_diagnostics_preserve_wheel_error_and_release_tool_key(self):
        for diagnostic in ("ocr", "log_info", "screenshot"):
            with self.subTest(diagnostic=diagnostic):
                task = self.task()
                task.wait_ocr.return_value = []
                if diagnostic == "ocr":
                    task.ocr.side_effect = [[], RuntimeError("diagnostic OCR failed")]
                else:
                    getattr(task, diagnostic).side_effect = RuntimeError("diagnostic failed")
                with self.assertRaisesRegex(RuntimeError, "快捷工具轮盘未打开"):
                    EnvironmentMonitoringPhotoTask.open_camera(task)
                task.send_key_up.assert_called_once_with("f6")
                task.smooth_drag.assert_not_called()
                task.press_key.assert_not_called()

    def test_cancellation_during_diagnostics_is_propagated_and_releases_tool_key(self):
        task = self.task()
        task.wait_ocr.return_value = []
        task.screenshot.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_hover_without_camera_label_releases_tool_without_activating_it(self):
        task = self.task()
        task.wait_ocr.side_effect = [[Box(700, 500, 100, 30)], []]
        with self.assertRaisesRegex(RuntimeError, "未选中相机"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.smooth_drag.assert_called_once()
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_tool_selection_cancellation_and_drag_failure_release_tool(self):
        for failing_method, error in (
            ("wait_ocr", TaskDisabledException()),
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
        task.wait_ocr.side_effect = [[Box(700, 500, 100, 30)], [Box(900, 500, 100, 30)], [], [Box(1700, 800, 150, 30)]]
        events = Mock()
        events.attach_mock(task.send_key_up, "release")
        events.attach_mock(task.wait_ocr, "observe")
        events.attach_mock(task.press_key, "press")
        EnvironmentMonitoringPhotoTask.open_camera(task)
        tail = events.mock_calls[2:]
        self.assertEqual(tail[0], call.release("f6"))
        self.assertEqual(tail[1].kwargs["match"], task.lang.environment_monitoring.camera_exit)
        self.assertGreater(tail[1].kwargs["time_out"], 0)
        self.assertEqual(tail[2], call.press("r", down_time=0.15))
        self.assertEqual(tail[3].kwargs["match"], task.lang.environment_monitoring.camera_exit)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.click.assert_not_called()

    def test_existing_camera_ui_never_reopens_tool_wheel_or_presses_r(self):
        task = self.task()
        task.ocr.return_value = [Box(1700, 800, 150, 30)]
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

    def test_failed_camera_entry_is_not_reported_as_open(self):
        task = self.task()
        task.wait_ocr.side_effect = [[Box(700, 500, 100, 30)], [Box(900, 500, 100, 30)], [], []]
        with self.assertRaisesRegex(RuntimeError, "未进入拍照界面"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.send_key_up.assert_called_once_with("f6")

    def test_camera_ui_and_correct_target_are_both_required_for_checked_icon(self):
        for recognized in ([], [[Box(1700, 800, 150, 30)], []]):
            with self.subTest(recognized=recognized):
                task = self.task()
                if recognized:
                    task.ocr.side_effect = recognized
                with patch(
                    "src.tasks.onetime.EnvironmentMonitoringPhotoTask.xiaochihe_target_checked", return_value=True
                ) as checked:
                    self.assertFalse(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
                checked.assert_not_called()

    def test_correct_camera_target_still_requires_a_checked_marker(self):
        for checked_result in (False, True):
            with self.subTest(checked=checked_result):
                task = self.task()
                task.ocr.return_value = [Box(100, 720, 150, 30)]
                with patch(
                    "src.tasks.onetime.EnvironmentMonitoringPhotoTask.xiaochihe_target_checked",
                    return_value=checked_result,
                ) as checked:
                    self.assertEqual(EnvironmentMonitoringPhotoTask.photo_condition_met(task), checked_result)
                checked.assert_called_once_with(task.frame)

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
                task.ocr.return_value = [Box(1700, 800, 150, 30)]
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
                task.adjust_photo_view.return_value = ready
                task.ocr.return_value = [Box(1700, 800, 150, 30)]
                task.prepare_photo.side_effect = lambda task=task: EnvironmentMonitoringPhotoTask.prepare_photo(task)
                result = EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
                task.prepare_observation.assert_called_once_with("小池荷")
                task.walk_pool_lotus_route.assert_called_once_with()
                task.open_camera.assert_called_once_with()
                task.adjust_photo_view.assert_called_once_with()
                self.assertEqual(
                    result, {"name": "小池荷", "camera_open": True, "photo_ready": ready, "photo_taken": False}
                )
                task.press_key.assert_not_called()
                task.click.assert_not_called()
                task.click_relative.assert_not_called()
                task.send_key_down.assert_not_called()

    def test_camera_lost_after_final_rotation_cannot_be_reported_as_open(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.adjust_photo_view.side_effect = lambda: EnvironmentMonitoringPhotoTask.adjust_photo_view(task)
        with self.assertRaisesRegex(RuntimeError, "取景结束时相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 10)
        task.ocr.assert_called_once_with(match=task.lang.environment_monitoring.camera_exit, box=task.box.right)
        task.log_info.assert_not_called()

    def test_camera_failure_prevents_success_result_and_view_adjustment(self):
        task = self.task()
        task.open_camera.side_effect = RuntimeError("camera failed")
        with self.assertRaisesRegex(RuntimeError, "camera failed"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        task.adjust_photo_view.assert_not_called()
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
