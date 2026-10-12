"""Integrate EndAxis plans with ``TimedCombatLogic`` at runtime.

The fixed-rotation bridge remains available for legacy mode. When timing mode is
active and ``EndAxis排轴`` is configured, this patch instead keeps EndAxis
absolute timestamps as a plan clock and lets the existing timing scheduler
validate SP, cooldowns, handoff windows, deaths, enemy presence and action
feedback before an action is committed.
"""

from __future__ import annotations

from src.data.endaxis_timeline import (
    EndAxisAction,
    EndAxisScenario,
    EndAxisTimelineError,
    parse_endaxis_rotation_spec,
    parse_endaxis_scenarios,
    select_endaxis_scenario,
)

KEY_TIMING_ENDAXIS = "EndAxis排轴"
_PATCH_INSTALLED = False
_CONFIG_INSTALLED = False
_EPSILON = 1e-6


def _scenario_from_config(raw) -> EndAxisScenario | None:
    """Parse a dedicated timed-mode field as raw share code or prefixed spec."""
    value = str(raw or "").strip()
    if not value:
        return None
    parsed = parse_endaxis_rotation_spec(value)
    if parsed is None:
        selector, code = None, value
    else:
        selector, code = parsed
    return select_endaxis_scenario(parse_endaxis_scenarios(code), selector)


def _plan_active(logic) -> bool:
    """Whether an imported EndAxis scenario still owns the plan lane."""
    scenario = getattr(logic, "_endaxis_scenario", None)
    cursor = int(getattr(logic, "_endaxis_cursor", 0))
    return scenario is not None and cursor < len(scenario.actions)


def _next_action(logic) -> EndAxisAction | None:
    """Return the current EndAxis action without changing the cursor."""
    if not _plan_active(logic):
        return None
    return logic._endaxis_scenario.actions[logic._endaxis_cursor]


def _plan_elapsed(logic, now=None) -> float:
    """Return EndAxis plan time, excluding periods frozen before an action starts."""
    if getattr(logic, "_endaxis_frozen", False):
        return float(getattr(logic, "_endaxis_frozen_elapsed", 0.0))
    now = logic._clock() if now is None else float(now)
    anchor_time = float(getattr(logic, "_endaxis_clock_wall", now))
    anchor_elapsed = float(getattr(logic, "_endaxis_clock_elapsed", 0.0))
    return max(0.0, anchor_elapsed + max(0.0, now - anchor_time))


def _freeze_at_action(logic, action: EndAxisAction, now=None) -> None:
    """Roll the plan clock back to a due action after a failed/unstarted attempt."""
    now = logic._clock() if now is None else float(now)
    logic._endaxis_frozen = True
    logic._endaxis_frozen_elapsed = action.start_seconds
    logic._endaxis_frozen_wall = now
    logic._endaxis_clock_elapsed = action.start_seconds
    logic._endaxis_clock_wall = now


def _resume_action_clock(logic, action: EndAxisAction, now=None) -> None:
    """Resume plan time from the due timestamp once an action attempt actually starts."""
    now = logic._clock() if now is None else float(now)
    logic._endaxis_frozen = False
    logic._endaxis_clock_elapsed = action.start_seconds
    logic._endaxis_clock_wall = now
    logic._endaxis_frozen_elapsed = action.start_seconds
    logic._endaxis_frozen_wall = None


def _sync_due_action(logic, now=None) -> EndAxisAction | None:
    """Freeze the plan clock exactly when the next action becomes due."""
    action = _next_action(logic)
    if action is None:
        return None
    now = logic._clock() if now is None else float(now)
    not_before = float(getattr(logic, "_endaxis_not_before", now))
    if now + _EPSILON < not_before:
        return None
    if getattr(logic, "_endaxis_attempt_index", None) is not None:
        return action
    if getattr(logic, "_endaxis_frozen", False):
        return action
    if _plan_elapsed(logic, now) + _EPSILON < action.start_seconds:
        return None

    _freeze_at_action(logic, action, now)
    label = action.name or action.action_id or f"{action.kind}:{action.slot}"
    logic.task.log_info(
        f"EndAxis 时间轴到点: {action.start_seconds:.3f}s {label}，冻结计划时钟直到动作可开始"
    )
    return action


def _commit_action(logic, expected_index: int, *, outcome: str = "confirmed") -> bool:
    """Advance one confirmed EndAxis action without double-counting its execution time."""
    if not _plan_active(logic) or logic._endaxis_cursor != expected_index:
        return False
    action = _next_action(logic)
    if action is None:
        return False

    now = logic._clock()
    # Direct commits from a still-frozen state (mainly defensive/tests) resume at
    # the due point now. Normal runtime attempts already resumed when the input
    # was sent, so their animation/confirmation time remains part of plan time.
    if getattr(logic, "_endaxis_frozen", False):
        _resume_action_clock(logic, action, now)

    logic._endaxis_cursor += 1
    logic._endaxis_attempt_index = None
    logic.task.log_info(
        f"EndAxis 动作已{outcome}: {action.kind}:{action.slot} @ {action.start_seconds:.3f}s；"
        f"进度 {logic._endaxis_cursor}/{len(logic._endaxis_scenario.actions)}"
    )
    if not _plan_active(logic):
        logic._endaxis_completed = True
        logic.task.log_info("EndAxis 时间轴执行完成，恢复技能时间排轴的原生动态规划")
    return True


