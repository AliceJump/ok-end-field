from __future__ import annotations

from dataclasses import dataclass, field

_PATCH_INSTALLED = False
_STABILITY_GAP_SECONDS = 2.5
_REQUIRED_MATCHES = 2
_STATE_ATTR = "_timed_team_detection_stability"


@dataclass
class _StableCandidate:
    value: list[str] = field(default_factory=list)
    streak: int = 0
    seen_at: float | None = None


class _TimedTeamStability:
    """Keep team-confirmation streaks across scheduler ticks.

    TimedCombatLogic samples team composition roughly once per second. Requiring
    two expensive template scans to finish inside one 0.2/0.3 second helper call
    makes a correct first observation useless on slower/debug runs because the
    local streak is discarded when the helper returns. Store the candidate on
    the scheduler instance instead so the next tick can confirm it without
    blocking the combat loop.
    """

    def __init__(self):
        self.channels: dict[str, _StableCandidate] = {}

    def observe(self, channel: str, team, now: float):
        current = list(team or [])
        state = self.channels.setdefault(channel, _StableCandidate())

        if not current or all(member == "?" for member in current):
            state.value = []
            state.streak = 0
            state.seen_at = None
            return current or ["?"], False

        expired = state.seen_at is not None and now - state.seen_at > _STABILITY_GAP_SECONDS
        if expired or current != state.value:
            state.value = current
            state.streak = 1
        else:
            state.streak += 1
        state.seen_at = now
        return current, state.streak >= _REQUIRED_MATCHES


def _reset_channel(logic, channel: str) -> None:
    tracker = getattr(logic, _STATE_ATTR, None)
    if tracker is not None:
        tracker.channels.pop(channel, None)


def _observe_once(logic, channel: str, fallback, **kwargs):
    task = logic.task
    detector = getattr(task, "detect_team", None)
    if not callable(detector):
        return fallback(**kwargs)

    frame = getattr(task, "frame", None)
    if frame is None or (hasattr(frame, "size") and frame.size == 0):
        frame = task.next_frame()
    if frame is None or (hasattr(frame, "size") and frame.size == 0):
        _reset_channel(logic, channel)
        return ["?"], False

    team = detector(frame)
    member_count = kwargs.get("member_count")
    if member_count is None:
        member_count = getattr(task, "_battle_member_count", None)
    if member_count is not None and 1 <= member_count <= 4:
        team = team[:member_count]
    tracker = getattr(logic, _STATE_ATTR, None)
    if tracker is None:
        tracker = _TimedTeamStability()
        setattr(logic, _STATE_ATTR, tracker)
    return tracker.observe(channel, team, task.active_time())


def _call_with_incremental_stability(logic, channel: str, callback, *args, **kwargs):
    task = logic.task
    original = task.detect_team_stable

    def detect_team_incrementally(**detect_kwargs):
        return _observe_once(logic, channel, original, **detect_kwargs)

    # Keep the rest of TimedCombatLogic unchanged: only replace the expensive
    # synchronous two-frame detector for the duration of this one scheduler
    # entry point. Other battle modes and HUD-recovery waits retain their
    # original blocking semantics.
    task.detect_team_stable = detect_team_incrementally
    try:
        return callback(logic, *args, **kwargs)
    finally:
        task.detect_team_stable = original


def install_timed_team_detection_patch() -> None:
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

    original_detect_team = TimedCombatLogic._detect_team
    original_refresh_team_slots = TimedCombatLogic._refresh_team_slots

    def detect_team_across_ticks(self, deadline):
        return _call_with_incremental_stability(self, "initial", original_detect_team, deadline)

    def refresh_team_slots_across_ticks(self, deadline):
        return _call_with_incremental_stability(self, "refresh", original_refresh_team_slots, deadline)

    TimedCombatLogic._detect_team = detect_team_across_ticks
    TimedCombatLogic._refresh_team_slots = refresh_team_slots_across_ticks
    _PATCH_INSTALLED = True
