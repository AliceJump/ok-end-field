"""Recover combat movement from Endfield's red off-screen enemy marker."""

from __future__ import annotations

import time

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_diagnostics import draw_enemy_direction_debug
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast

_PATCH_INSTALLED = False
_PROBE_INTERVAL = 0.04
_DODGE_INTERVAL = 0.25
_CENTERING_GUARD_SECONDS = 0.20
_DODGE_PRE_HOLD = 0.03
_DODGE_DOWN_TIME = 0.02
_DODGE_AFTER_SLEEP = 0.01
_DODGE_DIRECTIONS = ("d", "sd", "s", "sa", "a", "wa", "w", "wd")


def _task_time(task) -> float:
    clock = getattr(task, "active_time", None)
    if callable(clock):
        return float(clock())
    return time.monotonic()


def _reset_direction_state(task, *, keep_throttle: bool = False) -> None:
    task._enemy_direction_last_angle = None
    task._enemy_direction_last_seen_at = None
    task._enemy_direction_streak = 0
    task._enemy_direction_recovering = False
    if not keep_throttle:
        task._enemy_direction_next_probe_at = 0.0


def _dodge_direction_from_angle(angle_deg: float) -> str:
    """Map a screen-space marker angle to the nearest of eight WASD directions.

    Enemy-direction angles use screen coordinates: 0° is right, 90° is down,
    180° is left, and -90°/270° is up.
    """
    normalized = float(angle_deg) % 360.0
    sector = int((normalized + 22.5) // 45.0) % 8
    return _DODGE_DIRECTIONS[sector]


def _centering_blocked(task, now: float | None = None) -> bool:
    if now is None:
        now = _task_time(task)
    return float(now) < float(getattr(task, "_enemy_direction_no_center_until", 0.0))


def _guard_centering_after_dodge(task, now: float) -> None:
    current = float(getattr(task, "_enemy_direction_no_center_until", 0.0))
    task._enemy_direction_no_center_until = max(current, now + _CENTERING_GUARD_SECONDS)


def _note_marker_seen(task, observation: EnemyDirectionObservation, now: float) -> int:
    task._enemy_direction_last_angle = observation.angle_deg
    task._enemy_direction_last_seen_at = now
    task._enemy_direction_streak = int(getattr(task, "_enemy_direction_streak", 0)) + 1
    task._enemy_direction_recovering = True
    return task._enemy_direction_streak


def recover_enemy_direction_if_needed(task, presence: EnemyPresence) -> bool:
    """Dodge toward the current off-screen marker until that marker disappears.

    A normal-enemy HP hit already provides an on-screen target, so the direction
    probe stays idle. Boss HP is intentionally different: its fixed top-center
    bar proves the boss exists but says nothing about camera direction, therefore
    boss PRESENT observations still allow this recovery probe.

    The marker angle is quantized to the nearest of eight movement directions:
    W/A/S/D and the four diagonals. Recovery never rotates the camera itself.
    Each dodge briefly suppresses middle-button centering while the dodge is in
    progress and for a short grace period afterward; normal centering frequency
    is otherwise unchanged. If the marker remains visible after the dodge
    cooldown, its current angle is sampled again and may choose a different
    movement direction.

    Every actual direction-probe attempt also emits optional diagnostics. The
    existing ``保存敌人检测调试帧`` option saves an annotated PNG plus matching
    ``.inform.json`` under ``enemy_direction/``; live debug overlay shows the
    fitted ellipse annulus and detected marker primitives.

    Returns True while a direction marker is currently being tracked.
    """
    normal_target_visible = presence == EnemyPresence.PRESENT and isinstance(
        getattr(task, "_enemy_hp_last_slice", None), int
    )
    if normal_target_visible:
        _reset_direction_state(task)
        return False

    now = _task_time(task)
    if now < float(getattr(task, "_enemy_direction_next_probe_at", 0.0)):
        return bool(getattr(task, "_enemy_direction_recovering", False))
    task._enemy_direction_next_probe_at = now + _PROBE_INTERVAL

    frame = getattr(task, "frame", None)
    try:
        observation = probe_enemy_direction_fast(frame)
    except Exception as exc:
        draw_enemy_direction_debug(
            task,
            frame,
            None,
            presence,
            streak=0,
            action="error",
            error=str(exc),
        )
        logger = getattr(task, "log_debug", None)
        if callable(logger):
            logger(f"敌人方向提示检测失败，忽略本帧: {exc}")
        _reset_direction_state(task, keep_throttle=True)
        return False

    if observation is None:
        draw_enemy_direction_debug(
            task,
            frame,
            None,
            presence,
            streak=0,
            action="miss",
        )
        _reset_direction_state(task, keep_throttle=True)
        return False

    streak = _note_marker_seen(task, observation, now)
    direction_key = _dodge_direction_from_angle(observation.angle_deg)
    next_dodge_at = float(getattr(task, "_enemy_direction_next_dodge_at", 0.0))
    dodge = getattr(task, "_dodge_with_direction", None)

    if now < next_dodge_at:
        draw_enemy_direction_debug(
            task,
            frame,
            observation,
            presence,
            streak=streak,
            action=f"dodge_cooldown_{direction_key}",
        )
        return True

    if not callable(dodge):
        draw_enemy_direction_debug(
            task,
            frame,
            observation,
            presence,
            streak=streak,
            action=f"dodge_{direction_key}_unavailable",
        )
        return True

    task._enemy_direction_next_dodge_at = now + _DODGE_INTERVAL
    _guard_centering_after_dodge(task, now)
    draw_enemy_direction_debug(
        task,
        frame,
        observation,
        presence,
        streak=streak,
        action=f"dodge_{direction_key}",
    )
    dodge(
        direction_key,
        pre_hold=_DODGE_PRE_HOLD,
        dodge_down_time=_DODGE_DOWN_TIME,
        after_sleep=_DODGE_AFTER_SLEEP,
    )
    return True


def _recover_direction_fail_soft(task, presence: EnemyPresence) -> EnemyPresence:
    """Run optional movement recovery without changing or blocking presence probing."""
    try:
        recover_enemy_direction_if_needed(task, presence)
    except Exception as exc:
        # Direction recovery is auxiliary. Neither an input failure nor a broken
        # diagnostic logger may interrupt the established presence path.
        try:
            logger = getattr(task, "log_debug", None)
            if callable(logger):
                logger(f"敌人方向恢复失败，保留原敌人检测结果: {exc}")
        except Exception:
            pass
    return presence


def install_enemy_direction_recovery_patch() -> None:
    """Attach marker recovery and its short middle-click guard to battle tasks."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.mixin.battle_mixin import BattleMixin

    original_probe = BattleMixin.probe_enemy_presence
    if not getattr(original_probe, "_enemy_direction_recovery_wrapped", False):
        def probe_enemy_presence_with_direction_recovery(self):
            presence = original_probe(self)
            return _recover_direction_fail_soft(self, presence)

        probe_enemy_presence_with_direction_recovery._enemy_direction_recovery_wrapped = True
        BattleMixin.probe_enemy_presence = probe_enemy_presence_with_direction_recovery

    original_click = BattleMixin.click
    if not getattr(original_click, "_enemy_direction_center_guard_wrapped", False):
        def click_with_enemy_direction_center_guard(self, *args, **kwargs):
            key = args[8] if len(args) > 8 else kwargs.get("key", "left")
            if key == "middle" and _centering_blocked(self):
                return False
            return original_click(self, *args, **kwargs)

        click_with_enemy_direction_center_guard._enemy_direction_center_guard_wrapped = True
        BattleMixin.click = click_with_enemy_direction_center_guard

    _PATCH_INSTALLED = True
