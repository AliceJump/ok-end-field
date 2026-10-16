import ctypes
import struct
import unittest
from dataclasses import replace
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.config import config
from src.tasks.test import pitch_scan_input
from src.tasks.test.pitch_scan_input import MouseSettings, ScanInputEnvironment, split_mouse_delta
from src.tasks.test.PitchScanTestTask import PitchScanTestTask, PitchScanTrajectory


class TestPitchScanTrajectory(unittest.TestCase):
    def test_wider_sweep_preserves_both_axis_speeds(self):
        peak_speeds = []
        for amplitude in (30, 240, 600):
            trajectory = PitchScanTrajectory(amplitude, 1745.45, 0.55 * amplitude / 30, 2)
            step = 0.001
            positions = [trajectory.position(index * step) for index in range(int(trajectory.duration / step) + 1)]
            peak_speeds.append(
                tuple(max(abs(b[axis] - a[axis]) / step for a, b in pairwise(positions)) for axis in (0, 1))
            )
            self.assertAlmostEqual(max(y for _x, y in positions), amplitude, delta=0.01)
            self.assertAlmostEqual(min(y for _x, y in positions), -amplitude, delta=0.01)
        for horizontal, vertical in peak_speeds:
            self.assertAlmostEqual(horizontal, 1745.45, delta=0.01)
            self.assertAlmostEqual(vertical, peak_speeds[0][1], delta=0.02)

    def test_smooth_start_stop_and_zero_final_pitch(self):
        trajectory = PitchScanTrajectory(240, 1745.45, 4.4, 2)
        self.assertEqual(trajectory.position(0), (0, 0))
        self.assertAlmostEqual(trajectory.position(trajectory.duration)[1], 0)
        for offset in (0, trajectory.duration - 0.001):
            start, end = trajectory.position(offset), trajectory.position(offset + 0.001)
            self.assertLess(abs(end[0] - start[0]), 0.001)
            self.assertLess(abs(end[1] - start[1]), 0.001)


