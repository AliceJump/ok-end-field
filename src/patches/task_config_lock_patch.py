from __future__ import annotations

_PATCH_INSTALLED = False


def is_task_config_editable(task) -> bool:
    """Return whether task configuration may be edited safely.

    Keep the original lock semantics: configuration is locked only while the
    task is actually running. TriggerTask enablement alone is not treated as an
    active invocation.
    """
    return not bool(task and getattr(task, "running", False))


def release_finished_trigger_state(executor):
    """Clear stale TriggerTask state at the boundary between executor iterations.

    This helper is only called from the patched ``TaskExecutor.next_task``.
    Reaching that method means the previous ``task.run()`` invocation has
    already returned, so clearing the known ``running=True`` leak here cannot
    expose configuration while task code is still executing.
    """
    from ok import TriggerTask

    task = getattr(executor, "current_task", None)
    if not isinstance(task, TriggerTask) or not bool(getattr(task, "running", False)):
        return None

    task.running = False
    executor.current_task = None
    return task


def install_task_config_lock_patch():
    """Disable task configuration inputs while the task is actually running."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.core.events import communicate
    from ok.gui.tasks.TaskCard import TaskCard
    from ok.task.TaskExecutor import TaskExecutor

    original_update_buttons = TaskCard.update_buttons
    original_next_task = TaskExecutor.next_task

    def update_buttons(self, task):
        original_update_buttons(self, task)
        editable = is_task_config_editable(self.task)
        for widget in getattr(self, "config_widgets", ()):
            widget.setEnabled(editable)
        reset_config = getattr(self, "reset_config", None)
        if reset_config is not None:
            reset_config.setEnabled(editable)

    def next_task(self):
        finished_trigger = release_finished_trigger_state(self)
        if finished_trigger is not None:
            communicate.task.emit(finished_trigger)
        return original_next_task(self)

    update_buttons.__wrapped__ = original_update_buttons
    next_task.__wrapped__ = original_next_task
    TaskCard.update_buttons = update_buttons
    TaskExecutor.next_task = next_task
    _PATCH_INSTALLED = True
