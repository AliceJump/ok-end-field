"""WGC presentation timestamps shared by runtime positioning and validation.

SystemRelativeTime is QPC in 100 ns units. This instrumentation leaves the
capture dependency untouched and never substitutes arrival time for frame time.
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
import weakref
from dataclasses import asdict, dataclass
from functools import wraps

import numpy as np

_FRAME_TIMINGS = {}
_TIMING_LOCK = threading.RLock()
# Real WGC callbacks on this system can precede the reported frame timestamp
# by a few milliseconds. Retain that timestamp; this is a validity bound, not
# a fitted correction or a substitution of callback time for presentation time.
MAX_FRAME_LEAD_S = 0.020


def _remember_timing(capture, image, timing):
    if timing is None:
        return
    root = _image_root(image)
    key = id(root)

    def released(reference):
        with _TIMING_LOCK:
            previous = _FRAME_TIMINGS.get(key)
            if previous is not None and previous[0] is reference:
                _FRAME_TIMINGS.pop(key, None)

    with _TIMING_LOCK:
        _FRAME_TIMINGS[key] = (weakref.ref(root, released), weakref.ref(capture), timing)
        while len(_FRAME_TIMINGS) > 32:
            _FRAME_TIMINGS.pop(next(iter(_FRAME_TIMINGS)))


def _image_root(image):
    # BaseCaptureMethod.get_frame strips BGRA alpha using a NumPy view.
    # That view can bypass intermediate crop views in its .base chain.
    while isinstance(getattr(image, "base", None), np.ndarray):
        image = image.base
    return image


@dataclass(frozen=True)
class FrameTiming:
    frame_time: float
    system_relative_100ns: int
    callback_time: float
    clock_uncertainty_s: float
    source: str = "wgc_system_relative_time"

    def to_dict(self):
        return asdict(self)


def map_frame_time(ticks: int, qpc_before: int, qpc_after: int, frequency: int, wall_ns: int) -> FrameTiming:
    """Map QPC to UTC using a bracketed clock observation, without a fitted lag."""
    if ticks <= 0 or frequency <= 0 or qpc_after < qpc_before:
        raise ValueError("invalid QPC observation")
    qpc_mid = (qpc_before + qpc_after) / (2 * frequency)
    wall = wall_ns / 1e9
    frame_qpc = ticks / 1e7
    age = qpc_mid - frame_qpc
    if not math.isfinite(age) or not -MAX_FRAME_LEAD_S <= age <= 2:
        raise ValueError(f"frame timestamp outside the capture clock window ({age:.6f}s)")
    return FrameTiming(wall - age, ticks, wall, (qpc_after - qpc_before) / (2 * frequency) + 1e-7)


def observe_frame_time(ticks: int) -> FrameTiming:
    kernel = ctypes.windll.kernel32
    frequency = ctypes.c_longlong()
    before, after = ctypes.c_longlong(), ctypes.c_longlong()
    if not kernel.QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise OSError("QueryPerformanceFrequency failed")
    if not kernel.QueryPerformanceCounter(ctypes.byref(before)):
        raise OSError("QueryPerformanceCounter failed")
    wall = time.time_ns()
    if not kernel.QueryPerformanceCounter(ctypes.byref(after)):
        raise OSError("QueryPerformanceCounter failed")
    return map_frame_time(ticks, before.value, after.value, frequency.value, wall)


def install_wgc_frame_timing(capture_class=None, observer=observe_frame_time):
    """Install frame metadata without modifying capture results.

    _do_get_frame executes under the framework's get_frame_lock. The callback
    runs under its capture lock; keep metadata with the converted frame until
    the corresponding get_frame request returns, including timeout/None paths.
    """
    if capture_class is None:
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

        capture_class = WindowsGraphicsCaptureMethod
    if capture_class.__dict__.get("_localization_timing_installed", False):
        return
    original_convert = capture_class.convert_dx_frame
    original_get = capture_class._do_get_frame

    @wraps(original_convert)
    def convert(self, frame):
        timing = None
        self._localization_timing_error = None
        try:
            ticks = frame.SystemRelativeTime
            timing = observer(int(getattr(ticks, "value", ticks)))
        except (AttributeError, TypeError, ValueError, OSError) as exc:
            self._localization_timing_error = f"{type(exc).__name__}: {exc}"
        image = original_convert(self, frame)
        if image is not None:
            self._localization_pending_timing = timing
        return image

    @wraps(original_get)
    def get_frame(self, *args, **kwargs):
        with self.lock:
            self._localization_pending_timing = None
            self._localization_frame_timing = None
            self._localization_timed_image = None
        image = original_get(self, *args, **kwargs)
        with self.lock:
            if image is not None:
                self._localization_frame_timing = self._localization_pending_timing
                self._localization_timed_image = weakref.ref(_image_root(image))
                _remember_timing(self, image, self._localization_frame_timing)
        return image

    capture_class.convert_dx_frame = convert
    capture_class._do_get_frame = get_frame
    capture_class._localization_timing_installed = True


def timing_for_frame(capture, image) -> FrameTiming | None:
    """Read image-associated metadata, including retained frames after a refresh."""
    if image is None:
        return None
    lock = getattr(capture, "lock", None)
    if lock is None:
        return None
    with lock:
        reference = getattr(capture, "_localization_timed_image", None)
        if reference is not None and reference() is _image_root(image):
            return getattr(capture, "_localization_frame_timing", None)
        with _TIMING_LOCK:
            entry = _FRAME_TIMINGS.get(id(_image_root(image)))
            if entry is not None and entry[0]() is _image_root(image) and entry[1]() is capture:
                return entry[2]
        return None
