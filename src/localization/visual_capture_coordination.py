"""Coordinate foreground screenshots with navigation's opportunistic probes."""

import threading
import time

from ok.task.exceptions import CaptureBusyException, CaptureException


class VisualCaptureCoordinator:
    """Keep probe contention out of the foreground's short capture-lock timeout.

    A probe cannot preempt lifecycle/cropping/compositing already in progress.
    Foreground callers wait up to a deadline for that request to finish, then use
    the unchanged capture API. Waiting callers take priority over new probes.
    This removes the executor's extra retry sleep; a stalled probe fails capture.
    """

    def __init__(self, capture, *, foreground_timeout=1.0):
        self.capture = capture
        self._condition = threading.Condition()
        self._probe_active = False
        self._foreground_requests = 0
        self._cancelled = False
        self._foreground_timeout = foreground_timeout
        self._original = capture.get_frame
        self._had_override = "get_frame" in vars(capture)
        self._override = vars(capture).get("get_frame")
        self._wrapper = self.get_frame

    def install(self):
        """Wrap only this capture instance for the navigation sampling scope."""
        self.capture.get_frame = self._wrapper

    def close(self):
        """Restore the original method once the worker no longer uses capture."""
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
                if self._probe_active:
                    budget = kwargs.get("busy_wait", args[0] if args else 0)
                    started = time.monotonic()
                    ready = self._condition.wait_for(
                        lambda: not self._probe_active, timeout=min(self._foreground_timeout, max(0, budget))
                    )
                    if not ready:
                        if self._cancelled or self._foreground_timeout <= budget:
                            raise CaptureException("visual probe stalled during foreground capture")
                        return None
                    remaining = max(0.0, budget - (time.monotonic() - started))
                    if args:
                        args = (remaining, *args[1:])
                    elif "busy_wait" in kwargs:
                        kwargs["busy_wait"] = remaining
            return self._original(*args, **kwargs)
        finally:
            with self._condition:
                self._foreground_requests -= 1

    def get_frame_for_probe(self, timeout):
        """Skip sampling while any foreground request is waiting or capturing."""
        with self._condition:
            if self._cancelled or self._foreground_requests or self._probe_active:
                raise CaptureBusyException("foreground capture has priority")
            self._probe_active = True
        try:
            return self.capture.get_frame_for_probe(timeout)
        finally:
            with self._condition:
                self._probe_active = False
                self._condition.notify_all()

    def cancel(self):
        """Stop admission and wake foreground waiters without touching backend locks."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()
