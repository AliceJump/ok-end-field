"""Bounded native work; tracking state and cache publication stay on the caller."""

import threading
import time
import traceback
from collections import deque
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager

import psutil


def decode_memory_bytes(width, height, output_pixels=0):
    """Budget decoded pixels, codec/conversion scratch and resized output."""
    return 64 * 1024**2 + int(width) * int(height) * 12 + int(output_pixels) * 8


class DecodeBudget:
    """Admission shared across resource pools, including retiring sources."""

    def __init__(self, available=None, *, reserve_bytes=256 * 1024**2):
        self._available = available or (lambda: psutil.virtual_memory().available)
        self._reserve_bytes = reserve_bytes
        self._lock = threading.Lock()
        self.active = self.reserved_bytes = 0

    def acquire(self, size):
        with self._lock:
            try:
                available = max(0, int(self._available()) - self._reserve_bytes)
            except (OSError, ValueError, TypeError, psutil.Error):
                available = 0
            # At least one decoder can progress under pressure. Never add a
            # concurrent decoder unless its estimated peak also fits. Counting
            # running reservations against current free RAM is conservative.
            if self.active >= 4 or (self.active and self.reserved_bytes + size > available):
                return False
            self.active += 1
            self.reserved_bytes += size
            return True

    def release(self, size):
        with self._lock:
            self.active -= 1
            self.reserved_bytes -= size


_DECODE_BUDGET = DecodeBudget()
_RESOURCE_RESERVATION = threading.local()


@contextmanager
def decode_reservation(size, *, budget=_DECODE_BUDGET, check_open=lambda: None):
    """Reuse a worker's peak reservation instead of waiting on itself."""
    held = getattr(_RESOURCE_RESERVATION, "held", None)
    if held is not None and held[0] is budget:
        if size > held[1]:
            raise ValueError("decode exceeds resource worker reservation")
        check_open()
        yield
        return
    while not budget.acquire(size):
        check_open()
        time.sleep(0.05)
    try:
        check_open()
        yield
    finally:
        budget.release(size)


def _clear_exception_frames(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        pending.extend((current.__cause__, current.__context__))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def ordered_results(futures, stop=None):
    """Drain failures before reuse; closing can abandon independent resources."""
    results, failure = [], None
    try:
        for future in futures:
            if stop is not None:
                while not future.done():
                    if stop.is_set():
                        raise CancelledError()
                    wait((future,), timeout=0.05)
                if stop.is_set():
                    raise CancelledError()
            try:
                results.append(future.result())
            except Exception as exc:
                failure = failure or exc
        if failure is not None:
            raise failure
        return results
    finally:
        for future in futures:
            future.cancel()


class _ResourceExecutor:
    """Bounded daemon workers with memory admission before native decoding."""

    def __init__(self, max_workers=4, *, budget=_DECODE_BUDGET, name="VisualResource"):
        self._queue = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._discard_results = False
        self._budget = budget
        self._threads = []
        self._max_workers, self._name = max_workers, name

    def submit(self, fn, /, *args, memory_bytes=128 * 1024**2, **kwargs):
        with self._condition:
            if self._closed:
                raise RuntimeError("resource executor is closed")
            future = Future()
            self._queue.append((future, fn, args, kwargs, max(1, int(memory_bytes))))
            if not self._threads:
                for i in range(self._max_workers):
                    thread = threading.Thread(target=self._run, name=f"{self._name}-{i}", daemon=True)
                    self._threads.append(thread)
                    thread.start()
            self._condition.notify_all()
            return future

    def _run(self):
        while True:
            with self._condition:
                while True:
                    while self._queue and self._queue[0][0].cancelled():
                        self._queue.popleft()[0].set_running_or_notify_cancel()
                    if not self._queue:
                        if self._closed:
                            return
                        self._condition.wait()
                    elif self._budget is None or self._budget.acquire(self._queue[0][4]):
                        item = self._queue.popleft()
                        break
                    else:
                        # Another source may release the shared budget, or
                        # available system memory may change without a submit.
                        self._condition.wait(timeout=0.05)
            size = item[4]
            try:
                _RESOURCE_RESERVATION.held = (self._budget, size)
                self._execute(item)
            finally:
                _RESOURCE_RESERVATION.held = None
                del item  # Do not retain images or bound locators while idle.
                if self._budget is not None:
                    self._budget.release(size)
                with self._condition:
                    self._condition.notify_all()

    def _execute(self, item):
        future, fn, args, kwargs, _ = item
        try:
            if future.set_running_or_notify_cancel():
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    # Keep traceback locations, not decoded arrays in exited
                    # frames. Failed futures can outlive resource backoff.
                    _clear_exception_frames(exc)
                    future.set_exception(CancelledError() if self._discard_results else exc)
                else:
                    if self._discard_results:
                        future.set_exception(CancelledError())
                    else:
                        future.set_result(result)
        finally:
            # This executing frame cannot be cleared by clear_frames above.
            # Break its future -> traceback -> frame -> future reference cycle.
            del future, fn, args, kwargs, item

    def shutdown(self, wait=True, *, cancel_futures=False):
        pending = []
        with self._condition:
            self._closed = True
            if cancel_futures:
                self._discard_results = True
                pending = [item[0] for item in self._queue]
                self._queue.clear()
            self._condition.notify_all()
        for future in pending:
            if future.cancel():
                future.set_running_or_notify_cancel()
        if wait:
            for thread in self._threads:
                thread.join()


class ResourcePending(Exception):
    """A resource is still being decoded and cannot prove visual uniqueness."""


class LocalizationWorkers:
    def __init__(self, match_workers=2):
        self.resources = _ResourceExecutor()
        # Coordination must not occupy a decoder while awaiting its children.
        self.indexes = _ResourceExecutor(1, budget=None, name="VisualIndex")
        self.rankings = _ResourceExecutor(4, budget=None, name="VisualFloorRank")
        self.acquisitions = _ResourceExecutor(1, budget=None, name="VisualAcquire")
        self.matches = ThreadPoolExecutor(max_workers=match_workers, thread_name_prefix="VisualMapSearch")
        self.stop = threading.Event()
        self.closed = False

    def close(self):
        self.closed = True
        self.stop.set()
        # Running decoders own only their result, never a live cache or pose.
        self.resources.shutdown(wait=False, cancel_futures=True)
        self.indexes.shutdown(wait=False, cancel_futures=True)
        self.rankings.shutdown(wait=False, cancel_futures=True)
        self.acquisitions.shutdown(wait=False, cancel_futures=True)
        self.matches.shutdown(wait=False, cancel_futures=True)
