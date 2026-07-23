import unittest
from unittest.mock import patch

from ok import BaseTask
from src.core.BaseEfTask import BaseEfTask


class _ExitEvent:
    def is_set(self):
        return False


class _Executor:
    def __init__(self):
        self.paused = False
        self.pause_start = 100.0
        self.exit_event = _ExitEvent()
        self.wait_scene_timeout = 10
        self.wait_until_settle_time = -1

    def reset_scene(self):
        pass


def _make_task():
    task = BaseEfTask.__new__(BaseEfTask)
    task._executor = _Executor()
    task._active_time_paused_total = 0.0
    task._task_pause_started_at = None
    task._seen_executor_pause_start = task.executor.pause_start
    return task


class TestPauseAwareTimeout(unittest.TestCase):
    def test_active_time_excludes_executor_pause(self):
        task = _make_task()

        with patch("src.core.BaseEfTask.time.monotonic", return_value=10.0), \
                patch("src.core.BaseEfTask.time.time", return_value=100.0):
            start = task.active_time()

        task.executor.pause_start = 105.0
        task.executor.paused = True
        with patch("src.core.BaseEfTask.time.monotonic", return_value=30.0), \
                patch("src.core.BaseEfTask.time.time", return_value=125.0):
            self.assertEqual(task.active_time(), start)

        task.executor.paused = False
        with patch("src.core.BaseEfTask.time.monotonic", return_value=30.0), \
                patch("src.core.BaseEfTask.time.time", return_value=125.0):
            self.assertEqual(task.active_time(), start)

        with patch("src.core.BaseEfTask.time.monotonic", return_value=32.0), \
                patch("src.core.BaseEfTask.time.time", return_value=127.0):
            self.assertEqual(task.active_time() - start, 2.0)

    def test_wait_until_uses_active_time_for_timeout(self):
        task = _make_task()
        active_times = iter((0.0, 0.0, 0.5, 1.0, 1.5))
        task.active_time = lambda: next(active_times)
        task.next_frame = lambda: None
        condition_calls = []

        result = task.wait_until(
            lambda: condition_calls.append(True) and False,
            time_out=1,
        )

        self.assertIsNone(result)
        self.assertEqual(len(condition_calls), 4)

    def test_task_pause_and_unpause_freeze_active_time(self):
        task = _make_task()

        with patch.object(BaseTask, "pause"), \
                patch("src.core.BaseEfTask.time.monotonic", return_value=10.0):
            task.pause()

        with patch("src.core.BaseEfTask.time.monotonic", return_value=30.0), \
                patch("src.core.BaseEfTask.time.time", return_value=100.0):
            self.assertEqual(task.active_time(), 10.0)

        with patch.object(BaseTask, "unpause"), \
                patch("src.core.BaseEfTask.time.monotonic", return_value=30.0):
            task.unpause()

        with patch("src.core.BaseEfTask.time.monotonic", return_value=32.0), \
                patch("src.core.BaseEfTask.time.time", return_value=102.0):
            self.assertEqual(task.active_time(), 12.0)

    def test_sleep_uses_active_deadline(self):
        task = _make_task()
        active_times = iter((0.0, 0.05, 0.11))
        task.active_time = lambda: next(active_times)

        with patch.object(BaseTask, "sleep") as framework_sleep:
            self.assertTrue(task.sleep(0.1))

        framework_sleep.assert_called_once_with(0.05)


if __name__ == "__main__":
    unittest.main()
