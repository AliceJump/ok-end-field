"""Temporarily bind an ability event's recipient in a persistent callback scope."""

from contextlib import contextmanager


@contextmanager
def event_targets(world, action_id, target):
    context = world._action_targets.setdefault(action_id, {})
    previous = {key: context.get(key) for key in ("current", "trigger")}
    if target is not None:
        context.update(current=(target,), trigger=(target,))
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                context.pop(key, None)
            else:
                context[key] = value
