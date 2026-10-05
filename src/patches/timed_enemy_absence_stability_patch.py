"""Require sustained enemy absence before timed combat pauses skill scheduling."""

from __future__ import annotations

from src.data.combat_observation import EnemyPresence, normalize_enemy_presence

_PATCH_INSTALLED = False
_ABSENT_CONFIRM_SECONDS = 0.5


def _enemy_operation_paused_with_stability(logic) -> bool:
    """Pause only after ABSENT remains continuous for a short real-time window.

    The underlying HP probe already requires two complete clean slice rounds,
    but those rounds can finish in only a few fast scheduler iterations. This
    adds an independent time gate to the *pause decision* without slowing
    positive PRESENT recovery or other consumers of the raw presence probe.
    """
    probe = getattr(logic.task, "probe_enemy_presence", None)
    state = normalize_enemy_presence(probe() if callable(probe) else None)
    now = logic._clock()

    if state == EnemyPresence.PRESENT:
        logic._enemy_absent_candidate_since = None
        if logic.enemy_pause_started is not None:
            elapsed = max(0.0, now - logic.enemy_pause_started)
            logic.enemy_pause_started = None
            logic.task.log_info(
                f"时间排轴敌人占位检测: 敌人重新出现，恢复技能调度；暂停 {elapsed:.2f}s 已计入状态耗时"
            )
        return False

    if logic.enemy_pause_started is not None:
        # Preserve the existing resume contract: once actually paused, UNKNOWN
        # is not enough to resume; a positive PRESENT observation is required.
        return True

    if state != EnemyPresence.ABSENT:
        # UNKNOWN means the continuous absence evidence was interrupted (for
        # example by an invalid frame), so a future ABSENT must start over.
        logic._enemy_absent_candidate_since = None
        return False

    candidate_since = getattr(logic, "_enemy_absent_candidate_since", None)
    if candidate_since is None or now < candidate_since:
        logic._enemy_absent_candidate_since = now
        return False

    if now - candidate_since < _ABSENT_CONFIRM_SECONDS:
        return False

    logic.enemy_pause_started = now
    logic.task.log_info(
        "时间排轴敌人占位检测: 战斗UI仍在且连续未见敌人证据，暂停技能调度；"
        "保持普攻和中键索敌，状态与冷却时间继续流逝"
    )
    return True


def install_timed_enemy_absence_stability_patch() -> None:
    """Attach the sustained-absence gate before combat decision tracing wraps it."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

    current = TimedCombatLogic._enemy_operation_paused
    if getattr(current, "_enemy_absence_stability_wrapped", False):
        _PATCH_INSTALLED = True
        return

    def enemy_operation_paused_with_stability(self):
        return _enemy_operation_paused_with_stability(self)

    enemy_operation_paused_with_stability._enemy_absence_stability_wrapped = True
    TimedCombatLogic._enemy_operation_paused = enemy_operation_paused_with_stability
    _PATCH_INSTALLED = True
