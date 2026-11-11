"""Coordinate foreground screenshots with navigation's opportunistic probes."""

import threading

from ok.task.exceptions import CaptureBusyException


class VisualCaptureCoordinator:
    """Keep probe contention out of the foreground's short capture-lock timeout.

    A probe cannot preempt lifecycle/cropping/compositing already in progress.
    Foreground callers wait for that request to finish, then use the unchanged
    capture API. Waiting callers take priority over new probes. This removes the
    executor's extra retry sleep, not the capture backend's own processing time.
    """

    def __init__(self, capture):
        self.capture = capture
        self._condition = threading.Condition()
        self._probe_active = False
        self._foreground_requests = 0
        self._original = capture.get_frame
        self._had_override = "get_frame" in vars(capture)
        self._override = vars(capture).get("get_frame")
        self._wrapper = self.get_frame

    def install(self):
        """Wrap only this capture instance for the navigation sampling scope."""
        self.capture.get_frame = self._wrapper

    def close(self):
        """Restore the original method after the sampler has been joined."""
        if self.capture.get_frame is self._wrapper:
            if self._had_override:
                self.capture.get_frame = self._override
            else:
                del self.capture.get_frame

    def get_frame(self, *args, **kwargs):
        """Wait out an active probe before starting the normal capture request."""
        with self._condition:
            self._foreground_requests += 1
        try:
            with self._condition:
                self._condition.wait_for(lambda: not self._probe_active)
            return self._original(*args, **kwargs)
        finally:
            with self._condition:
                self._foreground_requests -= 1

    def get_frame_for_probe(self, timeout):
        """Skip sampling while any foreground request is waiting or capturing."""
        with self._condition:
            if self._foreground_requests or self._probe_active:
                raise CaptureBusyException("foreground capture has priority")
            self._probe_active = True
        try:
            return self.capture.get_frame_for_probe(timeout)
        finally:
            with self._condition:
                self._probe_active = False
                self._condition.notify_all()
