"""Navigation-scoped visual sampling, independent of blocking control actions."""

import threading

from ok.task.exceptions import CaptureBusyException, CaptureException

from src.localization.wgc_frame_timing import timing_for_frame


class VisualPoseSampler:
    """One bounded frame request at a time; never touch the executor's frame cache."""

    def __init__(self, capture, observe, enabled, *, interval=0.25):
        self.capture = capture
        self.observe = observe
        self.enabled = enabled
        self.interval = interval
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="VisualPoseSampler", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        # No service lock is held here. Join the bounded capture/observation
        # before the navigation scope can restore or destroy its source.
        self.thread.join()

    def _run(self):
        while not self.stopped.wait(self.interval):
            if not self.enabled():
                continue
            try:
                # Reuse WGC's request lock and absolute deadline. A busy capture
                # skips this round; it never cancels another consumer's request.
                frame = self.capture.get_frame_for_probe(self.interval)
                timing = timing_for_frame(self.capture, frame)
                if frame is not None and timing is not None and not self.stopped.is_set() and self.enabled():
                    self.observe(frame[:, :, :3], self.stopped)
            except (CaptureBusyException, CaptureException, OSError, ValueError):
                # No manufactured observation on failure. The normal frame-age
                # and evidence-gap gates expire the last actual observation.
                continue
