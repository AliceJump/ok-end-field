import unittest
from types import SimpleNamespace

from ok import TriggerTask

from src.patches.task_config_lock_patch import is_task_config_editable, wrap_trigger_run


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

    def test_trigger_run_emits_locked_then_unlocked_state_on_success(self):
        task = self._trigger_task(enabled=True, running=True)
        executor = SimpleNamespace(current_task=task)
        task._executor = executor
        emitted = []

        def original_run():
            self.assertFalse(is_task_config_editable(task))
            return True

        task.run = original_run
        wrap_trigger_run(task, lambda current: emitted.append((current.running, executor.current_task is current)))

        self.assertTrue(task.run())
        self.assertEqual(emitted, [(True, True), (False, False)])
        self.assertFalse(task.running)
        self.assertIsNone(executor.current_task)
        self.assertTrue(is_task_config_editable(task))

    def test_disabled_active_trigger_unlocks_only_after_run_exits(self):
        task = self._trigger_task(enabled=False, running=True)
        executor = SimpleNamespace(current_task=task)
        task._executor = executor
        emitted = []

        class DisabledDuringRun(Exception):
            pass

        def original_run():
            self.assertFalse(is_task_config_editable(task))
            raise DisabledDuringRun()

        task.run = original_run
        wrap_trigger_run(task, lambda current: emitted.append((current.running, executor.current_task is current)))

        with self.assertRaises(DisabledDuringRun):
            task.run()

        self.assertEqual(emitted, [(True, True), (False, False)])
        self.assertFalse(task.running)
        self.assertIsNone(executor.current_task)
        self.assertTrue(is_task_config_editable(task))

    def test_trigger_run_wrapper_does_not_manage_direct_untracked_calls(self):
        task = self._trigger_task(enabled=True, running=False)
        executor = SimpleNamespace(current_task=None)
        task._executor = executor
        emitted = []
        task.run = lambda: "ok"

        wrap_trigger_run(task, emitted.append)

        self.assertEqual(task.run(), "ok")
        self.assertEqual(emitted, [])
        self.assertFalse(task.running)
        self.assertIsNone(executor.current_task)

    def test_trigger_run_wrapper_is_idempotent(self):
        task = self._trigger_task(enabled=True, running=True)
        executor = SimpleNamespace(current_task=task)
        task._executor = executor
        emitted = []
        task.run = lambda: None

        wrap_trigger_run(task, emitted.append)
        wrapped = task.run
        wrap_trigger_run(task, emitted.append)

        self.assertIs(task.run, wrapped)


if __name__ == "__main__":
    unittest.main()
