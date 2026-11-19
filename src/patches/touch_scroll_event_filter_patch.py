"""Keep unrelated Qt events out of the framework's touch-click guard."""

from functools import wraps


def install_touch_scroll_event_filter_patch():
    """Preserve the original click handling without inspecting every UI event."""
    from ok.ui.qt.util.touch_scroll import _TouchScrollClickGuard
    from PySide6.QtCore import QEvent

    original_filter = _TouchScrollClickGuard.eventFilter
    if getattr(original_filter, "_okef_mouse_events_only", False):
        return

    mouse_press = QEvent.Type.MouseButtonPress
    mouse_release = QEvent.Type.MouseButtonRelease

    @wraps(original_filter)
    def event_filter(self, obj, event):
        event_type = event.type()
        if event_type != mouse_press and event_type != mouse_release:
            return False
        # Keep the framework's lifetime, drag and synthetic-release handling.
        return original_filter(self, obj, event)

    event_filter._okef_mouse_events_only = True
    _TouchScrollClickGuard.eventFilter = event_filter
