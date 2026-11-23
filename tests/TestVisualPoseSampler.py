import threading
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.localization.visual_capture_coordination import VisualCaptureCoordinator
from src.localization.visual_pose_sampling import VisualPoseSampler
from src.localization.wgc_frame_timing import FrameTiming, timing_for_frame


class TestVisualPoseSampler(unittest.TestCase):
    def test_background_ready_wakes_fresh_capture_without_waiting_for_interval(self):
        frame = np.ones((20, 20, 3), np.uint8)
        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=Mock(return_value=frame))
        observed = threading.Event()
        frames = []

        def observe(value, stopped):
            frames.append(value.copy())
            observed.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=10, capture_timeout=0.01)
        with patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()):
            sampler.start()
            try:
                sampler.wake()
                self.assertTrue(observed.wait(2))
                self.assertEqual(len(frames), 1)
                np.testing.assert_array_equal(frames[0], frame)
                capture.get_frame_for_probe.assert_called_once()
            finally:
                self.assertTrue(sampler.close())
        self.assertFalse(sampler.thread.is_alive())

    def test_stalled_capture_has_bounded_exit_and_deferred_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        original = Mock()

        def stuck(_timeout):
            entered.set()
            release.wait(3)
            return np.zeros((20, 20, 3), np.uint8)

        capture = SimpleNamespace(get_frame=original, get_frame_for_probe=stuck)
        observe = Mock()
        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01, close_timeout=0.02)
        sampler.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(sampler.close())
            self.assertTrue(sampler.thread.is_alive())
            self.assertIsNot(capture.get_frame, original)
            from ok.task.exceptions import CaptureException

            with self.assertRaises(CaptureException):
                capture.get_frame(busy_wait=0.1)
            self.assertFalse(sampler.finished.is_set())
        finally:
            release.set()
            sampler.thread.join(3)
        self.assertIs(capture.get_frame, original)
        observe.assert_not_called()

    def test_foreground_wait_has_deadline_when_probe_does_not_return(self):
        from ok.task.exceptions import CaptureException

        entered, release = threading.Event(), threading.Event()
        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=lambda _: (entered.set(), release.wait(3)))
        requests = VisualCaptureCoordinator(capture, foreground_timeout=0.02)
        requests.install()
        worker = threading.Thread(target=lambda: requests.get_frame_for_probe(0.08))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(CaptureException, "stalled"):
                capture.get_frame(busy_wait=0.1)
        finally:
            release.set()
            worker.join(3)
            requests.close()

    def test_delayed_probe_does_not_send_real_executor_into_busy_retry(self):
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
        from ok.task.TaskExecutor import TaskExecutor

        entered, release, navigating = (threading.Event() for _ in range(3))
        frame = np.zeros((20, 20, 4), np.uint8)
        capture = object.__new__(WindowsGraphicsCaptureMethod)
        capture.exit_event = threading.Event()
        capture.get_frame_lock = threading.RLock()
        capture._size = (0, 0)

        def delayed_capture(frame_deadline=None):
            if frame_deadline is not None:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test did not release probe")
            return frame

        capture._do_get_frame = delayed_capture
        original = capture.get_frame
        executor = SimpleNamespace(
            method=capture,
            exit_event=capture.exit_event,
            reset_scene=Mock(),
            check_enabled=Mock(),
            can_capture=lambda: True,
            blur_overlay_processor=None,
            sleep=Mock(side_effect=AssertionError("probe caused foreground retry sleep")),
        )
        results, errors = [], []

        def navigate():
            navigating.set()
            try:
                results.append(TaskExecutor.next_frame(executor))
            except Exception as exc:
                errors.append(exc)

        sampler = VisualPoseSampler(capture, Mock(), lambda: True, interval=0.01)
        sampler.start()
        worker = threading.Thread(target=navigate)
        try:
            self.assertTrue(entered.wait(2))
            worker.start()
            self.assertTrue(navigating.wait(2))
            # Exceed TaskExecutor's real 0.1 s lock timeout while the probe
            # holds the actual WGC request lock, as slow compositing can do.
            worker.join(0.2)
            self.assertTrue(worker.is_alive())
            sampler.stopped.set()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(3)
            sampler.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        np.testing.assert_array_equal(results[0], frame[:, :, :3])
        executor.sleep.assert_not_called()
        self.assertEqual(capture.get_frame, original)
        self.assertNotIn("get_frame", vars(capture))

    def test_foreground_capture_blocks_new_probes_and_restores_override(self):
        entered, release = threading.Event(), threading.Event()

        def foreground(*args, **kwargs):
            entered.set()
            release.wait(3)

        capture = SimpleNamespace(get_frame=foreground, get_frame_for_probe=Mock())
        requests = VisualCaptureCoordinator(capture)
        requests.install()
        worker = threading.Thread(target=capture.get_frame)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            from ok.task.exceptions import CaptureBusyException

            with self.assertRaises(CaptureBusyException):
                requests.get_frame_for_probe(0.08)
            capture.get_frame_for_probe.assert_not_called()
        finally:
            release.set()
            worker.join(3)
            requests.close()
        self.assertIs(capture.get_frame, foreground)

    def test_failed_probe_releases_foreground_and_failed_start_restores_capture(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(side_effect=RuntimeError("capture failed"))
        )
        original = capture.get_frame
        requests = VisualCaptureCoordinator(capture)
        requests.install()
        try:
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                requests.get_frame_for_probe(0.08)
            self.assertFalse(requests._probe_active)
            capture.get_frame(busy_wait=0.1)
            original.assert_called_once_with(busy_wait=0.1)
        finally:
            requests.close()
        sampler = VisualPoseSampler(capture, Mock(), lambda: True)
        with (
            patch.object(sampler.thread, "start", side_effect=RuntimeError("start failed")),
            self.assertRaisesRegex(RuntimeError, "start failed"),
        ):
            sampler.start()
        self.assertIs(capture.get_frame, original)

    def test_observation_error_is_logged_and_sampling_continues(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 3), np.uint8))
        )
        continued = threading.Event()
        calls = []

        def observe(frame, stopped):
            calls.append(frame)
            if len(calls) == 1:
                raise ValueError("observer state failed")
            continued.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with (
            patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
            patch("src.localization.visual_pose_sampling.logger.exception") as log,
        ):
            sampler.start()
            try:
                self.assertTrue(continued.wait(2))
            finally:
                sampler.close()
        self.assertGreaterEqual(len(calls), 2)
        log.assert_called_once()

    def test_capture_error_is_logged_and_sampling_continues(self):
        frame = np.zeros((20, 20, 3), np.uint8)
        calls = 0
        continued = threading.Event()

        def get_frame(_timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("capture backend failed")
            continued.set()
            return frame

        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=get_frame)
        sampler = VisualPoseSampler(capture, Mock(), lambda: True, interval=0.01)
        with (
            patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
            patch("src.localization.visual_pose_sampling.logger.exception") as log,
        ):
            sampler.start()
            try:
                self.assertTrue(continued.wait(2))
            finally:
                sampler.close()
        log.assert_called_once()

    def test_probe_timing_survives_rgb_slice_into_observation(self):
        frame = np.zeros((20, 20, 4), np.uint8)
        observed = threading.Event()
        timing = FrameTiming(100.0, 1, 100.0, 0.0001)
        capture = SimpleNamespace(
            lock=threading.RLock(),
            get_frame=Mock(),
            get_frame_for_probe=Mock(return_value=frame),
            _localization_timed_image=weakref.ref(frame),
            _localization_frame_timing=timing,
        )

        def observe(sliced, stopped):
            self.assertEqual(sliced.shape, (20, 20, 3))
            self.assertIs(timing_for_frame(capture, sliced), timing)
            observed.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        sampler.start()
        try:
            self.assertTrue(observed.wait(2))
        finally:
            sampler.close()

    def test_sampling_progresses_during_blocked_control_and_stops_on_close(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 4), np.uint8))
        )
        observed = threading.Event()
        calls = []

        def observe(frame, stopped):
            self.assertEqual(frame.shape, (20, 20, 3))
            calls.append(frame)
            if len(calls) >= 3:
                observed.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()):
            sampler.start()
            try:
                # Control performs no world_pose calls during this wait.
                self.assertTrue(observed.wait(2))
            finally:
                sampler.close()
        self.assertFalse(sampler.thread.is_alive())
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(call.args == (0.01,) for call in capture.get_frame_for_probe.call_args_list))

    def test_paused_or_untimed_frames_do_not_create_observations(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 3), np.uint8))
        )
        observe = Mock()
        checked = threading.Event()

        def disabled():
            checked.set()
            return False

        sampler = VisualPoseSampler(capture, observe, disabled, interval=0.01)
        sampler.start()
        try:
            self.assertTrue(checked.wait(2))
        finally:
            sampler.close()
        capture.get_frame_for_probe.assert_not_called()
        # A frame without a WGC presentation clock cannot be fed as a new sample.
        done = threading.Event()

        def no_timing(*args):
            done.set()
            return None

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with patch("src.localization.visual_pose_sampling.timing_for_frame", side_effect=no_timing):
            sampler.start()
            try:
                self.assertTrue(done.wait(2))
            finally:
                sampler.close()
        observe.assert_not_called()

    def test_cancelled_inflight_capture_cannot_publish_after_scope_exit(self):
        entered, release = threading.Event(), threading.Event()

        def frame(timeout):
            entered.set()
            self.assertTrue(release.wait(2))
            return np.zeros((20, 20, 3), np.uint8)

        observe = Mock()
        sampler = VisualPoseSampler(
            SimpleNamespace(get_frame=Mock(), get_frame_for_probe=frame), observe, lambda: True, interval=0.01
        )
        with patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()):
            sampler.start()
            self.assertTrue(entered.wait(2))
            sampler.stopped.set()
            release.set()
            sampler.close()
        observe.assert_not_called()
        self.assertFalse(sampler.thread.is_alive())
