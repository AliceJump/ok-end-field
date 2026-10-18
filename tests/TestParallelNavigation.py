"""Short arrival prompts must stop input while marker inference is still busy."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.image.navigation_arrival_detector import has_tracking_arrival_band
from src.tasks.mixin.navigation_detection_mode import ParallelNavigationDetection
from src.tasks.mixin.parallel_navigation_detection import (
    LatestFrameDetector,
    NavigationDetection,
    NavigationFrame,
    _clone_ocr,
)
from src.tasks.mixin.search_mixin import SearchMixin
from tests.TestNavigationMixin import NavigationMixin


def detection(x=7):
    return SimpleNamespace(x=x, y=5, width=2, height=2)


class _Task:
    width = height = 16

    def __init__(self):
        self.now = 0.0
        self.events = []
        self.frames = 0
        self.held = None

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def box_of_screen(self, *args):
        return None

    def screen_center(self):
        return 8, 8

    def scale_distance(self, distance):
        return distance / 120

    def next_frame(self):
        self.frames += 1
        return np.zeros((16, 16, 3), dtype=np.uint8)

    def send_key_down(self, key):
        self.events.append(("down", key, self.now))
        self.held = key

    def send_key_up(self, key):
        self.events.append(("up", key, self.now))
        self.held = None

    def press_key(self, key, **kwargs):
        self.events.append(("press", key, self.now))

    def active_and_send_mouse_delta(self, *args, **kwargs):
        self.events.append(("turn", self.now))

    def move_to_target_once(self, *args, **kwargs):
        self.events.append(("turn", self.now))

    def click(self, **kwargs):
        self.events.append(("click", self.now))

    def log_info(self, message, **kwargs):
        self.events.append(("log", message, self.now))

    def move_keys(self, key, duration):
        self.last_move = key
        self.events.append(("step", key, self.now))
        self.send_key_down(key)
        try:
            self.sleep(duration)
        finally:
            self.send_key_up(key)

    def strafe_search(self, *args, **kwargs):
        return SearchMixin.strafe_search(self, *args, **kwargs)

    def rotate_search(self, *args, **kwargs):
        self.events.append(("rotate", self.now))
        return SearchMixin.rotate_search(self, *args, **kwargs)

    def align_ocr_or_find_target_to_center(self, *args, **kwargs):
        return NavigationMixin.align_ocr_or_find_target_to_center(self, *args, **kwargs)


class _ReplayDetector:
    def __init__(self, detect):
        self.detect = detect
        self.latest = self.hit = None
        self.closed = False

    def start(self):
        return self

    def submit(self, frame):
        self.latest = NavigationDetection(frame.sequence, frame.captured_at, tuple(self.detect(frame)))
        if self.latest.boxes:
            self.hit = self.latest

    def snapshot(self):
        return self.latest, self.hit

    def close(self):
        self.closed = True


class TestParallelNavigation(unittest.TestCase):
    def setUp(self):
        mode_patch = patch.dict(
            NavigationMixin.navigate_until_target.__globals__,
            {"ParallelNavigationDetection": ParallelNavigationDetection},
        )
        mode_patch.start()
        self.addCleanup(mode_patch.stop)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Worker did not publish its result")
            time.sleep(0.002)

    def test_pending_frames_are_replaced_and_hits_keep_capture_time(self):
        entered, release = threading.Event(), threading.Event()
        seen = []

        def detect(frame):
            seen.append(frame)
            if frame == 1:
                entered.set()
                release.wait(2)
                return [detection()]
            return []

        worker = LatestFrameDetector(detect, name="test-latest").start()
        try:
            worker.submit(NavigationFrame(1, 10.0, 1))
            self.assertTrue(entered.wait(1))
            worker.submit(NavigationFrame(2, 11.0, 2))
            worker.submit(NavigationFrame(3, 12.0, 3))
            release.set()
            self.wait_for(lambda: worker.snapshot()[0] is not None and worker.snapshot()[0].sequence == 3)
            latest, hit = worker.snapshot()
            self.assertEqual(seen, [1, 3])
            self.assertEqual((latest.sequence, hit.sequence, hit.captured_at), (3, 1, 10.0))
        finally:
            release.set()
            worker.close()
        self.assertFalse(worker._thread.is_alive())

    def test_worker_failure_propagates_without_input_on_worker(self):
        def fail(frame):
            raise ValueError("inference failed")

        worker = LatestFrameDetector(fail, name="test-failure").start()
        try:
            worker.submit(NavigationFrame(1, 0, None))
            self.wait_for(lambda: not worker._thread.is_alive())
            with self.assertRaisesRegex(RuntimeError, "test-failure failed") as raised:
                worker.snapshot()
            self.assertIsInstance(raised.exception.__cause__, ValueError)
        finally:
            worker.close()

    def test_ocr_clone_has_separate_requests_and_preprocessing_state(self):
        source = SimpleNamespace()
        for name in ("text_detector", "text_recognizer"):
            compiled = Mock(outputs=[object()])
            setattr(
                source,
                name,
                SimpleNamespace(
                    is_openvino=True,
                    session=compiled,
                    args=SimpleNamespace(value=[]),
                    preprocess_op=[[]],
                ),
            )
        clone, sessions = _clone_ocr(source)
        self.assertEqual(len(sessions), 2)
        for name in ("text_detector", "text_recognizer"):
            original, worker = getattr(source, name), getattr(clone, name)
            original.session.create_infer_request.assert_called_once()
            self.assertIsNot(original, worker)
            self.assertIsNot(original.preprocess_op, worker.preprocess_op)
            worker.args.value.append(1)
            self.assertEqual(original.args.value, [])
            worker.session(inputs={"frame": object()})
            worker.session.request.infer.assert_called_once()
            original.session.assert_not_called()

    def run_replay(self, task, target, nav, *, timeout=6, band=None, disappearing=True):
        workers = _ReplayDetector(target), _ReplayDetector(nav)
        with (
            patch("src.tasks.mixin.navigation_detection_mode.create_navigation_detectors", return_value=workers),
            patch(
                "src.tasks.mixin.navigation_detection_mode.has_tracking_arrival_band",
                side_effect=band or (lambda frame: False),
            ),
        ):
            result = NavigationMixin.navigate_until_target(
                task,
                target="activate",
                nav=["marker"],
                time_out=timeout,
                detection_mode="parallel",
                nav_disappears_on_arrival=disappearing,
            )
        self.assertTrue(all(worker.closed for worker in workers))
        self.assertIsNone(task.held)
        return result

    def test_band_and_disappearing_marker_keep_forward_until_target_then_use_original_confirmation(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task,
                lambda frame: [detection()] if frame.captured_at >= 0.4 else [],
                lambda frame: [detection()] if frame.captured_at < 0.1 else [],
                band=lambda frame: task.now >= 0.1,
            )
        )
        moves = [event for event in task.events if event[0] in ("down", "up")]
        self.assertEqual([event[:2] for event in moves], [("down", "w"), ("up", "w")])
        self.assertGreaterEqual(moves[-1][2], 0.4)
        self.assertLess(moves[-1][2], 0.5)
        self.assertGreaterEqual(task.now - moves[-1][2], 2)
        self.assertEqual(len([event for event in task.events if event[:2] == ("press", "ctrl")]), 2)

    def test_target_lost_during_confirmation_reuses_actual_wasd_search(self):
        task = _Task()

        def target(frame):
            if 0.1 <= frame.captured_at < 0.15 or getattr(task, "last_move", None) == "a":
                return [detection()]
            return []

        self.assertTrue(self.run_replay(task, target, lambda frame: [], band=lambda frame: True))
        self.assertEqual([event[1] for event in task.events if event[0] == "step"], ["w", "a"])
        self.assertTrue(any("WASD" in event[1] for event in task.events if event[0] == "log"))

    def test_failed_wasd_search_reuses_continuous_backward_and_confirms_after_recovery(self):
        task = _Task()
        recovered = False

        def target(frame):
            nonlocal recovered
            if task.held == "s" and getattr(task, "last_move", None) == "d":
                recovered = True
            return [detection()] if recovered or 0.1 <= frame.captured_at < 0.15 else []

        self.assertTrue(self.run_replay(task, target, lambda frame: [], timeout=10, band=lambda frame: True))
        self.assertEqual([event[1] for event in task.events if event[0] == "step"], list("wasd") * 3)
        self.assertTrue(recovered)
        self.assertTrue(any("后退过程中重新找到目标" in event[1] for event in task.events if event[0] == "log"))

    def test_detection_budget_shifts_after_band_both_detectors_continue(self):
        task = _Task()
        target_frames, nav_frames = [], []

        def target(frame):
            target_frames.append(frame.captured_at)
            return [detection()] if frame.captured_at >= 1.4 else []

        def nav(frame):
            nav_frames.append(frame.captured_at)
            return [detection()] if frame.captured_at < 0.5 else []

        self.assertTrue(self.run_replay(task, target, nav, band=lambda frame: task.now >= 0.5))
        self.assertGreater(len([stamp for stamp in nav_frames if stamp < 0.5]), 5)
        near_nav = [stamp for stamp in nav_frames if 0.5 <= stamp < 1.4]
        near_target = [stamp for stamp in target_frames if 0.5 <= stamp < 1.4]
        self.assertLessEqual(len(near_nav), 2)
        self.assertGreater(len(near_target), len(near_nav) * 5)
        self.assertTrue(any(stamp < 0.5 for stamp in target_frames))
        self.assertTrue(any(stamp > 1.4 for stamp in nav_frames))

    def test_no_band_still_detects_target_at_low_frequency_and_uses_same_confirmation(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task, lambda frame: [detection()] if frame.captured_at >= 0.1 else [], lambda frame: [detection()]
            )
        )
        stopped_at = next(event[2] for event in task.events if event[0] == "up")
        self.assertGreaterEqual(stopped_at, 0.5)
        self.assertGreaterEqual(task.now - stopped_at, 2)

    def test_lost_marker_boosts_target_without_band_and_recovery_restores_normal_budget(self):
        task = _Task()
        target_frames, nav_frames = [], []

        def target(frame):
            target_frames.append(frame.captured_at)
            return []

        def nav(frame):
            nav_frames.append(frame.captured_at)
            return [] if 0.2 <= frame.captured_at < 0.8 else [detection()]

        self.assertFalse(self.run_replay(task, target, nav, timeout=1.4))
        self.assertGreater(len([stamp for stamp in target_frames if 0.3 <= stamp < 0.8]), 8)
        self.assertGreater(len([stamp for stamp in nav_frames if 0.3 <= stamp < 0.8]), 5)
        self.assertLessEqual(len([stamp for stamp in target_frames if 0.9 <= stamp < 1.3]), 1)
        self.assertFalse(any(event[0] == "step" for event in task.events))

    def test_lost_marker_enables_fast_target_detection_even_for_an_undeclared_route(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task,
                lambda frame: [detection()] if frame.captured_at >= 0.25 else [],
                lambda frame: [detection()] if frame.captured_at < 0.1 else [],
                disappearing=False,
            )
        )
        stopped_at = next(event[2] for event in task.events if event[0] == "up")
        self.assertGreaterEqual(stopped_at, 0.25)
        self.assertLess(stopped_at, 0.4)

    def test_parallel_navigation_reuses_existing_alignment_with_async_template_results(self):
        task = _Task()

        def nav(frame):
            turned = any(event[0] == "turn" for event in task.events)
            return [detection(7 if turned else 0)]

        self.assertTrue(self.run_replay(task, lambda frame: [detection()] if frame.captured_at >= 0.4 else [], nav))
        first_turn = next(index for index, event in enumerate(task.events) if event[0] == "turn")
        first_release = next(index for index, event in enumerate(task.events) if event[0] == "up")
        self.assertLess(first_release, first_turn)

    def test_undeclared_disappearing_marker_never_enables_band_behavior(self):
        task = _Task()
        self.assertFalse(
            self.run_replay(
                task,
                lambda frame: [],
                lambda frame: [detection()],
                timeout=0.3,
                band=lambda frame: self.fail("Must not inspect band"),
                disappearing=False,
            )
        )
        self.assertFalse(any(event[0] == "press" for event in task.events))

    def test_no_band_and_missing_marker_reuses_rotation_and_can_stop_on_target(self):
        task = _Task()

        def target(frame):
            return [detection()] if any(event[0] == "rotate" for event in task.events) else []

        self.assertTrue(self.run_replay(task, target, lambda frame: [], timeout=10))
        self.assertTrue(any(event[0] == "rotate" for event in task.events))

    def test_parallel_mode_preserves_max_run_time_and_callbacks(self):
        task = _Task()
        workers = _ReplayDetector(lambda frame: []), _ReplayDetector(lambda frame: [detection()])
        before_loop = Mock()
        with patch("src.tasks.mixin.navigation_detection_mode.create_navigation_detectors", return_value=workers):
            self.assertFalse(
                NavigationMixin.navigate_until_target(
                    task,
                    "activate",
                    "marker",
                    time_out=0.2,
                    detection_mode="parallel",
                    pre_loop_callback=before_loop,
                    max_run_time=0,
                    allow_rotate_search=False,
                )
            )
        before_loop.assert_called()
        self.assertEqual(len([event for event in task.events if event[:2] == ("press", "ctrl")]), 2)

    def test_pending_ocr_is_not_counted_as_four_misses_or_as_success(self):
        task = _Task()
        arrival = _ReplayDetector(lambda frame: [])
        marker = _ReplayDetector(lambda frame: [detection()])
        arrival.hit = NavigationDetection(1, 0, (detection(),))
        arrival.submit = lambda frame: None
        with patch(
            "src.tasks.mixin.navigation_detection_mode.create_navigation_detectors", return_value=(arrival, marker)
        ):
            self.assertFalse(
                NavigationMixin.navigate_until_target(task, "activate", "marker", time_out=1, detection_mode="parallel")
            )
        self.assertFalse(any(event[0] == "step" for event in task.events))
        self.assertGreaterEqual(task.now, 1)

    def test_backward_exception_releases_s_and_closes_workers(self):
        task = _Task()
        sleep = task.sleep

        def stop_during_backward(seconds):
            if task.held == "s":
                raise RuntimeError("task disabled")
            sleep(seconds)

        task.sleep = stop_during_backward
        # Isolate reaching the continuous backward recovery, keeping the original branch.
        task.strafe_search = lambda *args, **kwargs: False
        workers = (
            _ReplayDetector(lambda frame: [detection()] if 0.1 <= frame.captured_at < 0.15 else []),
            _ReplayDetector(lambda frame: []),
        )
        with (
            patch("src.tasks.mixin.navigation_detection_mode.create_navigation_detectors", return_value=workers),
            patch("src.tasks.mixin.navigation_detection_mode.has_tracking_arrival_band", return_value=True),
            self.assertRaisesRegex(RuntimeError, "task disabled"),
        ):
            NavigationMixin.navigate_until_target(
                task, "activate", "marker", detection_mode="parallel", nav_disappears_on_arrival=True
            )
        self.assertIsNone(task.held)
        self.assertTrue(all(worker.closed for worker in workers))

    def test_default_detection_mode_does_not_construct_workers(self):
        task = _Task()
        task.ocr = lambda *args, **kwargs: [detection()]
        with patch.dict(
            NavigationMixin.navigate_until_target.__globals__,
            {"ParallelNavigationDetection": Mock(side_effect=AssertionError("worker"))},
        ):
            self.assertTrue(NavigationMixin.navigate_until_target(task, "activate", "marker"))

    def test_ocr_stops_movement_while_marker_worker_is_blocked(self):
        owner = threading.get_ident()
        blocked, release = threading.Event(), threading.Event()
        started = time.monotonic()
        task = _Task()
        task.active_time = lambda: time.monotonic() - started
        task.sleep = lambda seconds: time.sleep(min(seconds, 0.01))
        position = 0.0
        last_tick = started
        capture = task.next_frame
        input_threads = []
        down, up = task.send_key_down, task.send_key_up
        task.send_key_down = lambda key: (input_threads.append(threading.get_ident()), down(key))
        task.send_key_up = lambda key: (input_threads.append(threading.get_ident()), up(key))

        def next_frame():
            nonlocal position, last_tick
            now = time.monotonic()
            if task.held == "w":
                position += now - last_tick
            last_tick = now
            frame = capture()
            frame[0, 0, 0] = 1 if 0.08 <= position <= 0.35 else 0
            return frame

        task.next_frame = next_frame

        def marker_detect(frame):
            blocked.set()
            release.wait(2)
            return [detection()]

        def arrival_detect(frame):
            return [detection()] if frame[0, 0, 0] else []

        arrival = LatestFrameDetector(arrival_detect, name="test-arrival")
        marker = LatestFrameDetector(marker_detect, name="test-marker")

        def stop_check(seconds):
            if blocked.is_set() and any(event[:2] == ("up", "w") for event in task.events):
                release.set()
            time.sleep(min(seconds, 0.01))

        task.sleep = stop_check
        try:
            with (
                patch(
                    "src.tasks.mixin.navigation_detection_mode.create_navigation_detectors",
                    return_value=(arrival, marker),
                ),
                patch("src.tasks.mixin.navigation_detection_mode.has_tracking_arrival_band", return_value=True),
            ):
                self.assertTrue(
                    NavigationMixin.navigate_until_target(
                        task,
                        target="activate",
                        nav="marker",
                        time_out=4,
                        detection_mode="parallel",
                        nav_disappears_on_arrival=True,
                    )
                )
        finally:
            release.set()
            arrival.close()
            marker.close()
        self.assertTrue(blocked.is_set())
        self.assertTrue(release.is_set())
        self.assertEqual(set(input_threads), {owner})
        self.assertLess(position, 0.35)

    def test_arrival_band_scales_and_rejects_blank_or_solid_regions(self):
        base = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for x in range(887, 1022, 20):
            cv2.putText(base, "E", (x, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            self.assertTrue(has_tracking_arrival_band(cv2.resize(base, (width, height))))
        self.assertFalse(has_tracking_arrival_band(np.zeros_like(base)))
        self.assertFalse(has_tracking_arrival_band(np.full_like(base, 255)))
        self.assertFalse(has_tracking_arrival_band(None))


if __name__ == "__main__":
    unittest.main()
