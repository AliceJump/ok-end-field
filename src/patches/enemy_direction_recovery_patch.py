"""Recover combat camera direction from Endfield's red off-screen enemy marker."""

from __future__ import annotations

import math
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
_MOUSE_STEP_1080 = 48


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


def _scaled_mouse_step(task) -> int:
    scale = getattr(task, "scale_distance", None)
    if callable(scale):
        return max(1, int(scale(_MOUSE_STEP_1080)))
    height = int(getattr(task, "height", 1080) or 1080)
    return max(1, int(round(_MOUSE_STEP_1080 * height / 1080.0)))


def _select_locked_observation(
    task,
    observation: EnemyDirectionObservation,
    now: float,
) -> EnemyDirectionObservation:
    """Keep the current marker unless it disappears or a rival is clearly stronger."""
    markers = observation.markers
    previous_angle = getattr(task, "_enemy_direction_last_angle", None)
    previous_at = getattr(task, "_enemy_direction_last_seen_at", None)
    if (
        not markers
        or previous_angle is None
        or previous_at is None
        or not 0.0 <= now - previous_at <= _STABLE_MAX_GAP
    ):
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
    """Rotate one small step toward a stable off-screen enemy marker.

    A normal-enemy HP hit already provides a useful on-screen location, so the
    direction probe stays completely idle in that case. Boss HP is intentionally
    different: its fixed top-center bar proves the boss exists but says nothing
    about camera direction, therefore boss PRESENT observations still allow this
    recovery probe.

    When multiple marker primitives are visible, the previously tracked nearby
    marker keeps the lock. A rival marker may take over only after becoming at
    least 25% stronger; if the old direction disappears, the strongest remaining
    marker is selected normally. This avoids frame-to-frame score jitter making
    the camera alternate between adjacent markers.

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

    observation = _select_locked_observation(task, observation, now)
    streak = _note_observation(task, observation, now)
    if streak < _STABLE_FRAMES:
        draw_enemy_direction_debug(
            task,
            frame,
            observation,
            presence,
            streak=streak,
            action="tracking",
        )
        return True

    radians = math.radians(observation.angle_deg)
    step = _scaled_mouse_step(task)
    # Endfield's calibrated mouse yaw sign is inverted horizontally: positive
    # dx turns the camera left, so a marker on screen-right must receive -dx.
    dx = int(round(-math.cos(radians) * step))
    dy = int(round(math.sin(radians) * step))
    if dx == 0 and dy == 0:
        draw_enemy_direction_debug(
            task,
            frame,
            observation,
            presence,
            streak=streak,
            action="stable_no_delta",
        )
        return True

    move = getattr(task, "active_and_send_mouse_delta", None)
    action = "mouse_delta" if callable(move) else "stable_no_mouse_sender"
    draw_enemy_direction_debug(
        task,
        frame,
        observation,
        presence,
        streak=streak,
        action=action,
        mouse_delta=(dx, dy) if callable(move) else None,
    )
    if callable(move):
        move(dx=dx, dy=dy, activate=True, delay=0.0, steps=1)
    return True


def _recover_direction_fail_soft(task, presence: EnemyPresence) -> EnemyPresence:
    """Run optional camera recovery without changing or blocking presence probing."""
    try:
        recover_enemy_direction_if_needed(task, presence)
    except Exception as exc:
        # Camera recovery is auxiliary. Neither a movement/scaling failure nor a
        # broken diagnostic logger may interrupt the established presence path.
        try:
            logger = getattr(task, "log_debug", None)
            if callable(logger):
                logger(f"敌人方向恢复失败，保留原敌人检测结果: {exc}")
        except Exception:
            pass
    return presence


def install_enemy_direction_recovery_patch() -> None:
    """Attach direction recovery to the existing enemy-presence probe hot path."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.mixin.battle_mixin import BattleMixin

    original_probe = BattleMixin.probe_enemy_presence
    if getattr(original_probe, "_enemy_direction_recovery_wrapped", False):
        _PATCH_INSTALLED = True
        return

    def probe_enemy_presence_with_direction_recovery(self):
        presence = original_probe(self)
        return _recover_direction_fail_soft(self, presence)

    probe_enemy_presence_with_direction_recovery._enemy_direction_recovery_wrapped = True
    BattleMixin.probe_enemy_presence = probe_enemy_presence_with_direction_recovery
    _PATCH_INSTALLED = True
