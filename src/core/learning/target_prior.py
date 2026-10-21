"""Geometry hints describe a real detection; they never prove current visibility."""

from dataclasses import replace


def roi_around(position, *, padding=0.12):
    if position is None:
        return None
    x, y = position
    return max(0, x - padding), max(0, y - padding), min(1, x + padding), min(1, y + padding)


def direction_towards(position, axes):
    if position is None:
        return None
    dx = position[0] - 0.5 if "x" in axes else 0
    dy = position[1] - 0.5 if "y" in axes else 0
    if max(abs(dx), abs(dy)) <= 0.03:
        return None
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def merge_target_prior(previous, current):
    """A replayed sample has no original-pose detection; retain earlier real geometry."""
    if current is None:
        return previous
    if previous is None or current.initial_position is not None:
        return current
    return replace(
        current,
        initial_position=previous.initial_position,
        roi=previous.roi,
        search_direction=previous.search_direction,
    )
