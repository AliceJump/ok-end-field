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
        self.joined = False
        self.__class__.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.joined = True


class _PausedDuringDelayTask(TopmostMixin):
    _TOPMOST_START_DELAY = 0

    def run(self):
        self.pause_topmost_monitor()


class _BaseNestedRunTask(TopmostMixin):
    def run(self):
        return "done"


class _NestedRunTask(_BaseNestedRunTask):
    def run(self):
        return super().run()


class _ResumeDuringRunTask(TopmostMixin):
    def run(self):
        self.resume_topmost_monitor()


class TestTopmostMixin(unittest.TestCase):
    def setUp(self):
        _DeferredTimer.instances.clear()

    def assert_state_lock_held(self, task):
        acquired = []

        def probe_lock():
            lock_acquired = task._topmost_state_lock.acquire(blocking=False)
            acquired.append(lock_acquired)
            if lock_acquired:
                task._topmost_state_lock.release()

        probe = threading.Thread(target=probe_lock)
        probe.start()
        probe.join()
        self.assertEqual([False], acquired)

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

    def test_nested_run_uses_one_owner_timer_and_cleanup(self):
        task = _NestedRunTask()
        task._init_topmost_mixin()

        with (
            patch.object(threading, "Timer", _DeferredTimer),
            patch.object(task, "stop_topmost_monitor") as stop_monitor,
        ):
            self.assertEqual("done", task.run())

        self.assertEqual(1, len(_DeferredTimer.instances))
        self.assertTrue(_DeferredTimer.instances[0].cancelled)
        self.assertTrue(_DeferredTimer.instances[0].joined)
        stop_monitor.assert_called_once_with()
        self.assertEqual(0, task._topmost_run_depth)

    def test_owner_stops_monitor_started_by_resume(self):
        task = _ResumeDuringRunTask()
        task._init_topmost_mixin()
        task._topmost_paused = True

        with (
            patch.object(threading, "Timer", _DeferredTimer),
            patch.object(task, "_reapply_modified") as reapply_modified,
            patch.object(task, "start_topmost_monitor") as start_monitor,
            patch.object(task, "stop_topmost_monitor") as stop_monitor,
        ):
            task.run()

        reapply_modified.assert_called_once_with()
        start_monitor.assert_called_once_with()
        stop_monitor.assert_called_once_with()

    def test_resume_clears_paused_state_before_starting_monitor(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task._topmost_paused = True

        with (
            patch.object(task, "_reapply_modified") as reapply_modified,
            patch.object(task, "start_topmost_monitor") as start_monitor,
        ):
            reapply_modified.side_effect = lambda: self.assert_state_lock_held(task)

            def assert_start_state():
                self.assertFalse(task._topmost_paused)
                self.assert_state_lock_held(task)

            start_monitor.side_effect = assert_start_state
            task.resume_topmost_monitor()

        reapply_modified.assert_called_once_with()
        start_monitor.assert_called_once_with()

    def test_stop_serializes_thread_shutdown_and_window_restore(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        monitor_thread = MagicMock()
        monitor_thread.is_alive.return_value = True
        task._topmost_thread = monitor_thread
        operations = []

        def assert_join_state():
            self.assertTrue(task._topmost_stop_event.is_set())
            self.assert_state_lock_held(task)
            operations.append("join")

        def assert_restore_state():
            self.assert_state_lock_held(task)
            operations.append("restore")

        monitor_thread.join.side_effect = assert_join_state
        with patch.object(task, "_restore_all_modified", side_effect=assert_restore_state) as restore:
            task.stop_topmost_monitor()

        monitor_thread.join.assert_called_once_with()
        restore.assert_called_once_with()
        self.assertEqual(["join", "restore"], operations)
        self.assertIsNone(task._topmost_thread)

    def test_pause_serializes_thread_shutdown_and_window_restore(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        monitor_thread = MagicMock()
        monitor_thread.is_alive.return_value = True
        task._topmost_thread = monitor_thread
        operations = []

        def assert_join_state():
            self.assertTrue(task._topmost_paused)
            self.assertTrue(task._topmost_stop_event.is_set())
            self.assert_state_lock_held(task)
            operations.append("join")

        def assert_restore_state(*, keep_records):
            self.assertTrue(keep_records)
            self.assert_state_lock_held(task)
            operations.append("restore")

        monitor_thread.join.side_effect = assert_join_state
        with patch.object(task, "_restore_all_modified", side_effect=assert_restore_state) as restore:
            task.pause_topmost_monitor()

        monitor_thread.join.assert_called_once_with()
        restore.assert_called_once_with(keep_records=True)
        self.assertEqual(["join", "restore"], operations)
        self.assertIsNone(task._topmost_thread)

    def test_start_does_nothing_while_paused(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task._topmost_paused = True

        with patch.object(threading, "Thread") as monitor_thread:
            task.start_topmost_monitor()

        monitor_thread.assert_not_called()

    def test_is_executor_paused_returns_false_when_no_executor(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task.executor = None

        self.assertFalse(task._is_executor_paused())

    def test_is_executor_paused_returns_executor_state(self):
        task = TopmostMixin.__new__(TopmostMixin)
        task._init_topmost_mixin()
        task.executor = MagicMock()
        task.executor.paused = True

        self.assertTrue(task._is_executor_paused())

        task.executor.paused = False
        self.assertFalse(task._is_executor_paused())
