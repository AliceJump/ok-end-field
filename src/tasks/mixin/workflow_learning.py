"""One-use entry evidence for trade, liaison and demo initial alignment."""

from dataclasses import dataclass, replace

from ok import TaskDisabledException

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
    try:
        stable = task.wait_ui_stable(stable_time=0.3, max_wait=2, refresh_interval=0.1)
    except TaskDisabledException:
        raise
    except Exception as error:
        task.log_info(f"[workflow-learning] entry unconfirmed; visual only: {error}")
        return None
    if not stable:
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


def confirm_tracking_context(task, context, entry):
    """Failed learning-only world confirmation must retain the original visual path."""
    if context is None:
        return None
    try:
        # Confirmation is observational only: ensure_main/is_main can click
        # "结束拜访" and leave the friend's boat during a trade.
        world = task.wait_until(task.in_world, time_out=2, raise_if_not_found=False)
        if not world:
            return None
        stable = task.wait_ui_stable(stable_time=0.3, max_wait=2, refresh_interval=0.1)
        if not stable:
            return None
        # Check the current stable frame once: a retrying wait could return
        # after loading ends, without verifying the new frame is stable.
        if not task.in_world():
            return None
    except TaskDisabledException:
        raise
    except Exception as error:
        task.log_info(f"[workflow-learning] tracking entry unconfirmed; visual only: {error}")
        return None
    if entry.epoch != learning_epoch() or not learning_allowed():
        return None
    return context


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


def trade_entry_after_visit(task, *, epoch):
    """Only the caller that clicked Go and confirmed arrival may grant this entry."""
    context = _context(
        task,
        "trade",
        "friend_boat_terminal_initial",
        "帝江号",
        "物资调度终端",
        "new_friend_boat_visit",
        "friend_boat_spawn",
        "market_dispatch_terminal_out",
    )
    return confirmed_workflow_entry(task, epoch=epoch, context=context)


def liaison_entry_after_transfer(task, *, epoch, verify_bridge=False):
    """Record the default bridge transfer, not merely being somewhere on the boat."""
    if verify_bridge:
        try:
            arrived = task.wait_ocr(
                match=task.lang.daily_liaison_mixin.k_27d2b829,
                box=task.box.left,
                time_out=2,
                raise_if_not_found=False,
            )
        except TaskDisabledException:
            raise
        except Exception as error:
            task.log_info(f"[workflow-learning] bridge unconfirmed; visual only: {error}")
            return None
        if not arrived:
            task.log_info("[workflow-learning] bridge unconfirmed; visual only")
            return None
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
    # Each confirmed walk count is a separate stable entry. Statistical sample
    # reliability still gates replay; never borrow the other walk count's motion.
    return confirmed_workflow_entry(
        task,
        epoch=entry.epoch,
        context=context,
        confidence=ContextConfidence.HIGH,
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
