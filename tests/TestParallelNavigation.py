"""Short arrival prompts must stop input while marker inference is still busy."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.image.navigation_arrival_detector import has_tracking_arrival_band
from src.tasks.mixin.parallel_navigation import navigate_parallel
from src.tasks.mixin.parallel_navigation_detection import (
    LatestFrameDetector,
    NavigationDetection,
    NavigationFrame,
    _clone_ocr,
)


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

    def press_key(self, key):
        self.events.append(("press", key, self.now))

    def active_and_send_mouse_delta(self, *args, **kwargs):
        self.events.append(("turn", self.now))

    def move_to_target_once(self, *args, **kwargs):
        self.events.append(("turn", self.now))

    def click(self, **kwargs):
        self.events.append(("click", self.now))

    def log_info(self, *args):
        pass


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

    def run_replay(self, task, target, nav, *, timeout=1, band=None):
        workers = _ReplayDetector(target), _ReplayDetector(nav)
        with (
            patch("src.tasks.mixin.parallel_navigation.create_navigation_detectors", return_value=workers),
            patch(
                "src.tasks.mixin.parallel_navigation.has_tracking_arrival_band", side_effect=band or (lambda _: False)
            ),
        ):
            result = navigate_parallel(task, target="activate", nav=["marker"], box=None, time_out=timeout)
        self.assertTrue(all(worker.closed for worker in workers))
        self.assertIsNone(task.held)
        return result

    def test_arrival_stops_before_confirmation_and_never_resumes_on_old_marker(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task,
                lambda frame: [detection()] if frame.captured_at >= 0.15 else [],
                lambda frame: [detection()],
                band=lambda frame: task.now >= 0.1,
            )
        )
        movements = [event[:2] for event in task.events if event[0] in ("up", "down")]
        self.assertEqual(movements, [("down", "w"), ("up", "w")])
        self.assertLess(next(event[2] for event in task.events if event[0] == "up"), 0.25)

    def test_missing_markers_do_not_walk_blindly_and_deadline_cuts_off_scan(self):
        task = _Task()
        self.assertFalse(self.run_replay(task, lambda frame: [], lambda frame: [], timeout=6))
        self.assertFalse(any(event[:2] == ("down", "w") for event in task.events))
        self.assertTrue(any(event[0] == "turn" for event in task.events))
        self.assertLess(task.now, 6.05)

    def test_stale_arrival_hit_cannot_confirm_success(self):
        task = _Task()
        arrival, marker = _ReplayDetector(lambda frame: []), _ReplayDetector(lambda frame: [])
        arrival.hit = NavigationDetection(1, -3, (detection(),))
        with patch("src.tasks.mixin.parallel_navigation.create_navigation_detectors", return_value=(arrival, marker)):
            self.assertFalse(navigate_parallel(task, target="activate", nav="marker", box=None, time_out=0.2))
        self.assertFalse(any(event[0] == "down" for event in task.events))

    def test_capture_failure_releases_input_and_closes_both_workers(self):
        task = _Task()
        original = task.next_frame

        def capture():
            if task.held:
                raise RuntimeError("capture failed")
            return original()

        task.next_frame = capture
        workers = _ReplayDetector(lambda frame: []), _ReplayDetector(lambda frame: [detection()])
        with (
            patch("src.tasks.mixin.parallel_navigation.create_navigation_detectors", return_value=workers),
            self.assertRaisesRegex(RuntimeError, "capture failed"),
        ):
            navigate_parallel(task, target="activate", nav="marker", box=None, time_out=1)
        self.assertIsNone(task.held)
        self.assertTrue(all(worker.closed for worker in workers))

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
                    "src.tasks.mixin.parallel_navigation.create_navigation_detectors", return_value=(arrival, marker)
                ),
                patch("src.tasks.mixin.parallel_navigation.has_tracking_arrival_band", return_value=True),
            ):
                self.assertTrue(navigate_parallel(task, target="activate", nav="marker", box=None, time_out=2))
        finally:
            release.set()
            arrival.close()
            marker.close()
        self.assertTrue(blocked.is_set())
        self.assertTrue(release.is_set())
        self.assertEqual(set(input_threads), {owner})
        self.assertLess(position, 0.35)

    def test_band_then_disappearing_marker_keeps_w_until_interaction_hit(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task,
                lambda frame: [detection()] if frame.captured_at >= 0.4 else [],
                lambda frame: [detection()] if frame.captured_at < 0.1 else [],
                band=lambda frame: 0.1 <= task.now <= 0.14,
            )
        )
        movements = [event for event in task.events if event[0] in ("up", "down")]
        self.assertEqual([event[:2] for event in movements], [("down", "w"), ("up", "w")])
        self.assertGreaterEqual(movements[-1][2], 0.4)
        self.assertLess(movements[-1][2], 0.5)
        self.assertEqual(
            [event[:2] for event in task.events if event[0] == "press"], [("press", "ctrl"), ("press", "ctrl")]
        )
        self.assertFalse(any(event[0] in ("turn", "click") for event in task.events))

    def test_far_navigation_keeps_target_at_low_frequency_and_keeps_sprint(self):
        task = _Task()
        target_times = []

        def target(frame):
            target_times.append(frame.captured_at)
            return []

        self.assertFalse(self.run_replay(task, target, lambda frame: [detection()], timeout=1.1))
        self.assertEqual(len(target_times), 3)
        self.assertFalse(any(event[0] == "press" for event in task.events))

    def test_detection_budget_shifts_to_target_after_band_and_keeps_both_running(self):
        task = _Task()
        target_frames, nav_frames = [], []

        def target(frame):
            target_frames.append(frame.captured_at)
            return [detection()] if frame.captured_at >= 1.4 else []

        def nav(frame):
            nav_frames.append(frame.captured_at)
            return [detection()] if frame.captured_at < 0.5 else []

        self.assertTrue(self.run_replay(task, target, nav, timeout=3, band=lambda frame: task.now >= 0.5))
        far_nav = [stamp for stamp in nav_frames if stamp < 0.5]
        near_nav = [stamp for stamp in nav_frames if 0.5 <= stamp < 1.4]
        near_target = [stamp for stamp in target_frames if 0.5 <= stamp < 1.4]
        self.assertGreater(len(far_nav), 5)
        self.assertLessEqual(len(near_nav), 2)
        self.assertGreater(len(near_target), len(near_nav) * 5)
        self.assertTrue(any(stamp < 0.5 for stamp in target_frames))
        self.assertTrue(any(stamp > 1.4 for stamp in nav_frames))

    def test_visible_interaction_can_stop_navigation_without_band_or_disappearing_icon(self):
        task = _Task()
        self.assertTrue(
            self.run_replay(
                task,
                lambda frame: [detection()] if frame.captured_at >= 0.1 else [],
                lambda frame: [detection()],
                timeout=2,
            )
        )
        self.assertFalse(any(event[0] == "press" for event in task.events))

    def test_band_alone_is_not_arrival_and_continuous_approach_has_a_limit(self):
        task = _Task()
        self.assertFalse(self.run_replay(task, lambda frame: [], lambda frame: [], timeout=20, band=lambda frame: True))
        movements = [event for event in task.events if event[0] in ("up", "down")]
        self.assertEqual([event[:2] for event in movements], [("down", "w"), ("up", "w")])
        self.assertGreaterEqual(task.now, 10)
        self.assertLess(task.now, 10.1)

    def test_arrival_band_geometry_scales_and_rejects_blank_or_solid_regions(self):
        base = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for x in range(887, 1022, 20):
            cv2.putText(base, "E", (x, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            self.assertTrue(has_tracking_arrival_band(cv2.resize(base, (width, height))))
        self.assertFalse(has_tracking_arrival_band(np.zeros_like(base)))
        self.assertFalse(has_tracking_arrival_band(np.full_like(base, 255)))
        self.assertFalse(has_tracking_arrival_band(None))

    def test_battle_does_not_activate_after_failed_parallel_navigation(self):
        from src.tasks.onetime.BattleTask import BattleTask

        task = SimpleNamespace(battle_ctx=SimpleNamespace(), navigate_to_gather=Mock(return_value=False))
        self.assertFalse(BattleTask.battle_gather(task))


if __name__ == "__main__":
    unittest.main()
