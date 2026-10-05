import unittest
from types import SimpleNamespace

from ok import TriggerTask

from src.patches.task_config_lock_patch import (
    is_task_config_editable,
    release_finished_trigger_state,
)


class _RegularTask:
    def __init__(self, running: bool):
        self.running = running


class TestTaskConfigLockPatch(unittest.TestCase):
    @staticmethod
    def _trigger_task(*, enabled: bool, running: bool):
        task = object.__new__(TriggerTask)
        task._enabled = enabled
        task.running = running
        return task

    def test_regular_task_locks_only_while_running(self):
        self.assertFalse(is_task_config_editable(_RegularTask(running=True)))
        self.assertTrue(is_task_config_editable(_RegularTask(running=False)))

    def test_enabled_idle_trigger_remains_editable(self):
        task = self._trigger_task(enabled=True, running=False)
        self.assertTrue(is_task_config_editable(task))

    def test_disabled_trigger_stays_locked_until_active_invocation_returns(self):
        task = self._trigger_task(enabled=False, running=True)
        executor = SimpleNamespace(current_task=task)

        self.assertFalse(is_task_config_editable(task))

        finished = release_finished_trigger_state(executor)

        self.assertIs(finished, task)
        self.assertFalse(task.running)
        self.assertIsNone(executor.current_task)
        self.assertTrue(is_task_config_editable(task))

    def test_finished_trigger_cleanup_ignores_non_trigger_current_task(self):
        task = _RegularTask(running=True)
        executor = SimpleNamespace(current_task=task)

        self.assertIsNone(release_finished_trigger_state(executor))
        self.assertIs(executor.current_task, task)
        self.assertTrue(task.running)

    def test_finished_trigger_cleanup_ignores_already_idle_trigger(self):
        task = self._trigger_task(enabled=False, running=False)
        executor = SimpleNamespace(current_task=task)

        self.assertIsNone(release_finished_trigger_state(executor))
        self.assertIs(executor.current_task, task)
        self.assertFalse(task.running)


if __name__ == "__main__":
    unittest.main()
