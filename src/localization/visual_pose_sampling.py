"""Navigation-scoped visual sampling, independent of blocking control actions."""

import logging
import math
import threading

from ok.task.exceptions import CaptureBusyException, CaptureException

from src.localization.visual_capture_coordination import VisualCaptureCoordinator
from src.localization.wgc_frame_timing import timing_for_frame

logger = logging.getLogger(__name__)


class VisualPoseSampler:
    """Sample between foreground captures without touching the executor's cache."""

    def __init__(self, capture, observe, enabled, *, interval=0.25, capture_timeout=0.08, close_timeout=1.0):
        self.capture = capture
        self.observe = observe
        self.enabled = enabled
        self.interval = float(interval)
        capture_timeout = float(capture_timeout)
        self.close_timeout = float(close_timeout)
        if not all(
            math.isfinite(value) and value > 0 for value in (self.interval, capture_timeout, self.close_timeout)
        ):
            raise ValueError("sampling interval and capture/close timeouts must be finite and positive")
        # This bounds frame waiting only; lifecycle and compositing may take longer.
        self.capture_timeout = min(capture_timeout, self.interval)
        self._requests = VisualCaptureCoordinator(capture)
        self.stopped = threading.Event()
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self._run, name="VisualPoseSampler", daemon=True)

    def start(self):
        self._requests.install()
        try:
            self.thread.start()
        except Exception:
            self._requests.close()
            self.finished.set()
            raise

    def close(self):
        """Bound scope exit; the worker retains capture ownership until it finishes."""
        self.stopped.set()
        self._requests.cancel()
        if self.thread.ident is not None:
            self.thread.join(timeout=self.close_timeout)
        if self.finished.is_set():
            return True
        logger.warning("visual pose sampler is still stopping; capture cleanup deferred")
        return False

    def _run(self):
        try:
            self._sample_loop()
        finally:
            self._requests.close()
            self.finished.set()

    def _sample_loop(self):
        while not self.stopped.wait(self.interval):
            if not self.enabled():
                continue
            try:
                # Admission gives foreground work priority; the backend still owns
                # capture locking and deadlines, including cancellation semantics.
                frame = self._requests.get_frame_for_probe(self.capture_timeout)
                if self.stopped.is_set():
                    return
                timing = timing_for_frame(self.capture, frame)
            except (CaptureBusyException, CaptureException, OSError, ValueError):
                continue
            except Exception:
                logger.exception("visual pose capture failed")
                continue
            if frame is not None and timing is not None and not self.stopped.is_set() and self.enabled():
                try:
                    self.observe(frame[:, :, :3], self.stopped)
                except Exception:
                    # The observer owns state rollback. Continue only after it
                    # returns so a partially applied observation is never retried.
                    logger.exception("visual pose observation failed")
