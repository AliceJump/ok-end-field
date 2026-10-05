"""Recover combat camera direction from Endfield's red off-screen enemy marker."""

from __future__ import annotations

import math
import time

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_probe import EnemyDirectionObservation, probe_enemy_direction_fast

_PATCH_INSTALLED = False
_PROBE_INTERVAL = 0.04
_STABLE_FRAMES = 2
_STABLE_MAX_GAP = 0.18
_STABLE_ANGLE_TOLERANCE_DEG = 12.0
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

    try:
        observation = probe_enemy_direction_fast(getattr(task, "frame", None))
    except Exception as exc:
        logger = getattr(task, "log_debug", None)
        if callable(logger):
            logger(f"敌人方向提示检测失败，忽略本帧: {exc}")
        _reset_direction_state(task, keep_throttle=True)
        return False

    if observation is None:
        _reset_direction_state(task, keep_throttle=True)
        return False

    streak = _note_observation(task, observation, now)
    if streak < _STABLE_FRAMES:
        return True

    radians = math.radians(observation.angle_deg)
    step = _scaled_mouse_step(task)
    dx = int(round(math.cos(radians) * step))
    dy = int(round(math.sin(radians) * step))
    if dx == 0 and dy == 0:
        return True

    move = getattr(task, "active_and_send_mouse_delta", None)
    if callable(move):
        move(dx=dx, dy=dy, activate=True, delay=0.0, steps=1)
    return True


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
        recover_enemy_direction_if_needed(self, presence)
        return presence

    probe_enemy_presence_with_direction_recovery._enemy_direction_recovery_wrapped = True
    BattleMixin.probe_enemy_presence = probe_enemy_presence_with_direction_recovery
    _PATCH_INSTALLED = True
