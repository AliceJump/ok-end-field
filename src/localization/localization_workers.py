"""Bounded native work; tracking state and cache publication stay on the caller."""

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor


class _ResourceExecutor:
    """One daemon decoder; stalled filesystem I/O must not hold process exit."""

    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="VisualResource", daemon=True)
        self._thread.start()

    def submit(self, fn, /, *args, **kwargs):
        with self._lock:
            if self._closed:
                raise RuntimeError("resource executor is closed")
            future = Future()
            self._queue.put((future, fn, args, kwargs))
            return future

    def _run(self):
        while (item := self._queue.get()) is not None:
            future, fn, args, kwargs = item
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    future.set_exception(exc)
            del item, future, fn, args, kwargs

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_futures:
                    while True:
                        try:
                            self._queue.get_nowait()[0].cancel()
                        except queue.Empty:
                            break
                self._queue.put(None)
        if wait:
            self._thread.join()


class ResourcePending(Exception):
    """A resource is still being decoded and cannot prove visual uniqueness."""


class LocalizationWorkers:
    def __init__(self, match_workers=2):
        self.resources = _ResourceExecutor()
        self.matches = ThreadPoolExecutor(max_workers=match_workers, thread_name_prefix="VisualMapSearch")
        self.closed = False

    def close(self):
        self.closed = True
        # Running decoders own only their result, never a live cache or pose.
        self.resources.shutdown(wait=False, cancel_futures=True)
        self.matches.shutdown(wait=False, cancel_futures=True)
