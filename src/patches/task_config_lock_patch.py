from __future__ import annotations

_PATCH_INSTALLED = False


def install_task_config_lock_patch():
    """Disable a task card's configuration inputs while it is running."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.gui.tasks.TaskCard import TaskCard

    original_update_buttons = TaskCard.update_buttons

    def update_buttons(self, task):
        original_update_buttons(self, task)
        editable = not bool(getattr(self.task, "running", False))
        for widget in getattr(self, "config_widgets", ()):
            widget.setEnabled(editable)
        reset_config = getattr(self, "reset_config", None)
        if reset_config is not None:
            reset_config.setEnabled(editable)

    TaskCard.update_buttons = update_buttons
    _PATCH_INSTALLED = True