class TestPitchScanTask(unittest.TestCase):
    def setUp(self):
        foreground = patch("src.tasks.test.PitchScanTestTask.win32gui.GetForegroundWindow", return_value=123)
        self.foreground = foreground.start()
        self.addCleanup(foreground.stop)
        cursor = patch("src.tasks.test.PitchScanTestTask.win32api.SetCursorPos")
        self.set_cursor = cursor.start()
        self.addCleanup(cursor.stop)

    def make_environment(self, width=1920, height=1080):
        return ScanInputEnvironment(
            123, (width, height), (width, height), (100, 100), 96, 60, MouseSettings(10, (6, 10), 1), (), "missing"
        )

    def make_task(self):
        task = PitchScanTestTask.__new__(PitchScanTestTask)
        task._executor = SimpleNamespace(method=SimpleNamespace(width=1920, height=1080))
        task.config = {"俯仰振幅(1080p像素)": 240, "测试周期数": 1}
        task.log_info = Mock()
        task.active_and_send_mouse_delta = Mock(return_value=True)
        task.click = Mock()
        task.get_game_hwnd = Mock(return_value=123)
        task._read_input_environment = Mock(return_value=self.make_environment())
        return task

    def test_registered_and_instantiable(self):
        self.assertIn(["src.tasks.test.PitchScanTestTask", "PitchScanTestTask"], config["onetime_tasks"])
        task = PitchScanTestTask(executor=SimpleNamespace(scene=None), app=None)
        self.assertEqual(task.default_config["俯仰振幅(1080p像素)"], 240)
        self.assertEqual(task.default_config["测试周期数"], 5)

    @patch("src.tasks.test.PitchScanTestTask.win32gui.GetForegroundWindow", return_value=123)
    @patch("src.tasks.test.PitchScanTestTask.win32api.GetCursorPos", return_value=(1060, 640))
    def test_center_required_before_scan(self, _cursor, _foreground):
        task = self.make_task()
        task._prepare_scan()
        task.active_and_send_mouse_delta.assert_called_once_with(only_activate=True)
        task.click.assert_called_once_with(960, 540, key="middle", down_time=0.02, after_sleep=0.3, move_back=False)
        self.set_cursor.assert_called_once_with((1060, 640))

    def test_activation_failure_emits_no_scan(self):
        task = self.make_task()
        task.active_and_send_mouse_delta.return_value = False
        with self.assertRaisesRegex(RuntimeError, "激活失败"):
            PitchScanTestTask.run.__wrapped__(task)
        task.click.assert_not_called()
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)

    @patch("src.tasks.test.PitchScanTestTask.win32gui.GetForegroundWindow", return_value=123)
    @patch("src.tasks.test.PitchScanTestTask.win32api.GetCursorPos", return_value=(0, 0))
    def test_failed_center_emits_no_scan(self, *_mocks):
        task = self.make_task()
        with self.assertRaisesRegex(RuntimeError, "未归中"):
            PitchScanTestTask.run.__wrapped__(task)
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)

    def test_scheduler_stall_does_not_cause_large_jump_and_stop_propagates(self):
        task = self.make_task()
        task._prepare_scan = Mock(return_value=self.make_environment())
        task.active_time = Mock(side_effect=[0, 2, 2.01])
        task.sleep = Mock(side_effect=[None, None, RuntimeError("stopped")])
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            PitchScanTestTask.run.__wrapped__(task)
        self.assertLessEqual(task.active_and_send_mouse_delta.call_count, 2)
        for call in task.active_and_send_mouse_delta.call_args_list:
            self.assertLessEqual(abs(call.kwargs["dx"]), 36)
            self.assertLessEqual(abs(call.kwargs["dy"]), 7)

    def test_complete_preview_returns_pitch_to_start(self):
        task = self.make_task()
        task._prepare_scan = Mock(return_value=self.make_environment())
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        task.sleep = sleep
        task.active_time = lambda: clock[0]
        PitchScanTestTask.run.__wrapped__(task)
        moves = task.active_and_send_mouse_delta.call_args_list
        self.assertAlmostEqual(clock[0], 4.65, delta=0.02)
        self.assertEqual(sum(move.kwargs["dy"] for move in moves), 0)
        # One pitch cycle retains its duration and amplitude while yaw covers
        # only one eighth of the previous preview's horizontal distance.
        self.assertEqual(sum(move.kwargs["dx"] for move in moves), 960)
        pitch = 0
        positions = []
        for move in moves:
            pitch += move.kwargs["dy"]
            positions.append(pitch)
        self.assertEqual(min(positions), -240)
        self.assertEqual(max(positions), 240)

    def test_five_cycles_scale_each_axis_with_client_resolution(self):
        for width, height, total_x, amplitude in (
            (1920, 1080, 4800, 240),
            (2560, 1440, 6400, 320),
            (3840, 2160, 9600, 480),
            (3440, 1440, 8600, 320),
            (1920, 1200, 4800, 267),
            (1280, 720, 3200, 160),
        ):
            with self.subTest(resolution=(width, height)):
                task = self.make_task()
                # Keep capture at 1080p: scaling must use the physical client,
                # not a resized capture or the saved resolution preference.
                task.config = {}
                environment = self.make_environment(width, height)
                task._prepare_scan = Mock(return_value=environment)
                task._read_input_environment.return_value = environment
                clock = [0.0]

                def sleep(seconds, clock=clock):
                    clock[0] += seconds

                task.sleep = sleep
                task.active_time = lambda clock=clock: clock[0]
                PitchScanTestTask.run.__wrapped__(task)
                moves = [
                    (call.kwargs["dx"], call.kwargs["dy"]) for call in task.active_and_send_mouse_delta.call_args_list
                ]
                self.assertAlmostEqual(clock[0], 22.25, delta=0.02)
                self.assertEqual(sum(dx for dx, _dy in moves), total_x)
                self.assertEqual(sum(dy for _dx, dy in moves), 0)
                pitch = 0
                pitch_positions = []
                for _dx, dy in moves:
                    pitch += dy
                    pitch_positions.append(pitch)
                self.assertEqual(min(pitch_positions), -amplitude)
                self.assertEqual(max(pitch_positions), amplitude)

    @patch("src.tasks.test.PitchScanTestTask.win32gui.GetForegroundWindow", return_value=123)
    @patch("src.tasks.test.PitchScanTestTask.win32api.GetCursorPos", return_value=(1820, 820))
    def test_center_adapts_to_ultrawide_window(self, _cursor, _foreground):
        task = self.make_task()
        task._executor.method.width = 3440
        task._executor.method.height = 1440
        task._read_input_environment.return_value = self.make_environment(3440, 1440)
        task._prepare_scan()
        task.click.assert_called_once_with(1720, 720, key="middle", down_time=0.02, after_sleep=0.3, move_back=False)
        self.set_cursor.assert_called_once_with((1820, 820))

    def test_changed_settings_stop_before_sending_more_input(self):
        baseline = self.make_environment()
        for changes in (
            {"mouse": MouseSettings(12, (6, 10), 1)},
            {"mouse": MouseSettings(10, (2, 4), 2)},
            {"window_dpi": 144},
            {"client_size": (2560, 1440)},
            {"capture_size": (1280, 720)},
            {"client_origin": (0, 0)},
            {"display_refresh_hz": 144},
            {"game_settings": (("controller_camera_speed_x", 1),)},
            {"game_settings_status": "unavailable"},
        ):
            with self.subTest(changes=changes):
                task = self.make_task()
                task._prepare_scan = Mock(return_value=baseline)
                task._read_input_environment.return_value = replace(baseline, **changes)
                task.active_time = Mock(side_effect=[0, 0.01])
                task.sleep = Mock()
                with self.assertRaisesRegex(RuntimeError, "扫描环境发生变化"):
                    PitchScanTestTask.run.__wrapped__(task)
                task.active_and_send_mouse_delta.assert_not_called()

    def test_focus_loss_does_not_reactivate_or_emit_input(self):
        task = self.make_task()
        task._prepare_scan = Mock(return_value=self.make_environment())
        task.active_time = Mock(side_effect=[0, 0.01])
        task.sleep = Mock()
        self.foreground.return_value = 456
        with self.assertRaisesRegex(RuntimeError, "失去焦点"):
            PitchScanTestTask.run.__wrapped__(task)
        task.active_and_send_mouse_delta.assert_not_called()

    def test_environment_is_rechecked_during_scan(self):
        task = self.make_task()
        baseline = self.make_environment()
        task._prepare_scan = Mock(return_value=baseline)
        task._read_input_environment.side_effect = [baseline, replace(baseline, window_dpi=144)]
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        task.sleep = sleep
        task.active_time = lambda: clock[0]
        with self.assertRaisesRegex(RuntimeError, "window_dpi"):
            PitchScanTestTask.run.__wrapped__(task)
        self.assertGreater(task.active_and_send_mouse_delta.call_count, 0)
        self.assertLess(clock[0], 0.55)

    def test_task_splits_packets_without_losing_motion(self):
        task = self.make_task()
        baseline = replace(self.make_environment(), mouse=MouseSettings(10, (2, 4), 1))
        task._prepare_scan = Mock(return_value=baseline)
        task._read_input_environment.return_value = baseline
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        task.sleep = sleep
        task.active_time = lambda: clock[0]
        PitchScanTestTask.run.__wrapped__(task)
        moves = task.active_and_send_mouse_delta.call_args_list
        self.assertEqual(sum(move.kwargs["dx"] for move in moves), 960)
        self.assertEqual(sum(move.kwargs["dy"] for move in moves), 0)
        for move in moves:
            self.assertLessEqual(max(abs(move.kwargs["dx"]), abs(move.kwargs["dy"])), 2)
            self.assertFalse(move.kwargs["activate"])

    def test_native_center_handles_capture_scale_and_window_dpi(self):
        task = self.make_task()
        task._read_input_environment.return_value = replace(self.make_environment(3840, 2160), window_dpi=192)
        with patch("src.tasks.test.PitchScanTestTask.win32api.GetCursorPos", return_value=(2020, 1180)):
            task._prepare_scan()
        self.set_cursor.assert_called_once_with((2020, 1180))


