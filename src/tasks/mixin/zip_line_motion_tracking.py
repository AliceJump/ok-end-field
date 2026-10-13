"""Observe the raw mouse input of one complete zipline alignment attempt."""

from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class ZipLineMotionTotal:
    """Confirmed net input and whether the complete attempt remains learnable."""

    dx: int = 0
    dy: int = 0
    complete: bool = True


@contextmanager
def track_zip_line_mouse_motion(task):
    """Count accepted input, including yaw, visual/search moves and rollback.

    The override belongs to this task instance and is restored even on failure.
    A sender exception may follow partial input, so that attempt cannot be learned.
    """
    name = "active_and_send_mouse_delta"
    original = getattr(task, name)
    had_instance_override = name in vars(task)
    total = ZipLineMotionTotal()

    def send_and_track(*args, **kwargs):
        try:
            sent = original(*args, **kwargs)
        except Exception:
            total.complete = False
            raise
        only_activate = kwargs.get("only_activate", args[3] if len(args) > 3 else False)
        if sent and not only_activate:
            try:
                total.dx += int(kwargs.get("dx", args[0] if args else 1))
                total.dy += int(kwargs.get("dy", args[1] if len(args) > 1 else 1))
            except (TypeError, ValueError, OverflowError):
                total.complete = False
        return sent

    setattr(task, name, send_and_track)
    try:
        yield total
    finally:
        if had_instance_override:
            setattr(task, name, original)
        else:
            delattr(task, name)
