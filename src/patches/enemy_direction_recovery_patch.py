"""Recover combat camera direction from Endfield's red off-screen enemy marker."""

from __future__ import annotations

import math
import time

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_diagnostics import draw_enemy_direction_debug
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast

_PATCH_INSTALLED = False
_PROBE_INTERVAL = 0.04
_MOUSE_STEP_1080 = 48
_MIN_HORIZONTAL_COMPONENT = 0.05


def _task_time(task) -> float:
    clock = getattr(task, "active_time", None)
    if callable(clock):
        return float(clock())
    return time.monotonic()


def _reset_direction_state(task, *, keep_throttle: bool = False) -> None:
    task._enemy_direction_last_angle = None
    task._enemy_direction_last_seen_at = None
    task._enemy_direction_streak = 0
    task._enemy_direction_turn_dx_sign = None
    task._enemy_direction_recovering = False
    if not keep_throttle:
        task._enemy_direction_next_probe_at = 0.0


def _scaled_mouse_step(task) -> int:
    scale = getattr(task, "scale_distance", None)
    if callable(scale):
        return max(1, int(scale(_MOUSE_STEP_1080)))
    height = int(getattr(task, "height", 1080) or 1080)
    return max(1, int(round(_MOUSE_STEP_1080 * height / 1080.0)))


def _initial_turn_dx_sign(angle_deg: float) -> int:
    """Return the calibrated horizontal turn sign for the marker's screen side."""
    horizontal = math.cos(math.radians(angle_deg))
    if horizontal > _MIN_HORIZONTAL_COMPONENT:
        # Marker is on screen-right. Endfield uses negative dx to turn right.
        return -1
    if horizontal < -_MIN_HORIZONTAL_COMPONENT:
        # Marker is on screen-left. Endfield uses positive dx to turn left.
        return 1
    return 0


def _note_marker_seen(task, observation: EnemyDirectionObservation, now: float) -> int:
    task._enemy_direction_last_angle = observation.angle_deg
    task._enemy_direction_last_seen_at = now
    task._enemy_direction_streak = int(getattr(task, "_enemy_direction_streak", 0)) + 1
    task._enemy_direction_recovering = True
    return task._enemy_direction_streak


def recover_enemy_direction_if_needed(task, presence: EnemyPresence) -> bool:
    """Rotate horizontally until the off-screen marker is gone or HP is visible.

    A normal-enemy HP hit already provides an on-screen target, so the direction
    probe stays idle and any latched turn direction is cleared. Boss HP is
    intentionally different: its fixed top-center bar proves the boss exists but
    says nothing about camera direction, therefore boss PRESENT observations
    still allow this recovery probe.

    The first marker with an unambiguous horizontal side chooses left or right.
    That turn side is then latched: later marker angle/score changes cannot reverse
    the camera. The latch is released only when the marker disappears, normal
    enemy HP becomes visible, or the recovery path errors. This prevents multiple
    nearby/overlapping marker primitives from making the camera oscillate.

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
    dx_sign = getattr(task, "_enemy_direction_turn_dx_sign", None)
    if dx_sign not in (-1, 1):
        dx_sign = _initial_turn_dx_sign(observation.angle_deg)
        if dx_sign == 0:
            draw_enemy_direction_debug(
                task,
                frame,
                observation,
                presence,
                streak=streak,
                action="tracking_no_horizontal_side",
            )
            return True
        task._enemy_direction_turn_dx_sign = dx_sign

    dx = int(dx_sign) * _scaled_mouse_step(task)
    dy = 0
    move = getattr(task, "active_and_send_mouse_delta", None)
    if dx > 0:
        action = "turn_left" if callable(move) else "turn_left_no_mouse_sender"
    else:
        action = "turn_right" if callable(move) else "turn_right_no_mouse_sender"
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
