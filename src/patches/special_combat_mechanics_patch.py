from __future__ import annotations

import time
from collections.abc import Mapping

from src.data.special_combat_mechanics import SPECIAL_COMBAT_MECHANIC_DETECTOR_PAIRS
from src.image.special_combat_mechanics_probe import SPECIAL_COMBAT_MECHANIC_PROBES

_PATCH_INSTALLED = False
_SHIELD_GUARD_DODGE_INTERVAL = 1.0
_DODGE_PRE_HOLD = 0.03
_DODGE_DOWN_TIME = 0.02
_DODGE_AFTER_SLEEP = 0.01


class SpecialCombatMechanicRuntime:
    """Single-active special-mechanic state machine shared by all combat modes."""

    def __init__(self, task, detector_pairs=None, probes: Mapping[str, object] | None = None, clock=None):
        self.task = task
        self.detector_pairs = tuple(detector_pairs or SPECIAL_COMBAT_MECHANIC_DETECTOR_PAIRS)
        self.probes = dict(SPECIAL_COMBAT_MECHANIC_PROBES if probes is None else probes)
        self._clock = clock or self._task_time
        self.active_name: str | None = None
        self._next_action_at: dict[str, float] = {}
        self._reported_probe_errors: set[tuple[str, str]] = set()

    def _task_time(self) -> float:
        active_time = getattr(self.task, "active_time", None)
        if callable(active_time):
            return float(active_time())
        return time.monotonic()

    def _log_info(self, message: str) -> None:
        logger = getattr(self.task, "log_info", None)
        if callable(logger):
            logger(message)

    def _log_warning(self, message: str) -> None:
        logger = getattr(self.task, "log_warning", None)
        if callable(logger):
            logger(message)
            return
        self._log_info(message)

    def _probe(self, mechanic_name: str, detector_name: str, frame, *, ending: bool) -> bool | None:
        detector = self.probes.get(detector_name)
        if not callable(detector):
            key = (mechanic_name, detector_name)
            if key not in self._reported_probe_errors:
                self._reported_probe_errors.add(key)
                self._log_warning(f"特殊战斗机制 {mechanic_name} 缺少检测器 {detector_name}")
            return None if ending else False
        try:
            return bool(detector(self.task, frame))
        except Exception as exc:
            key = (mechanic_name, detector_name)
            if key not in self._reported_probe_errors:
                self._reported_probe_errors.add(key)
                self._log_warning(f"特殊战斗机制 {mechanic_name} 检测器 {detector_name} 异常: {exc}")
            # Start failures simply do not activate. End failures fail open so a
            # broken detector cannot permanently suppress combat input.
            return None if ending else False

    def observe(self, frame) -> str | None:
        """Advance start/end detection and return the currently active mechanic."""
        if self.active_name is not None:
            for mechanic_name, (_start_detector, end_detector) in self.detector_pairs:
                if mechanic_name != self.active_name:
                    continue
                ended = self._probe(mechanic_name, end_detector, frame, ending=True)
                if ended is None:
                    self._log_warning(f"特殊战斗机制 {mechanic_name} 结束检测不可用，恢复普通战斗")
                    self.active_name = None
                    return None
                if ended:
                    self._log_info(f"特殊战斗机制结束: {mechanic_name}")
                    self.active_name = None
                    return None
                return self.active_name

            # Configuration changed underneath an active runtime. Fail open.
            self._log_warning(f"特殊战斗机制 {self.active_name} 已不在配置中，恢复普通战斗")
            self.active_name = None
            return None

        for mechanic_name, (start_detector, _end_detector) in self.detector_pairs:
            if self._probe(mechanic_name, start_detector, frame, ending=False):
                self.active_name = mechanic_name
                self._log_info(f"特殊战斗机制开始: {mechanic_name}")
                return mechanic_name
        return None

    def perform_active_action(self) -> bool:
        """Run the active mechanic action without changing camera centering."""
        if self.active_name != "shield_guard":
            return False

        now = float(self._clock())
        if now < self._next_action_at.get("shield_guard", 0.0):
            return True

        dodge = getattr(self.task, "_dodge_with_direction", None)
        if not callable(dodge):
            self._log_warning("大盾防御处理缺少方向闪避能力，恢复普通战斗")
            self.active_name = None
            return False

        try:
            dodge(
                "a",
                pre_hold=_DODGE_PRE_HOLD,
                dodge_down_time=_DODGE_DOWN_TIME,
                after_sleep=_DODGE_AFTER_SLEEP,
            )
        except Exception as exc:
            self._log_warning(f"大盾防御左闪避失败，恢复普通战斗: {exc}")
            self.active_name = None
            return False

        self._next_action_at["shield_guard"] = now + _SHIELD_GUARD_DODGE_INTERVAL
        return True

    def reset(self) -> None:
        self.active_name = None
        self._next_action_at.clear()


