"""Parallel observations for the existing navigation loop; no input decisions."""

from copy import copy

from src.tasks.mixin.parallel_navigation_detection import NavigationFrame, create_navigation_detectors


def has_tracking_arrival_band(frame):
    from src.image.navigation_arrival_detector import has_tracking_arrival_band as detect

    return detect(frame)


class ParallelNavigationDetection:
    def __init__(self, task, target, nav, box, *, nav_disappears_on_arrival=False):
        self.task = task
        target_box = box if box is not None else task.box_of_screen(0.635, 0.563, 0.724, 0.843)
        nav_box = task.box_of_screen(370 / 1920, 150 / 1080, 1550 / 1920, 930 / 1080)
        self.arrival, self.marker = create_navigation_detectors(task, target, nav, target_box, nav_box)
        self.nav_disappears_on_arrival = nav_disappears_on_arrival
        self.approaching = False
        self.target_priority = False
        self._shape = (task.height, task.width)
        self._last_capture = self._last_target = self._last_nav = float("-inf")
        self._target_after = self._nav_after = float("-inf")
        self._sequence = self._seen_target = 0
        self._nav_sequence_after = 0
        self._observed_nav = 0
        self._nav_missing = False

    def start(self):
        self.arrival.start()
        self.marker.start()

    def poll(self, frame=None):
        # Check failures even when the next capture is not due yet.
        self.arrival.snapshot()
        nav_result, _ = self.marker.snapshot()
        if self._fresh(nav_result, after=self._nav_after, age=0.8) and nav_result.sequence > self._observed_nav:
            self._observed_nav = nav_result.sequence
            self._nav_missing = not nav_result.boxes
        now = self.task.active_time()
        nav_slow = self.approaching or self.target_priority
        target_fast = nav_slow or self._nav_missing
        if frame is None:
            if now - self._last_capture < (0.03 if target_fast else 0.05):
                return
            frame = self.task.next_frame()
        self._last_capture = self.task.active_time()
        if frame is None:
            return
        if frame.shape[:2] != self._shape:
            raise RuntimeError("Capture resolution changed during navigation")
        snapshot = frame.copy()
        snapshot.flags.writeable = False
        if self.nav_disappears_on_arrival and not self.approaching and has_tracking_arrival_band(snapshot):
            self.approaching = True
        nav_slow = self.approaching or self.target_priority
        target_fast = nav_slow or self._nav_missing
        self._sequence += 1
        captured = NavigationFrame(self._sequence, self.task.active_time(), snapshot)
        # 丢标立即提高目标检测频率；未出现到达文本带时，模板也保持高频找回。
        if captured.captured_at - self._last_target >= (0.03 if target_fast else 0.5):
            self.arrival.submit(captured)
            self._last_target = captured.captured_at
        if captured.captured_at - self._last_nav >= (0.5 if nav_slow else 0.05):
            self.marker.submit(captured)
            self._last_nav = captured.captured_at

    def _fresh(self, result, *, after, age):
        return (
            result is not None
            and result.captured_at >= after
            and 0 <= self.task.active_time() - result.captured_at <= age
        )

    def target(self, *, after=None):
        self.poll()
        latest, hit = self.arrival.snapshot()
        cutoff = max(self._target_after, after if after is not None else float("-inf"))
        # A brief hit survives a subsequent miss until the owner has consumed it.
        result = hit if self._fresh(hit, after=cutoff, age=2) and hit.sequence > self._seen_target else latest
        if not self._fresh(result, after=cutoff, age=2) or result.sequence <= self._seen_target:
            return None  # No new observation: never count this as an OCR miss.
        self._seen_target = result.sequence
        return [copy(box) for box in result.boxes]

    def nav(self, frame=None):
        self.poll(frame)
        latest, _ = self.marker.snapshot()
        if not self._fresh(latest, after=self._nav_after, age=0.8) or latest.sequence <= self._nav_sequence_after:
            return None
        return [copy(box) for box in latest.boxes]

    def invalidate_target(self):
        self._target_after = self.task.active_time()
        self._seen_target = self._sequence

    def invalidate_nav(self):
        self._nav_after = self.task.active_time()
        self._nav_sequence_after = self._sequence

    def close(self):
        try:
            self.arrival.close()
        finally:
            self.marker.close()
