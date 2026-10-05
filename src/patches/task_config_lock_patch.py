from __future__ import annotations

_PATCH_INSTALLED = False


def is_task_config_editable(task) -> bool:
    """Return whether task configuration may be edited safely.

    One-time tasks only need to be locked while their current run is active.
    Trigger tasks are long-lived background workers, so their configuration is
    locked for the whole enabled period and becomes editable again as soon as
    the trigger is disabled.
    """
    if task is None:
        return True

    from ok import TriggerTask

    if isinstance(task, TriggerTask):
        return not bool(getattr(task, "enabled", False))
    return not bool(getattr(task, "running", False))


def install_task_config_lock_patch():
    """Disable task configuration inputs while the task may consume them."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import TriggerTask
    from ok.gui.tasks.TaskCard import TaskCard

    original_update_buttons = TaskCard.update_buttons
    original_trigger_disable = TriggerTask.disable

    def update_buttons(self, task):
        original_update_buttons(self, task)
        editable = is_task_config_editable(self.task)
        for widget in getattr(self, "config_widgets", ()):
            widget.setEnabled(editable)
        reset_config = getattr(self, "reset_config", None)
        if reset_config is not None:
            reset_config.setEnabled(editable)

    def trigger_disable(self):
        # ok-script 2.0.7b1 can leave TriggerTask.running=True when run()
        # returns True because its execute loop continues before clearing the
        # flag. Clear that stale state before BaseTask.disable() emits the task
        # update so every existing editor (including AccountConfigTab) unlocks
        # immediately when the trigger is switched off.
        self.running = False
        original_trigger_disable(self)

    TaskCard.update_buttons = update_buttons
    TriggerTask.disable = trigger_disable
    _PATCH_INSTALLED = True
