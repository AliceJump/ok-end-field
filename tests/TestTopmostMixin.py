import ctypes
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


_TOPMOST_MODULE = "src.core.base_mixin.topmost_mixin"
if _TOPMOST_MODULE in sys.modules:
    from src.core.base_mixin.topmost_mixin import TopmostMixin
else:
    with (
        patch.dict(sys.modules, {"win32gui": MagicMock()}),
        patch.object(ctypes, "windll", SimpleNamespace(user32=MagicMock()), create=True),
    ):
        from src.core.base_mixin.topmost_mixin import TopmostMixin
    sys.modules.pop(_TOPMOST_MODULE, None)


class _DeferredTimer:
    instances: list["_DeferredTimer"] = []

    def __init__(self, _interval, callback):
        self.callback = callback
        self.daemon = False
        self.cancelled = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        pass


class _PausedDuringDelayTask(TopmostMixin):
    _TOPMOST_START_DELAY = 0

    def run(self):
        self.pause_topmost_monitor()


class TestTopmostMixin(unittest.TestCase):
    def setUp(self):
        _DeferredTimer.instances.clear()

    def test_delayed_start_does_not_restart_a_paused_task(self):
        task = _PausedDuringDelayTask()
        task._init_topmost_mixin()

        with (
            patch.object(threading, "Timer", _DeferredTimer),
            patch.object(task, "start_topmost_monitor") as start_monitor,
        ):
            task.run()
            _DeferredTimer.instances[0].callback()

        self.assertTrue(task._topmost_paused)
        start_monitor.assert_not_called()

    def test_resume_clears_paused_state_before_starting_monitor(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task._topmost_paused = True

        with (
            patch.object(task, "_reapply_modified") as reapply_modified,
            patch.object(task, "start_topmost_monitor") as start_monitor,
        ):
            start_monitor.side_effect = lambda: self.assertFalse(task._topmost_paused)
            task.resume_topmost_monitor()

        reapply_modified.assert_called_once_with()
        start_monitor.assert_called_once_with()

    def test_start_does_nothing_while_paused(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task._topmost_paused = True

        with patch.object(threading, "Thread") as monitor_thread:
            task.start_topmost_monitor()

        monitor_thread.assert_not_called()
