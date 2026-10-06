from __future__ import annotations

from functools import wraps

_PATCH_INSTALLED = False


def is_task_config_editable(task) -> bool:
    """Return whether task configuration may be edited safely.

    Keep the original lock semantics: configuration is locked only while the
    task is actually running. TriggerTask enablement alone is not treated as an
    active invocation.
    """
    return not bool(task and getattr(task, "running", False))


def wrap_trigger_run(task, emit_state):
    """Wrap one TriggerTask run so UI state follows the real invocation lifetime."""
    if getattr(task, "_config_lock_run_wrapped", False):
        return

    original_run = task.run

    @wraps(original_run)
    def run_with_config_lock(*args, **kwargs):
        executor = getattr(task, "_executor", None)
        managed_invocation = bool(
            getattr(task, "running", False) and executor is not None and getattr(executor, "current_task", None) is task
        )
        if managed_invocation:
            emit_state(task)
        try:
            return original_run(*args, **kwargs)
        finally:
            if managed_invocation:
                task.running = False
                if getattr(executor, "current_task", None) is task:
                    executor.current_task = None
                emit_state(task)

    task.run = run_with_config_lock
    task._config_lock_run_wrapped = True


def install_task_config_lock_patch():
    """Disable task configuration inputs while the task is actually running."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import TriggerTask
    from ok.core.events import communicate
    from ok.gui.tasks.TaskCard import TaskCard

    original_update_buttons = TaskCard.update_buttons
    original_trigger_after_init = TriggerTask.after_init

    def update_buttons(self, task):
        original_update_buttons(self, task)
        editable = is_task_config_editable(self.task)
        for widget in getattr(self, "config_widgets", ()):
            widget.setEnabled(editable)
        reset_config = getattr(self, "reset_config", None)
        if reset_config is not None:
            reset_config.setEnabled(editable)

    def trigger_after_init(self, *args, **kwargs):
        original_trigger_after_init(self, *args, **kwargs)
        wrap_trigger_run(self, communicate.task.emit)

    update_buttons.__wrapped__ = original_update_buttons
    trigger_after_init.__wrapped__ = original_trigger_after_init
    TaskCard.update_buttons = update_buttons
    TriggerTask.after_init = trigger_after_init
    _PATCH_INSTALLED = True
