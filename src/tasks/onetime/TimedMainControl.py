"""State-dependent main-character handoff, independent of skill rotation order."""

from __future__ import annotations

from dataclasses import dataclass

from src.data.character_mechanics import MainControlMode, load_main_control_policies


@dataclass(frozen=True)
class _ControlWindow:
    mode: MainControlMode
    started: float
    until: float


class TimedMainControl:
    _PROBE_INTERVAL = 0.2
    _CONFIRM_TIMEOUT = 0.5
    _RETRY_INTERVAL = 1.0

    def __init__(self, logic):
        self.logic = logic
        self.policies = load_main_control_policies()
        self.reset()

    def reset(self):
        self.windows: dict[str, _ControlWindow] = {}
        self.busy_until: dict[str, float] = {}
        self.pending_slot = None
        self.requested_at = 0.0
        self.next_probe_at = 0.0
        self.retry_at = 0.0

    def record_cast(self, token, kind, profiles, started):
        logic = self.logic
        if not logic._slot_available(token) or not profiles:
            return
        self.busy_until[token] = started + max(profile.actionable for profile in profiles)
        policy = self.policies.get((logic.team[int(token) - 1], kind))
        if policy is None:
            return
        if policy.mode == MainControlMode.AVOID:
            until = max(self.busy_until[token], logic.state_until.get(token, 0.0))
            previous = self.windows.get(token)
            if previous is not None and previous.mode == MainControlMode.AVOID:
                until = max(until, previous.until)
        else:
            effect_at = max((profile.effect_start or 0.0) for profile in profiles)
            until = started + effect_at + policy.seconds
        self.windows[token] = _ControlWindow(policy.mode, started, until)
        self.next_probe_at = 0.0

    def preferred_until(self, token):
        window = self.windows.get(token)
        if window is not None and window.mode == MainControlMode.PREFER:
            return window.until
        return None

    def waiting_for_confirmation(self, now):
        return self.pending_slot is not None and now - self.requested_at < self._CONFIRM_TIMEOUT

    def _target(self, current, now):
        logic = self.logic
        preferred = [
            (window.started, token) for token, window in self.windows.items() if window.mode == MainControlMode.PREFER
        ]
        if preferred:
            return max(preferred, key=lambda item: (item[0], -int(item[1])))[1]
        window = self.windows.get(current)
        if window is None or window.mode != MainControlMode.AVOID:
            return None
        available = [
            str(index + 1)
            for index in range(len(logic.team))
            if logic._slot_available(str(index + 1))
            and str(index + 1) != current
            and str(index + 1) not in self.windows
            and now >= self.busy_until.get(str(index + 1), 0.0)
        ]
        return max(
            available,
            key=lambda token: (
                logic.normal_attack_sp_gains.get(logic.team[int(token) - 1]) or 0.0,
                -int(token),
            ),
            default=None,
        )

    def _log(self, template, **values):
        task = self.logic.task
        translate = getattr(task, "tr", lambda text: text)
        task.log_info(translate(template).format(**values))

    def update(self, force=False):
        """Return True only while a handoff is awaiting a fresh arrow observation."""
        logic = self.logic
        task = logic.task
        now = logic._clock()
        self.windows = {
            token: window
            for token, window in self.windows.items()
            if now < window.until and logic._slot_available(token)
        }
        if not self.windows:
            self.pending_slot = None
            return False
        detector = getattr(task, "detect_current_char_index", None)
        switch = getattr(task, "switch_main_control", None)
        if not callable(detector) or not callable(switch):
            return False
        if not force and now < self.next_probe_at:
            return self.waiting_for_confirmation(now)
        self.next_probe_at = now + self._PROBE_INTERVAL
        index = detector()
        current = str(index + 1) if index is not None and 0 <= index < len(logic.team) else None
        if self.pending_slot is not None:
            if current == self.pending_slot:
                self._log("时间排轴主控: 已确认切换到 {actor}（槽位 {slot}）", actor=logic.team[index], slot=current)
                self.pending_slot = None
                logic._hold(True, force=True)
            elif self.waiting_for_confirmation(now):
                return True
            else:
                self._log("时间排轴主控: 切换尚未确认，等待后续主控箭头")
                self.pending_slot = None
        if current is None or not logic._slot_available(current):
            return False
        target = self._target(current, now)
        if target is None or target == current or now < self.retry_at:
            return False
        if not logic._allowed(candidate_slot=target, candidate_kind="switch"):
            return False
        logic._hold(False)
        self.retry_at = now + self._RETRY_INTERVAL
        if not switch(int(target)):
            return False
        self.pending_slot = target
        self.requested_at = now
        self.next_probe_at = now + 0.05
        self._log("时间排轴主控: 尝试切换到 {actor}（槽位 {slot}）", actor=logic.team[int(target) - 1], slot=target)
        return True