def _try_endaxis_ultimate(logic, action: EndAxisAction) -> bool:
    """Attempt a planned ultimate and commit only after clean native feedback."""
    token = str(action.slot)
    if not logic._slot_available(token):
        return False
    profiles = logic.store.profiles(logic.team[int(token) - 1], "ult")
    if not logic._ready(profiles, slot=token, kind="ult"):
        return False
    if not logic.task._find_battle_ult("ult_" + token):
        return False

    expected_index = logic._endaxis_cursor
    started = logic._clock()
    logic._endaxis_attempt_index = expected_index
    logic._arm_action_feedback()
    _resume_action_clock(logic, action, started)
    if not logic.task.use_ult(ult_sequence=token, wait_for_team_recovery=True):
        logic._endaxis_attempt_index = None
        logic._clear_action_attempt_feedback()
        _freeze_at_action(logic, action, logic._clock())
        return False

    logic._note_action_attempt("ult", token)
    recovered_at = logic._clock()
    # use_ult() is a strong success signal, but keep the imported cursor pending
    # until the same short negative-feedback confirmation used by low-cost battle
    # casts has observed clean frames. A block calls _cancel_unstarted_action(),
    # whose EndAxis wrapper clears the attempt and rolls the plan clock back.
    if logic._wait_assumed_success_feedback():
        return True
    logic._clear_action_attempt_feedback()

    logic._observe_bonus("ult", token)
    logic._observe_phase_action(token, "ult")
    logic._set_cooldowns(profiles, started)
    logic._activate_state(token, logic.ult_state_specs.get(token), "终结技", started)
    logic.main_control.record_cast(token, "ult", profiles, started, recovered_at=recovered_at)
    logic._after_ultimate_mechanic(token, recovered_at)
    logic._clear_active(recovered_at)
    _commit_action(logic, expected_index)
    logic.task.log_info(
        f"EndAxis: 终结技 {token} 动画结束并通过动作反馈确认，HUD 动画锁 {recovered_at - started:.2f}s"
    )
    logic.main_control.update(force=True)
    return True


def _step_endaxis(logic) -> None:
    """Run one timed-combat frame while an EndAxis plan still has actions."""
    now = logic._clock()
    if logic._probe_action_feedback() is not None:
        return
    logic._confirm_battle(now)
    if logic.main_control.update():
        return
    if not logic.team:
        logic._hold(True)
        return

    action = _sync_due_action(logic, now)
    if action is None:
        logic._hold(True)
        return

    # A high-cost battle skill or ultimate confirmation may still own this due
    # action. Do not issue another input until that attempt commits or rolls back.
    if getattr(logic, "_endaxis_attempt_index", None) is not None:
        logic._hold(True)
        return

    if action.kind == "skill":
        sp = logic._sample_sp()
        expected_index = logic._endaxis_cursor
        logic._endaxis_attempt_index = expected_index
        _resume_action_clock(logic, action, logic._clock())
        attempted = logic._try_battle_token(str(action.slot), sp, advance_cursor=False)
        if not attempted:
            logic._endaxis_attempt_index = None
            _freeze_at_action(logic, action, logic._clock())
        return

    if action.kind == "ultimate":
        _try_endaxis_ultimate(logic, action)
        return

    logic.task.log_warning(f"EndAxis 时间排轴不支持动作类型 {action.kind!r}")


def install_endaxis_timed_config() -> None:
    """Register the timed EndAxis field before BattleMixin/global config import."""
    global _CONFIG_INSTALLED
    if _CONFIG_INSTALLED:
        return

    from src.core.BattleConfig import (
        BATTLE_CONFIG_DESCRIPTION,
        BATTLE_CONFIG_TYPE,
        DEFAULT_BATTLE_CONFIG,
        KEY_TIMING_ROTATION,
    )

    DEFAULT_BATTLE_CONFIG.setdefault(KEY_TIMING_ENDAXIS, "")
    BATTLE_CONFIG_TYPE.setdefault(KEY_TIMING_ENDAXIS, {})
    BATTLE_CONFIG_DESCRIPTION.setdefault(
        KEY_TIMING_ENDAXIS,
        "可直接粘贴 EndAxis 数据码。多场景可写 endaxis[场景ID或名称]:<数据码>。\n"
        "配置后由技能时间排轴直接执行绝对时间计划：动作到点且尚不能开始时冻结计划时钟；"
        "实际尝试开始后时钟继续流逝，但只有确认成功才推进到下一动作。",
    )
    true_children = BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION].setdefault("sub_configs", {}).setdefault(True, [])
    if KEY_TIMING_ENDAXIS not in true_children:
        true_children.append(KEY_TIMING_ENDAXIS)
    _CONFIG_INSTALLED = True