class TestPitchScanInput(unittest.TestCase):
    @patch.object(pitch_scan_input.winreg, "OpenKey", side_effect=FileNotFoundError)
    @patch.object(pitch_scan_input, "_system_parameters_info")
    def test_live_mouse_settings_and_unavailable_values(self, spi, _open):
        def read(action, _param, output, _flags):
            values = (12,) if action == 0x0070 else (3, 8, 2)
            buffer = ctypes.cast(output, ctypes.POINTER(ctypes.c_int))
            for index, value in enumerate(values):
                buffer[index] = value
            return True

        spi.side_effect = read
        settings = pitch_scan_input.read_mouse_settings()
        self.assertEqual((settings.speed, settings.thresholds, settings.acceleration), (12, (3, 8), 2))
        self.assertEqual(settings.pointer_curves_status, "missing")
        spi.side_effect = None
        spi.return_value = False
        settings = pitch_scan_input.read_mouse_settings()
        self.assertIsNone(settings.speed)
        self.assertIsNone(settings.thresholds)
        self.assertIsNone(settings.acceleration)
        self.assertEqual(settings.packet_limit, 1)

    @patch.object(pitch_scan_input.winreg, "OpenKey")
    @patch.object(pitch_scan_input, "_read_raw_registry_value")
    @patch.object(pitch_scan_input, "_registry_value_names")
    def test_only_camera_and_render_settings_are_read(self, names, query, _open):
        names.return_value = [
            "account_token_h123",
            "controller_camera_speed_x_h2607638912",
            "camera_distance_level_h3633770729",
            "video_frame_rate_8_h123",
        ]
        query.side_effect = [(4, struct.pack("<d", 1.25)), (3, b"opaque camera value"), (4, struct.pack("<I", 2000))]
        settings, status = pitch_scan_input.read_game_settings()
        self.assertEqual(status, "ok")
        self.assertEqual(dict(settings)["controller_camera_speed_x"], 1.25)
        self.assertEqual(dict(settings)["video_frame_rate_8"], 2000)
        self.assertIn("sha256=", dict(settings)["camera_distance_level"])
        self.assertNotIn("account_token_h123", [call.args[1] for call in query.call_args_list])
        self.assertEqual(query.call_count, 3)

    def test_camera_floats_use_raw_length_despite_dword_type(self):
        self.assertEqual(
            pitch_scan_input.decode_game_setting("controller_camera_speed_x", 4, struct.pack("<d", 1.25)), 1.25
        )
        self.assertEqual(
            pitch_scan_input.decode_game_setting("controller_camera_speed_y", 4, struct.pack("<f", -0.5)), -0.5
        )
        self.assertEqual(
            pitch_scan_input.decode_game_setting("controller_camera_reverse_x", 4, struct.pack("<I", 1)), 1
        )
        self.assertIsInstance(
            pitch_scan_input.decode_game_setting("controller_camera_speed_x", 4, struct.pack("<d", float("nan"))), str
        )

    def test_missing_and_unreadable_registry_are_distinct(self):
        for error, expected in ((FileNotFoundError(), "missing"), (PermissionError(), "unavailable")):
            with self.subTest(status=expected), patch.object(pitch_scan_input.winreg, "OpenKey", side_effect=error):
                self.assertEqual(pitch_scan_input.read_game_settings(), ((), expected))

    @patch.object(pitch_scan_input, "_set_thread_dpi_context", side_effect=[111, 222])
    def test_dpi_context_is_restored_after_failure(self, setter):
        with self.assertRaisesRegex(ValueError, "probe failed"), pitch_scan_input.physical_coordinates():
            raise ValueError("probe failed")
        self.assertEqual(setter.call_args_list[0].args[0].value, ctypes.c_void_p(-4).value)
        self.assertEqual(setter.call_args_list[1].args, (111,))

    def test_packet_limit_tracks_acceleration_and_unknown_values(self):
        self.assertEqual(MouseSettings(10, (3, 7), 1).packet_limit, 3)
        self.assertEqual(MouseSettings(10, (6, 2), 2).packet_limit, 2)
        self.assertEqual(MouseSettings(10, (0, 0), 0).packet_limit, 6)
        self.assertEqual(MouseSettings(None, (6, 10), 1).packet_limit, 1)

    def test_split_preserves_signed_motion_and_respects_threshold(self):
        for dx, dy in ((7, -13), (-13, 7), (0, 0), (-1, -2), (20, 3)):
            for limit in (1, 2, 6):
                with self.subTest(dx=dx, dy=dy, limit=limit):
                    packets = list(split_mouse_delta(dx, dy, limit))
                    self.assertEqual(sum(x for x, _y in packets), dx)
                    self.assertEqual(sum(y for _x, y in packets), dy)
                    self.assertTrue(all(max(abs(x), abs(y)) <= limit for x, y in packets))