def _runtime(task) -> SpecialCombatMechanicRuntime | None:
    if not getattr(task, "_special_combat_mechanic_monitoring", False):
        return None
    runtime = getattr(task, "_special_combat_mechanic_runtime", None)
    return runtime if isinstance(runtime, SpecialCombatMechanicRuntime) else None


def _active(task) -> bool:
    runtime = _runtime(task)
    return bool(runtime is not None and runtime.active_name is not None)


def install_special_combat_mechanics_patch() -> None:
    """Install one shared special-mechanic gate around all AutoCombat entry paths."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.tasks.mixin.battle_mixin import BattleMixin
    from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
    from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

    original_next_frame = BattleMixin.next_frame
    original_mouse_down = BattleMixin.mouse_down
    original_mouse_up = BattleMixin.mouse_up
    original_approach_enemy = BattleMixin.approach_enemy

    original_run = AutoCombatLogic.run
    original_normal_frame = AutoCombatLogic._do_normal_combat_frame
    original_conditional_step = AutoCombatLogic._do_conditional_rotation_step
    original_rotation_token = AutoCombatLogic._exec_rotation_token
    original_auto_rotation_step = AutoCombatLogic._do_auto_rotation_step
    original_instant_release = AutoCombatLogic._do_instant_release
    original_timed_step = TimedCombatLogic.step

    def run_with_special_mechanics(self, *args, **kwargs):
        task = self.task
        previous_runtime = getattr(task, "_special_combat_mechanic_runtime", None)
        previous_monitoring = getattr(task, "_special_combat_mechanic_monitoring", False)
        runtime = SpecialCombatMechanicRuntime(task)
        task._special_combat_mechanic_runtime = runtime
        task._special_combat_mechanic_monitoring = True
        try:
            return original_run(self, *args, **kwargs)
        finally:
            runtime.reset()
            task._special_combat_mechanic_monitoring = previous_monitoring
            task._special_combat_mechanic_runtime = previous_runtime

    def next_frame_with_special_mechanics(self, *args, **kwargs):
        frame = original_next_frame(self, *args, **kwargs)
        runtime = _runtime(self)
        if runtime is None:
            return frame

        previous = runtime.active_name
        active = runtime.observe(frame)
        if active is not None:
            if previous is None:
                # Stop the current normal-attack hold before the first side dodge.
                original_mouse_up(self, key="left")
            runtime.perform_active_action()
        return frame

    def mouse_down_with_special_mechanics(self, *args, **kwargs):
        key = args[0] if args else kwargs.get("key", "left")
        if key == "left" and _active(self):
            return False
        return original_mouse_down(self, *args, **kwargs)

    def approach_enemy_with_special_mechanics(self, *args, **kwargs):
        if _active(self):
            return None
        return original_approach_enemy(self, *args, **kwargs)

    def normal_frame_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            return None
        return original_normal_frame(self, *args, **kwargs)

    def conditional_step_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            return "", True
        return original_conditional_step(self, *args, **kwargs)

    def rotation_token_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            # Keep legacy rotation timeout frozen while the enemy mechanic owns input.
            self.last_rotation_ok_time = self.task.active_time()
            return False, ""
        return original_rotation_token(self, *args, **kwargs)

    def auto_rotation_step_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            return "", True
        return original_auto_rotation_step(self, *args, **kwargs)

    def instant_release_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            return None
        return original_instant_release(self, *args, **kwargs)

    def timed_step_with_special_mechanics(self, *args, **kwargs):
        if _active(self.task):
            self._hold(False)
            return None
        return original_timed_step(self, *args, **kwargs)

    BattleMixin.next_frame = next_frame_with_special_mechanics
    BattleMixin.mouse_down = mouse_down_with_special_mechanics
    BattleMixin.approach_enemy = approach_enemy_with_special_mechanics

    AutoCombatLogic.run = run_with_special_mechanics
    AutoCombatLogic._do_normal_combat_frame = normal_frame_with_special_mechanics
    AutoCombatLogic._do_conditional_rotation_step = conditional_step_with_special_mechanics
    AutoCombatLogic._exec_rotation_token = rotation_token_with_special_mechanics
    AutoCombatLogic._do_auto_rotation_step = auto_rotation_step_with_special_mechanics
    AutoCombatLogic._do_instant_release = instant_release_with_special_mechanics
    TimedCombatLogic.step = timed_step_with_special_mechanics

    _PATCH_INSTALLED = True