def install_endaxis_timed_combat_patch() -> None:
    """Install EndAxis as an optional authoritative plan source for timed combat."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    install_endaxis_timed_config()

    from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

    original_run = TimedCombatLogic.run
    original_step = TimedCombatLogic.step
    original_enemy_paused = TimedCombatLogic._enemy_operation_paused
    original_accept_battle = TimedCombatLogic._accept_battle_skill
    original_cancel_unstarted = TimedCombatLogic._cancel_unstarted_action
    original_confirm_battle = TimedCombatLogic._confirm_battle
    original_configure_team = TimedCombatLogic._configure_team

    def run_with_endaxis(self, start_sleep=None, no_battle=False, deadline=None):
        """Load the optional EndAxis plan before entering the native timed loop."""
        self._endaxis_scenario = None
        self._endaxis_cursor = 0
        self._endaxis_attempt_index = None
        self._endaxis_frozen = False
        self._endaxis_frozen_elapsed = 0.0
        self._endaxis_frozen_wall = None
        self._endaxis_completed = False
        try:
            raw = self.task.get_battle_config(KEY_TIMING_ENDAXIS, "")
        except Exception:
            raw = ""

        if str(raw or "").strip():
            try:
                self._endaxis_scenario = _scenario_from_config(raw)
            except EndAxisTimelineError as exc:
                self.task.log_warning(f"EndAxis 时间排轴配置无效: {exc}；停止本轮技能时间排轴")
                return False

        now = self._clock()
        self._endaxis_not_before = now + max(0.0, float(start_sleep or 0.0))
        self._endaxis_clock_elapsed = 0.0
        self._endaxis_clock_wall = self._endaxis_not_before
        if self._endaxis_scenario is not None:
            label = self._endaxis_scenario.name or self._endaxis_scenario.scenario_id or "未命名场景"
            self.task.log_info(
                f"EndAxis 时间排轴接管计划层: {label}，{len(self._endaxis_scenario.actions)} 个动作；"
                "原生实时监测与动作确认继续生效"
            )
        return original_run(self, start_sleep=start_sleep, no_battle=no_battle, deadline=deadline)

    def step_with_endaxis(self):
        """Use EndAxis as the plan lane only while imported actions remain."""
        if _plan_active(self):
            return _step_endaxis(self)
        return original_step(self)

    def enemy_paused_with_endaxis(self):
        """Let the plan reach/freeze at due time while enemy scheduling is paused."""
        result = original_enemy_paused(self)
        if _plan_active(self):
            _sync_due_action(self, self._clock())
        return result

    def accept_battle_with_endaxis(self, token, advance_cursor=True):
        """Commit an imported battle action only after native success handling."""
        expected_index = getattr(self, "_endaxis_attempt_index", None)
        result = original_accept_battle(self, token, advance_cursor=advance_cursor)
        action = _next_action(self)
        if (
            expected_index is not None
            and action is not None
            and action.kind == "skill"
            and str(action.slot) == str(token)
        ):
            _commit_action(self, expected_index)
        return result

    def cancel_unstarted_with_endaxis(self, kind, token, now):
        """Keep the due EndAxis cursor when native feedback cancels an attempt."""
        action = _next_action(self)
        result = original_cancel_unstarted(self, kind, token, now)
        expected_index = getattr(self, "_endaxis_attempt_index", None)
        if expected_index is None or action is None or str(action.slot) != str(token):
            return result

        matching_battle = kind == "battle" and action.kind == "skill"
        matching_ult = kind == "ult" and action.kind == "ultimate"
        if matching_battle or matching_ult:
            self._endaxis_attempt_index = None
            _freeze_at_action(self, action, now)
        return result

    def confirm_battle_with_endaxis(self, now):
        """Roll plan time back to due when SP-delta confirmation times out."""
        expected_index = getattr(self, "_endaxis_attempt_index", None)
        action = _next_action(self)
        pending_before = self.pending
        result = original_confirm_battle(self, now)
        if (
            expected_index is not None
            and pending_before is not None
            and self.pending is None
            and getattr(self, "_endaxis_attempt_index", None) == expected_index
        ):
            # Successful confirmation commits inside _accept_battle_skill and
            # clears the marker. Reaching this branch therefore means failure.
            self._endaxis_attempt_index = None
            if action is not None:
                _freeze_at_action(self, action, now)
        return result

    def configure_team_with_endaxis(self, *args, **kwargs):
        """Keep native timing/mechanics data but disable its strategic plan veto."""
        result = original_configure_team(self, *args, **kwargs)
        if result and _plan_active(self):
            self.phase_planner.active_plan = None
            self.plan = None
        return result

    TimedCombatLogic.run = run_with_endaxis
    TimedCombatLogic.step = step_with_endaxis
    TimedCombatLogic._enemy_operation_paused = enemy_paused_with_endaxis
    TimedCombatLogic._accept_battle_skill = accept_battle_with_endaxis
    TimedCombatLogic._cancel_unstarted_action = cancel_unstarted_with_endaxis
    TimedCombatLogic._confirm_battle = confirm_battle_with_endaxis
    TimedCombatLogic._configure_team = configure_team_with_endaxis
    _PATCH_INSTALLED = True
