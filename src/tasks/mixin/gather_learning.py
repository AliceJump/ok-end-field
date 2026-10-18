"""Private, single-transfer identities for pre-battle gather initial alignment."""

from dataclasses import dataclass, replace

from src.core.learning.models import CaptureProfile, ContextConfidence, WorkflowContext, yaw_bucket
from src.core.learning.policy import learning_allowed, learning_epoch
from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING, stages_dict


@dataclass(frozen=True)
class GatherEntry:
    """Runtime evidence for this transfer only; never persisted as a reusable token."""

    stage: str
    epoch: int
    width: int
    height: int
    scroll: bool


def gather_entry_after_transfer(task, transferred):
    """Confirm the index -> selected gather stage -> fast-transfer entry."""
    if not learning_allowed() or transferred is not True:
        return None
    stage = task.battle_ctx.stage_name
    if stage not in stages_dict[STAGE_CATEGORY_ENERGY_POOLING]:
        return None
    # The caller has already completed ensure_main(). Stability is evidence about
    # the loaded entry, not a requirement that the gather target be detectable.
    epoch = learning_epoch()
    if not task.wait_ui_stable(stable_time=0.3, max_wait=2, refresh_interval=0.1):
        task.log_info(f"[gather-learning] stage={stage} entry=after_transfer unstable; visual only")
        return None
    if not learning_allowed() or epoch != learning_epoch():
        return None
    entry = GatherEntry(stage, learning_epoch(), task.width, task.height, bool(task.zip_line_scroll_enabled()))
    task.log_info(f"[gather-learning] stage={stage} entry=after_transfer confirmed=True")
    return entry


def gather_initial_context(task, entry, *, distances=()):
    """Keep unknown physical families private, separated by stage, entry and pose."""
    if not learning_allowed() or not isinstance(entry, GatherEntry):
        return None
    if (
        entry.epoch != learning_epoch()
        or entry.stage != task.battle_ctx.stage_name
        or (entry.width, entry.height) != (task.width, task.height)
        or entry.scroll != bool(task.zip_line_scroll_enabled())
    ):
        return None
    route = tuple(distances)
    on_board = bool(route)
    phase = "after_zipline" if on_board else "after_transfer"
    profile = CaptureProfile(
        task.width,
        task.height,
        f"gather-on-board:zip-scroll-{entry.scroll}" if on_board else "gather-transfer:zoom-unmodified",
        "hud-initial-v1",
        "xy" if on_board else "x",
    )
    # The persisted stage-nearest-transfer identity denotes the index -> selected
    # stage -> fast-transfer chain. Retain its key so verified totals remain usable;
    # neither a named physical transfer point nor target detection is required.
    return WorkflowContext(
        "gather",
        phase,
        "gather-stage-private",
        entry.stage,
        "after_zipline_on_board" if on_board else "after_transfer",
        f"stage-nearest-transfer:{entry.stage}",
        f"gather:{entry.stage}",
        profile,
        route,
    )


def gather_transfer_alignment(task, entry):
    """No normalization on foot: character yaw is not a complete camera pose."""
    context = gather_initial_context(task, entry)
    if context is None:
        return None, ContextConfidence.INVALID
    reading = task._read_zip_line_settled_arrow_angle()
    if not learning_allowed() or entry.epoch != learning_epoch():
        return None, ContextConfidence.INVALID
    if reading is not None and reading[1] >= task._ZIP_LINE_ARROW_MIN_SCORE:
        context = replace(context, start_yaw_bucket=yaw_bucket(reading[0]))
    # A completed index/stage/fast-transfer chain establishes the stable entry.
    # Reliability still comes from verified sample count/variance, not a point name.
    return context, ContextConfidence.HIGH
