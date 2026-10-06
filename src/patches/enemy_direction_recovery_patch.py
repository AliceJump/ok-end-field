"""Recover combat movement from Endfield's red off-screen enemy marker."""

from __future__ import annotations

import time

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_diagnostics import draw_enemy_direction_debug
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast

_PATCH_INSTALLED = False
_PROBE_INTERVAL = 0.04
_STABLE_FRAMES = 2
_STABLE_MAX_GAP = 0.18
_STABLE_ANGLE_TOLERANCE_DEG = 12.0
_TARGET_LOCK_ANGLE_TOLERANCE_DEG = 12.0
_TARGET_SWITCH_SCORE_RATIO = 1.25
_DODGE_INTERVAL = 1.0
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


def _angle_delta(current: float, previous: float) -> float:
    return (current - previous + 180.0) % 360.0 - 180.0


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


def _select_locked_observation(
    task,
    observation: EnemyDirectionObservation,
    now: float,
) -> EnemyDirectionObservation:
    """Keep the current marker unless it disappears or a rival is clearly stronger."""
    markers = observation.markers
    previous_angle = getattr(task, "_enemy_direction_last_angle", None)
    previous_at = getattr(task, "_enemy_direction_last_seen_at", None)
    if not markers or previous_angle is None or previous_at is None or not 0.0 <= now - previous_at <= _STABLE_MAX_GAP:
        return observation

    nearest = min(
        markers,
        key=lambda marker: abs(_angle_delta(marker.angle_deg, float(previous_angle))),
    )
    nearest_delta = abs(_angle_delta(nearest.angle_deg, float(previous_angle)))
    best = max(markers, key=lambda marker: marker.score)

    if nearest_delta > _TARGET_LOCK_ANGLE_TOLERANCE_DEG:
        selected = best
    elif best is nearest or best.score < nearest.score * _TARGET_SWITCH_SCORE_RATIO:
        selected = nearest
    else:
        selected = best

    if selected.angle_deg == observation.angle_deg and selected.score == observation.score:
        return observation
    return EnemyDirectionObservation(
        angle_deg=selected.angle_deg,
        score=selected.score,
        markers=markers,
    )


def _centering_blocked(task, now: float | None = None) -> bool:
    if now is None:
        now = _task_time(task)
    return float(now) < float(getattr(task, "_enemy_direction_no_center_until", 0.0))


def _guard_centering_after_dodge(task, now: float) -> None:
    current = float(getattr(task, "_enemy_direction_no_center_until", 0.0))
    task._enemy_direction_no_center_until = max(current, now + _CENTERING_GUARD_SECONDS)


def _note_observation(task, observation: EnemyDirectionObservation, now: float) -> int:
    previous_angle = getattr(task, "_enemy_direction_last_angle", None)
    previous_at = getattr(task, "_enemy_direction_last_seen_at", None)
    previous_streak = int(getattr(task, "_enemy_direction_streak", 0))
    stable = (
        previous_angle is not None
        and previous_at is not None
        and 0.0 <= now - previous_at <= _STABLE_MAX_GAP
        and abs(_angle_delta(observation.angle_deg, float(previous_angle))) <= _STABLE_ANGLE_TOLERANCE_DEG
    )
    streak = previous_streak + 1 if stable else 1
    task._enemy_direction_last_angle = observation.angle_deg
    task._enemy_direction_last_seen_at = now
    task._enemy_direction_streak = streak
    task._enemy_direction_recovering = True
    return streak


def recover_enemy_direction_if_needed(task, presence: EnemyPresence) -> bool:
    """Dodge toward a stable off-screen marker when enemy absence is actionable.

    Any confirmed enemy HP presence, including a boss HP bar, proves that combat
    still has a visible enemy target. In that state the direction probe stays
    completely idle instead of trying to infer an off-screen recovery direction.

    Direction evidence must remain stable for two nearby frames before it can
    trigger movement. UNKNOWN enemy presence is observation-only: marker state
    and hysteresis continue to update, but no dodge is emitted until the HP probe
    resolves to ABSENT. This prevents one incomplete HP scan plus one transient
    red effect from becoming an immediate movement command.

    When multiple marker primitives are visible, the previously tracked nearby
    marker keeps the lock. A rival marker may take over only after becoming at
    least 25% stronger; if the old direction disappears, the strongest remaining
    marker is selected normally.

    The stable marker angle is quantized to the nearest of eight movement
    directions: W/A/S/D and the four diagonals. Recovery never rotates the camera
    itself. Enemy-directed recovery dodges are rate-limited to at most one per
    second. Each dodge briefly suppresses middle-button centering while the dodge
    is in progress and for a short grace period afterward; normal centering
    frequency is otherwise unchanged.

    Every actual direction-probe attempt also emits optional diagnostics. The
    existing ``保存敌人检测调试帧`` option saves an annotated PNG plus matching
    ``.inform.json`` under ``enemy_direction/``; live debug overlay shows the
    fitted ellipse annulus and detected marker primitives.

    Returns True while a direction marker is currently being tracked.
    """
    if presence == EnemyPresence.PRESENT:
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

    observation = _select_locked_observation(task, observation, now)
    streak = _note_observation(task, observation, now)
    direction_key = _dodge_direction_from_angle(observation.angle_deg)

    if streak < _STABLE_FRAMES or presence == EnemyPresence.UNKNOWN:
        draw_enemy_direction_debug(
            task,
            frame,
            observation,
            presence,
            streak=streak,
            action="tracking",
        )
        return True

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