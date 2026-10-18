"""One-use entry evidence for trade, liaison and demo initial alignment."""

from dataclasses import dataclass, replace
from hashlib import sha256

from src.core.learning.models import CaptureProfile, ContextConfidence, WorkflowContext
from src.core.learning.policy import learning_allowed, learning_epoch


@dataclass
class WorkflowEntry:
    """Runtime evidence belongs to one task and one alignment, never the cache."""

    owner: object
    epoch: int
    context: WorkflowContext
    confidence: ContextConfidence = ContextConfidence.HIGH
    consumed: bool = False


def confirmed_workflow_entry(task, *, epoch, context, confidence=ContextConfidence.HIGH):
    """Require fresh stable frames after the caller's successful entry transition."""
    if not learning_allowed() or epoch != learning_epoch():
        return None
    if not task.wait_ui_stable(stable_time=0.3, max_wait=2, refresh_interval=0.1):
        return None
    if not learning_allowed() or epoch != learning_epoch():
        return None
    return WorkflowEntry(task, epoch, context, confidence)


def take_workflow_context(task, entry, target_id):
    """Consume evidence even on failure; arbitrary repeat calls cannot reuse it."""
    if not isinstance(entry, WorkflowEntry) or entry.consumed:
        return None, ContextConfidence.INVALID
    entry.consumed = True
    context = entry.context
    if (
        entry.owner is not task
        or entry.epoch != learning_epoch()
        or not learning_allowed()
        or context.target_id != target_id
        or (context.capture_profile.width, context.capture_profile.height) != (task.width, task.height)
    ):
        return None, ContextConfidence.INVALID
    return context, entry.confidence


def _context(task, workflow, phase, area, location, entry, origin, target, *, axes="x"):
    """Keep each semantic entry and its capture/movement profile private."""
    return WorkflowContext(
        workflow,
        phase,
        area,
        location,
        entry,
        origin,
        target,
        CaptureProfile(task.width, task.height, f"{workflow}:{entry}:zoom-unmodified", "hud-initial-v1", axes),
    )


def trade_entry_after_visit(task, *, epoch, area, friend_name):
    """Only the caller that clicked Go and confirmed arrival may grant this entry."""
    if not isinstance(friend_name, str) or not friend_name.strip():
        return None
    # OCR names may collide, but keep different observed friends isolated rather
    # than presuming every friend's layout is identical. Do not persist raw names.
    friend = sha256(friend_name.strip().encode("utf-8")).hexdigest()
    context = _context(
        task,
        "trade",
        "friend_boat_terminal_initial",
        area,
        friend,
        "new_friend_boat_visit",
        "friend_boat_spawn",
        "market_dispatch_terminal_out",
    )
    return confirmed_workflow_entry(task, epoch=epoch, context=context)


def liaison_entry_after_transfer(task, *, epoch):
    """Record the default bridge transfer, not merely being somewhere on the boat."""
    context = _context(
        task,
        "liaison",
        "bridge_after_transfer",
        "帝江号",
        "舰桥",
        "default_bridge_transfer",
        "home_bridge_transfer",
        "operator_liaison_station_out_map",
    )
    return confirmed_workflow_entry(task, epoch=epoch, context=context)


def liaison_entry_after_walk(task, entry, attempts):
    """Separate the actual bridge walk variants; unconfirmed walks grant nothing."""
    context, _ = take_workflow_context(task, entry, "operator_liaison_station_out_map")
    if context is None:
        return None
    context = replace(
        context,
        phase="station_initial",
        location="中央环厅",
        entry="bridge_walk_confirmed",
        entry_route_signature=(1000,) * attempts,
    )
    # Timed walking proves the route but not an exact physical stopping position.
    return confirmed_workflow_entry(
        task,
        epoch=entry.epoch,
        context=context,
        confidence=ContextConfidence.MEDIUM,
    )


def demo_entry_after_transfer(task, *, epoch):
    """The demo overview's selected destination plus completed transfer is the entry."""
    context = _context(
        task,
        "demo",
        "after_transfer_initial",
        "武陵",
        "演武集算",
        "overview_fast_transfer",
        "demo_selected_nearest_transfer",
        "demographic_follow",
        axes="xy",
    )
    return confirmed_workflow_entry(task, epoch=epoch, context=context)
