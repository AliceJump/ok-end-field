"""Independent, conservative timeline scheduler using the existing battle monitors."""

from __future__ import annotations

import time
from collections import deque

from src.data.character_mechanics import load_character_mechanics, mechanic_blockers
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_observation import (
    ActionBlockReason,
    EnemyPresence,
    normalize_action_block_reason,
    normalize_enemy_presence,
)
from src.data.combat_runtime import CombatRuntime
from src.data.skill_rotation import generate_damage_rotation
from src.data.skill_timing import SkillTiming, SkillTimingStore, load_skill_timings
from src.data.team_phase_planner import (
    CombatPhase,
    TeamPhasePlanner,
    build_team_burst_plans,
)
from src.data.timing_dps import build_options, load_damage_quotes, optimize_cycle
from src.image.enemy_health_probe import reset_enemy_presence_probe
from src.image.skill_bar_expected_probe import read_expected_skill_bar_sp


class TimedCombatLogic:
    _NORMAL_ATTACK_REASSERT_INTERVAL = 0.25
    _DEFAULT_ASSUME_SUCCESS_SP_THRESHOLD = 25.0
    _ASSUME_SUCCESS_PAUSE = 0.1
    _ACTION_FEEDBACK_PROBE_GAP = 0.02
    _SP_ERROR_MARGIN = 5.0
    _NATURAL_SP_PER_SECOND = 8.0
    _SP_LOW_PROBE_INTERVAL = 0.25
    _SP_MEDIUM_PROBE_INTERVAL = 0.15
    _SP_HIGH_PROBE_INTERVAL = 0.08
    _SP_UNKNOWN_PROBE_INTERVAL = 0.10
    _SP_PRESSURE_MIN = 250.0
    _SP_PRESSURE_MAX = 275.0
    _SP_PRESSURE_HEADROOM = 10.0
    _FAILED_CAST_RETRY_DELAY = 0.20
    _ACTION_FEEDBACK_WINDOW = 1.20
    _DEAD_SLOT_CONFIRM_REFRESHES = 3

    def __init__(self, task, store=None, clock=None, wall_clock=None):
        self.task = task
        self.store = store
        self._clock = clock or time.monotonic
        self._sp_wall_clock = wall_clock or time.monotonic
        self.team = []
        self.order = []
        self.ult_order = ["1", "2", "3", "4"]
        self.cursor = 0
        self.active: tuple[SkillTiming, ...] = ()
        self.active_slot = None
        self.active_kind = None
        self.started = 0.0
        self.cooldowns = {}
        self.pending = None
        self.unconfirmed = False
        self._holding = False
        self.damage_quotes = {}
        self.plan = None
        self.cycle_samples = deque(maxlen=5)
        self._cycle_start = None
        self._cycle_bonus = 0.0
        self._completed_cycles = 0
        self.normal_attack_sp_gains = {}
        self.assume_success_sp_threshold = self._DEFAULT_ASSUME_SUCCESS_SP_THRESHOLD
        self.state_specs = {}
        self.ult_state_specs = {}
        self.state_until = {}
        self.disabled_slots = set()
        self.mechanics = load_character_mechanics()
        self.team_mechanics = {}
        self.battle_phase_indices = {}
        self.forced_battle_token = None
        self.free_battle_once = set()
        self.forced_main_control_slot = None
        self.forced_main_control_until = 0.0
        self.pending_advance_cursor = True
        self.cached_sp = -1.0
        self.expected_sp = -1.0
        self.last_observed_sp = -1.0
        self.last_visual_sp_wall_time = None
        self.next_sp_probe_at = 0.0
        self.last_sp_probe_at = 0.0
        self.sp_pressure_threshold = 265.0
        self.battle_retry_after = {}
        self.dead_slot_evidence = {}
        self.enemy_pause_started = None
        self.last_action_attempt = None
        self.phase_planner = TeamPhasePlanner()
        self._last_phase_log = None
        self.combat_runtime = None

    def _hold(self, enabled, force=False):
        if enabled:
            if self._holding and not force:
                return
            self._holding = True
            self.task.active_and_send_mouse_delta(activate=True, only_activate=True)
            self.task.mouse_down(key="left")
            return
        if not self._holding and not force:
            return
        self._holding = False
        self.task.mouse_up(key="left")

    def _enemy_operation_paused(self):
        """Pause skill scheduling while an explicit detector says no enemy exists.

        UNKNOWN preserves current behavior. Once an explicit ABSENT observation
        starts a pause, UNKNOWN does not resume skill scheduling; a positive
        PRESENT observation is required. Normal attack and middle-button target
        acquisition must keep running during this pause because those inputs are
        what let a newly spawned or newly reachable enemy become targetable.
        No timestamps are shifted, so cooldowns, state_until and native timeline
        elapsed time keep advancing on the monotonic combat clock.
        """
        probe = getattr(self.task, "probe_enemy_presence", None)
        state = normalize_enemy_presence(probe() if callable(probe) else None)
        now = self._clock()

        if state == EnemyPresence.ABSENT:
            if self.enemy_pause_started is None:
                self.enemy_pause_started = now
                self.task.log_info(
                    "时间排轴敌人占位检测: 战斗UI仍在但未见敌人证据，暂停技能调度；"
                    "保持普攻和中键索敌，状态与冷却时间继续流逝"
                )
            return True

        if self.enemy_pause_started is not None:
            if state != EnemyPresence.PRESENT:
                return True
            elapsed = max(0.0, now - self.enemy_pause_started)
            self.enemy_pause_started = None
            self.task.log_info(f"时间排轴敌人占位检测: 敌人重新出现，恢复技能调度；暂停 {elapsed:.2f}s 已计入状态耗时")
        return False

    def _arm_action_feedback(self):
        arm = getattr(self.task, "arm_combat_action_feedback_probe", None)
        if callable(arm):
            arm()

    def _clear_action_attempt_feedback(self):
        self.last_action_attempt = None
        reset = getattr(self.task, "reset_combat_action_feedback_probe", None)
        if callable(reset):
            reset()

    def _note_action_attempt(self, kind, token=None):
        self.last_action_attempt = (self._clock(), kind, token)

    def _cancel_unstarted_action(self, kind, token, now):
        if self.combat_runtime is not None:
            self.combat_runtime.cancel(token, kind)
        if kind == "battle":
            if self.pending is not None and self.pending[1] == token:
                self.pending = None
                self.pending_advance_cursor = True
            if token is not None:
                self.battle_retry_after[token] = now + self._FAILED_CAST_RETRY_DELAY
            if self.active_kind == "battle" and self.active_slot == token:
                self._clear_active(now)
            return
        if self.active_kind == kind:
            self._clear_active(now)

    def _probe_action_feedback(self):
        """Consume the top-center action-failure hook.

        TOO_FAR is detected by the cheap fixed-position white text-band probe;
        DURING_SKILL remains reserved for a later detector. Scheduler semantics:
        - too_far: cancel the unstarted action and enter the distance-recovery
          hook (future target-lock -> settle -> long forward rush);
        - during_skill: cancel the unstarted action and retry later without
          inventing a successful skill timeline.
        """
        if self.last_action_attempt is None:
            return None

        now = self._clock()
        attempted_at, kind, token = self.last_action_attempt
        if now - attempted_at > self._ACTION_FEEDBACK_WINDOW:
            self.last_action_attempt = None
            return None

        probe = getattr(self.task, "probe_combat_action_block_reason", None)
        reason = normalize_action_block_reason(probe() if callable(probe) else None)
        if reason is None:
            return None

        self._clear_action_attempt_feedback()
        self._cancel_unstarted_action(kind, token, now)

        if reason == ActionBlockReason.TOO_FAR:
            recover = getattr(self.task, "recover_target_too_far", None)
            started = bool(recover()) if callable(recover) else False
            self.task.log_info(
                "时间排轴动作受阻: 离敌人太远；进入距离恢复钩子"
                + ("（已启动）" if started else "（占位，尚未实现移动）")
            )
        elif reason == ActionBlockReason.DURING_SKILL:
            self.task.log_info("时间排轴动作受阻: 当前技能期间无法释放此技能；取消未证实时间轴并稍后重试")
        return reason

    def _sp_probe_interval(self, sp):
        if sp < 0:
            return self._SP_UNKNOWN_PROBE_INTERVAL
        if sp >= self.sp_pressure_threshold:
            return self._SP_HIGH_PROBE_INTERVAL
        if sp >= max(0.0, self.sp_pressure_threshold - 50.0):
            return self._SP_MEDIUM_PROBE_INTERVAL
        return self._SP_LOW_PROBE_INTERVAL

    def _cache_sp(self, sp, now=None):
        """Cache scheduler SP without changing visual-observation state."""
        now = self._clock() if now is None else now
        value = float(sp)
        self.cached_sp = value
        self.last_sp_probe_at = now
        self.next_sp_probe_at = now + self._sp_probe_interval(value)
        return value

    def _cache_visual_sp(self, sp, now=None, wall_now=None):
        """Commit a successful visual observation as the new prediction anchor."""
        now = self._clock() if now is None else now
        wall_now = self._sp_wall_clock() if wall_now is None else wall_now
        value = self._cache_sp(sp, now)
        self.expected_sp = value
        self.last_observed_sp = value
        self.last_visual_sp_wall_time = wall_now
        return value

    def _project_probe_expected(self, wall_now=None):
        """Add natural regen once for the full visual-to-visual wall-clock window."""
        base = self.expected_sp
        anchor = self.last_visual_sp_wall_time
        if base < 0 or anchor is None:
            return None
        wall_now = self._sp_wall_clock() if wall_now is None else wall_now
        elapsed = max(0.0, float(wall_now) - float(anchor))
        return min(300.0, max(0.0, base + elapsed * self._NATURAL_SP_PER_SECOND))

    def _sample_sp(self, force=False):
        """Sample SP, using the current prediction only to choose the first ROI."""
        now = self._clock()
        wall_now = self._sp_wall_clock()
        if (
            not force
            and self.cached_sp >= self.sp_pressure_threshold
            and hasattr(self.task, "is_skill_bar_full_fast")
            and self.task.is_skill_bar_full_fast()
        ):
            return self._cache_visual_sp(300.0, now, wall_now)

        if not force and now < self.next_sp_probe_at:
            return self.cached_sp

        probe_expected = self._project_probe_expected(wall_now)
        observed = read_expected_skill_bar_sp(
            self.task,
            probe_expected,
            frame=getattr(self.task, "frame", None),
        )
        if observed >= 0:
            return self._cache_visual_sp(observed, now, wall_now)

        # No new visual truth: retain prediction and the previous visual anchor.
        self.next_sp_probe_at = now + self._SP_UNKNOWN_PROBE_INTERVAL
        return -1.0

    def _note_assumed_sp_spend(self, before_sp, expected_cost):
        """Apply unverified low-cost spending to prediction only."""
        if before_sp < 0:
            self.next_sp_probe_at = self._clock()
            return
        base = self.expected_sp if self.expected_sp >= 0 else float(before_sp)
        predicted = max(0.0, base - max(0.0, float(expected_cost)))
        self.expected_sp = predicted
        self._cache_sp(predicted)

    def _log_phase_transition(self, old, new):
        if old == new:
            return
        plan = self.phase_planner.active_plan
        target = self.phase_planner.target_sp if plan is not None else 0
        reserve = self.phase_planner.reserve_floor if plan is not None else 0
        key = (old, new, plan.key if plan else None)
        if key == self._last_phase_log:
            return
        self._last_phase_log = key
        self.task.log_info(
            f"时间排轴阶段: {old.value} -> {new.value}"
            + (f"，爆发 {plan.key}，目标 {target:g} SP，保留 {reserve:g} SP" if plan is not None else "")
        )

    def _observe_phase_sp(self, sp):
        if self.combat_runtime is not None:
            self.combat_runtime.advance(self._clock())
        old, new = self.phase_planner.observe_sp(sp)
        self._log_phase_transition(old, new)
        return new

    def _observe_phase_action(self, token, kind):
        old, new = self.phase_planner.observe_action(token, kind)
        self._log_phase_transition(old, new)

    def _allowed(self, candidates=(), candidate_slot=None, candidate_kind=None):
        if self.pending is not None:
            return False
        if not self.active:
            return True
        elapsed = self._clock() - self.started
        if self.unconfirmed:
            return all(profile.allows(elapsed, ()) for profile in self.active)

        cross_actor = (
            (self.active_slot is not None and candidate_slot is not None and candidate_slot != self.active_slot)
            or (candidate_kind == "link")
            or (self.active_kind == "link" and candidate_kind in {"battle", "ult"})
            or (not candidates and candidate_slot is None and candidate_kind is None)
        )
        if cross_actor:
            return all(profile.committed(elapsed) for profile in self.active)
        return all(profile.allows(elapsed, candidates) for profile in self.active)

    def _ready(self, profiles, slot=None, kind=None):
        now = self._clock()
        return (
            bool(profiles)
            and self._allowed(profiles, candidate_slot=slot, candidate_kind=kind)
            and all(now >= self.cooldowns.get(profile.skill_id, 0) for profile in profiles)
        )

    def _begin(self, profiles, started, slot=None, kind=None):
        self.active = profiles
        self.active_slot = slot
        self.active_kind = kind
        self.started = started
        self.unconfirmed = False

    def _clear_active(self, now=None):
        self.active = ()
        self.active_slot = None
        self.active_kind = None
        self.started = self._clock() if now is None else now
        self.unconfirmed = False

    def _activate_state(self, token, spec, source, started=None):
        if spec is None:
            return
        started = self.started if started is None else started
        until = started + spec.duration
        self.state_until[token] = max(self.state_until.get(token, 0), until)
        self.task.log_info(
            f"时间排轴: {source} {token} 进入特殊状态 {spec.duration:.2f}s，"
            f"{spec.end_skill_id} 仅作为主动中止"
            + ("" if spec.end_cooldown is None else f"，主动中止冷却约 {spec.end_cooldown:g}s")
        )

    def _mechanic_for_token(self, token):
        return self.team_mechanics.get(token)

    def _battle_context(self, token):
        """Resolve the currently visible battle-button phase and its SP semantics."""
        name = self.team[int(token) - 1]
        runtime = self.combat_runtime
        if runtime is not None and runtime.catalog.candidates(token, "battle"):
            runtime.advance(self._clock())
            available = runtime.catalog.available(token, "battle")
            if not available:
                return (), None, None
            selected = available[0]
            profiles = self.store.battle_phase_profiles(name) or self.store.profiles(name, "battle")
            matching = tuple(p for p in profiles if p.skill_id == selected.key)
            if matching:
                # Immediate native refunds change the observable SP drop, but
                # never lower the SP budget needed to start the action.
                predicted = runtime.world.fork()
                before = predicted.sp
                if predicted.start(selected, action_id="confirmation-preview"):
                    net_cost = before - predicted.sp
                else:
                    net_cost = selected.sp_cost
                return matching, max(selected.sp_cost, selected.gate or 0), max(0, net_cost)
        mechanic = self._mechanic_for_token(token)
        if mechanic is not None and mechanic.archetype == "multi_stage_battle":
            phases = self.store.battle_phase_profiles(name)
            battle_transitions = tuple(
                transition for transition in mechanic.transitions if transition.action == "battle"
            )
            if phases and len(phases) == len(battle_transitions):
                index = min(self.battle_phase_indices.get(token, 0), len(phases) - 1)
                transition = battle_transitions[index]
                gate = transition.sp_gate
                cost = transition.sp_cost
                if gate is not None and cost is not None:
                    return (phases[index],), float(gate), max(0.0, float(cost) - transition.sp_refund)

        profiles = self.store.profiles(name, "battle")
        point_costs = [profile.skill_points for profile in profiles]
        sp_costs = [profile.sp_cost for profile in profiles]
        cost = None if not profiles or None in sp_costs else max(sp_costs)
        if cost is None and profiles and None not in point_costs:
            cost = max(point_costs) * 100.0
        if token in self.free_battle_once and profiles:
            return profiles, 0.0, 0.0
        return profiles, cost, cost

    def _advance_mechanic_battle(self, token):
        mechanic = self._mechanic_for_token(token)
        if mechanic is None or mechanic.archetype != "multi_stage_battle":
            if self.forced_battle_token == token:
                self.forced_battle_token = None
            self.free_battle_once.discard(token)
            return

        phases = self.store.battle_phase_profiles(self.team[int(token) - 1])
        if not phases:
            return
        index = min(self.battle_phase_indices.get(token, 0), len(phases) - 1)
        next_index = (index + 1) % len(phases)
        self.battle_phase_indices[token] = next_index

        if index == 0 and len(phases) > 1:
            self.forced_battle_token = token
        elif self.forced_battle_token == token:
            self.forced_battle_token = None

    def _reset_conditional_battle_phase_after_failed_attempt(self, token):
        mechanic = self._mechanic_for_token(token)
        if mechanic is None or mechanic.archetype != "multi_stage_battle":
            return
        phases = self.store.battle_phase_profiles(self.team[int(token) - 1])
        if phases and self.battle_phase_indices.get(token, 0) >= len(phases) - 1:
            self.battle_phase_indices[token] = 0
            if self.forced_battle_token == token:
                self.forced_battle_token = None
            self.phase_planner.reset_cycle()
            self.task.log_info(f"时间排轴: 战技 {token} 条件段未确认，重置到首段状态")

    def _accept_battle_skill(self, token, advance_cursor=True):
        self._clear_action_attempt_feedback()
        if self.combat_runtime is not None:
            self.combat_runtime.confirm(token, "battle", self._clock())
        self._observe_battle()
        self._set_cooldowns()
        self._activate_state(token, self.state_specs.get(token), "战技")
        self._observe_phase_action(token, "battle")
        self._advance_mechanic_battle(token)
        self.free_battle_once.discard(token)
        if advance_cursor and self.order:
            self.cursor = (self.cursor + 1) % len(self.order)

    def _confirm_battle(self, now):
        if self.pending is None:
            return
        before_sp, token, expected_cost = self.pending
        current_sp = self._sample_sp(force=True)
        elapsed = max(0.0, now - self.started)
        minimum_drop = max(
            2.0,
            expected_cost - self.assume_success_sp_threshold - self._NATURAL_SP_PER_SECOND * elapsed,
        )
        if current_sp >= 0 and before_sp - current_sp >= minimum_drop:
            self.pending = None
            advance_cursor = self.pending_advance_cursor
            self.pending_advance_cursor = True
            self._accept_battle_skill(token, advance_cursor=advance_cursor)
            self.task.log_info(
                f"时间排轴: 战技 {token} 技力消耗已确认 ({before_sp:.1f}->{current_sp:.1f}, 阈值 {minimum_drop:.1f})"
            )
        elif now - self.started >= 0.8:
            if self.combat_runtime is not None:
                self.combat_runtime.cancel(token, "battle")
            self.pending = None
            self._clear_action_attempt_feedback()
            self.pending_advance_cursor = True
            self._reset_conditional_battle_phase_after_failed_attempt(token)
            self.battle_retry_after[token] = now + self._FAILED_CAST_RETRY_DELAY
            self._clear_active(now)
            self.next_sp_probe_at = min(self.next_sp_probe_at, now)
            self.task.log_info(
                f"时间排轴: 战技 {token} 未确认消耗，清除未证实时间轴，{self._FAILED_CAST_RETRY_DELAY:.2f}s 后可重试"
            )

    def _set_cooldowns(self, profiles=None, started=None):
        profiles = self.active if profiles is None else profiles
        started = self.started if started is None else started
        for profile in profiles:
            self.cooldowns[profile.skill_id] = started + profile.cooldown

    def _slot_available(self, token):
        if token in self.disabled_slots:
            return False
        try:
            index = int(token) - 1
        except (TypeError, ValueError):
            return False
        return 0 <= index < len(self.team) and self.team[index] != "?"

    def _active_team_names(self):
        return [
            name for index, name in enumerate(self.team, 1) if str(index) not in self.disabled_slots and name != "?"
        ]

    def _refresh_sp_threshold(self):
        active_names = self._active_team_names()
        active_gains = {name: self.normal_attack_sp_gains.get(name) for name in active_names}
        known_gains = [value for value in active_gains.values() if value is not None]
        has_unknown = any(value is None for value in active_gains.values())
        fallback_gain = self.store.global_normal_attack_sp_gain() if active_names and has_unknown else None
        threshold_gain = max(
            known_gains + ([fallback_gain] if fallback_gain is not None else []),
            default=self._DEFAULT_ASSUME_SUCCESS_SP_THRESHOLD - self._SP_ERROR_MARGIN,
        )
        self.assume_success_sp_threshold = threshold_gain + self._SP_ERROR_MARGIN
        self.sp_pressure_threshold = max(
            self._SP_PRESSURE_MIN,
            min(
                self._SP_PRESSURE_MAX,
                300.0 - threshold_gain - self._SP_PRESSURE_HEADROOM,
            ),
        )
        self.task.log_info(
            f"时间排轴技力确认阈值: 存活槽位重击回复 {active_gains}, "
            f"最大值 {threshold_gain:g} + 误差 {self._SP_ERROR_MARGIN:g} = "
            f"{self.assume_success_sp_threshold:g} SP；"
            f"防溢出压力线 {self.sp_pressure_threshold:g} SP"
        )

    def _sync_task_team_slots(self):
        """Make BattleMixin's HUD-recovery matcher ignore unknown/dead slots only.

        Unknown slots are deliberately *not* scheduler-disabled: they stay eligible
        for later background completion. The BattleMixin recovery matcher uses the
        task-level set as a positional don't-care mask, so partial entry teams do
        not make every ultimate wait for a literal '?' portrait to reappear.
        """
        ignored = {index for index, name in enumerate(self.team) if name == "?"}
        ignored.update(int(token) - 1 for token in self.disabled_slots)
        self.task._battle_team_disabled_slots = ignored

    def _configure_team(self, team, *, reset_runtime=False, filled_slots=()):
        """Apply a stable full-or-partial four-slot snapshot to the scheduler.

        Composition-derived data is rebuilt whenever a '?' slot is completed,
        while cooldowns, active timelines and already-authored state timers stay
        intact. Initial detection resets per-battle slot failure state; later
        completion does not.
        """
        team = list(team)
        if len(team) != 4 or all(name == "?" for name in team):
            return False

        previous_team = list(self.team)
        previous_order = list(self.order)
        current_token = (
            previous_order[self.cursor] if previous_order and 0 <= self.cursor < len(previous_order) else None
        )
        previous_phase_indices = dict(self.battle_phase_indices)

        if reset_runtime:
            self.disabled_slots.clear()
            self.dead_slot_evidence.clear()
            self.battle_retry_after.clear()
            self.forced_battle_token = None
            self.free_battle_once.clear()
            self.forced_main_control_slot = None
            self.forced_main_control_until = 0.0
            previous_phase_indices = {}

        self.team = team
        self.task._battle_team = list(team)
        self.ult_order = generate_damage_rotation(team)
        self.order = [
            token
            for token in self.ult_order
            if team[int(token) - 1] != "?" and self.store.profiles(team[int(token) - 1], "battle")
        ]

        self.team_mechanics = {
            str(index + 1): self.mechanics.get(name)
            for index, name in enumerate(team)
            if name != "?" and self.mechanics.get(name) is not None
        }
        self.battle_phase_indices = {}
        for token, mechanic in self.team_mechanics.items():
            if mechanic.archetype != "multi_stage_battle":
                continue
            index = int(token) - 1
            unchanged = not reset_runtime and index < len(previous_team) and previous_team[index] == team[index]
            self.battle_phase_indices[token] = previous_phase_indices.get(token, 0) if unchanged else 0

        available_tokens = {
            str(index + 1)
            for index, name in enumerate(team)
            if name != "?" and str(index + 1) not in self.disabled_slots
        }
        if self.forced_battle_token not in available_tokens:
            self.forced_battle_token = None
        self.free_battle_once.intersection_update(available_tokens)
        if self.forced_main_control_slot not in available_tokens:
            self.forced_main_control_slot = None
            self.forced_main_control_until = 0.0
        self.battle_retry_after = {
            token: value for token, value in self.battle_retry_after.items() if token in available_tokens
        }
        self.dead_slot_evidence = {
            token: value for token, value in self.dead_slot_evidence.items() if token in available_tokens
        }

        known_team = [name for name in team if name != "?"]
        self.normal_attack_sp_gains = self.store.team_normal_attack_sp_gains(known_team)
        self._refresh_sp_threshold()
        self.state_specs = {
            str(index + 1): self.store.battle_state(name) for index, name in enumerate(team) if name != "?"
        }
        self.ult_state_specs = {
            str(index + 1): self.store.ultimate_state(name) for index, name in enumerate(team) if name != "?"
        }
        self.damage_quotes = load_damage_quotes(team)
        if isinstance(self.store, SkillTimingStore):
            catalog = build_combat_catalog(team, self.store)
            self.combat_runtime = CombatRuntime(catalog, epoch=self._clock())
            for diagnostic in catalog.diagnostics:
                self.task.log_info(f"时间排轴机制数据: {diagnostic}")

        burst_plans = build_team_burst_plans(team, self.mechanics, self.store)
        preferred_slots = tuple(token for token in self.ult_order if self._slot_available(token))
        active_burst = self.phase_planner.configure(
            burst_plans,
            preferred_slots=preferred_slots,
        )
        if self.disabled_slots:
            self.phase_planner.disable_slots(set(self.disabled_slots))
        self._last_phase_log = None

        self.cycle_samples.clear()
        self._cycle_start = None
        self._cycle_bonus = 0.0
        self._completed_cycles = 0

        unknown_slots = [str(index + 1) for index, name in enumerate(team) if name == "?"]
        if reset_runtime:
            suffix = f"，未识别槽位 {unknown_slots} 后台继续补全" if unknown_slots else ""
            self.task.log_info(f"时间排轴队伍: {team}, 战技顺序: {self.order}{suffix}")
        elif filled_slots:
            details = [f"{token}:{name}" for token, name in filled_slots]
            self.task.log_info(f"时间排轴补全槽位 {details}，队伍更新为 {team}，战技顺序重算: {self.order}")

        changed_tokens = {
            str(index + 1)
            for index, name in enumerate(team)
            if reset_runtime or index >= len(previous_team) or previous_team[index] != name
        }
        for token, mechanic in self.team_mechanics.items():
            if token not in changed_tokens:
                continue
            self.task.log_info(
                f"时间排轴机制: {team[int(token) - 1]}({token}) {mechanic.archetype}; " + "；".join(mechanic.evidence)
            )
        for source, specs in (("战技", self.state_specs), ("终结技", self.ult_state_specs)):
            for token, spec in specs.items():
                if token not in changed_tokens or spec is None:
                    continue
                self.task.log_info(
                    f"时间排轴状态{source}: {team[int(token) - 1]}({token}) "
                    f"{spec.base_skill_id} -> {spec.end_skill_id}, " + ", ".join(spec.evidence)
                )

        if reset_runtime or filled_slots:
            for burst_plan in burst_plans:
                participants = ",".join(burst_plan.participants)
                damage = "unknown" if burst_plan.expected_damage is None else f"{burst_plan.expected_damage:.0f}"
                self.task.log_info(
                    f"时间排轴爆发候选: {burst_plan.key} 槽位[{participants}] "
                    f"起手至少 {burst_plan.min_start_sp:g} SP，结束预计 "
                    f"{burst_plan.expected_end_sp:g} SP，阶段伤害 {damage}，"
                    f"runtime={'yes' if burst_plan.runtime_executable else 'diagnostic'}"
                )
                for action in burst_plan.actions:
                    if (
                        action.expected_damage is None
                        or action.damage_low is None
                        or action.damage_high is None
                        or action.damage_high <= action.damage_low
                    ):
                        continue
                    self.task.log_info(
                        f"时间排轴隐藏状态期望: {action.actor}/{action.label} "
                        f"{action.expected_damage:.0f} "
                        f"(下界 {action.damage_low:.0f}, 上界 {action.damage_high:.0f}, "
                        f"满层概率 {action.full_probability:.1%}, "
                        f"期望层级比例 {action.expected_fraction:.1%}, "
                        f"{action.damage_basis})"
                    )
            if active_burst is not None:
                self.task.log_info(
                    f"时间排轴爆发蓄力: 选择 {active_burst.key}，"
                    f"保留 {active_burst.reserve_floor:g} SP，"
                    f"达到 {active_burst.min_start_sp:g} SP 后开始"
                )

        if unknown_slots:
            self.plan = None
            self.task.log_info("时间排轴DPS规划: 队伍仍有未识别槽位，先使用已知角色伤害顺序；补全后自动重算")
        else:
            blockers = mechanic_blockers(team)
            if blockers:
                self.plan = None
                labels = ", ".join(mechanic.blocker_reason for mechanic in blockers)
                self.task.log_info(
                    f"时间排轴机制规划: {labels} 不能压成单战技循环，禁用旧DPS子集搜索并保留完整战技顺序"
                )
            else:
                self.plan = optimize_cycle(build_options(team, self.store, self.damage_quotes))
                if self.plan is not None:
                    self.order = list(self.plan.slots)
                    self.task.log_info(
                        f"时间排轴DPS规划: 战技 {self.order}, 可重复窗口 {self.plan.seconds:.2f}s, "
                        f"预计战技伤害 {self.plan.damage:.0f}, 战技增量DPS {self.plan.dps:.1f}"
                    )
                else:
                    self.task.log_info("时间排轴DPS规划: 数据或状态映射不足，保留原伤害顺序")

        if current_token in self.order:
            self.cursor = self.order.index(current_token)
        else:
            self.cursor = 0
        self._sync_task_team_slots()
        return True

    def _refresh_team_slots(self, deadline):
        if not self.team:
            return
        detected, stable = self.task.detect_team_stable(
            max_attempts=2,
            interval=0.05,
            confidence=2,
            deadline=deadline,
        )
        if not stable or len(detected) != len(self.team) or all(member == "?" for member in detected):
            return

        mismatches = [
            (index + 1, expected, current)
            for index, (expected, current) in enumerate(zip(self.team, detected))
            if expected != "?" and current != "?" and current != expected
        ]
        if mismatches:
            self.task.log_debug(f"时间排轴忽略槽位刷新，已知角色位置不匹配: {mismatches}")
            return

        filled_slots = [
            (str(index + 1), current)
            for index, (expected, current) in enumerate(zip(self.team, detected))
            if expected == "?" and current != "?"
        ]
        if filled_slots:
            completed = list(self.team)
            for token, name in filled_slots:
                completed[int(token) - 1] = name
                self.dead_slot_evidence.pop(token, None)
            self._configure_team(completed, reset_runtime=False, filled_slots=filled_slots)

        candidates = set()
        for index, (expected, current) in enumerate(zip(self.team, detected), 1):
            token = str(index)
            if expected == "?" or token in self.disabled_slots:
                continue
            if current == expected:
                self.dead_slot_evidence.pop(token, None)
                continue
            if current == "?":
                count = self.dead_slot_evidence.get(token, 0) + 1
                self.dead_slot_evidence[token] = count
                if count >= self._DEAD_SLOT_CONFIRM_REFRESHES:
                    candidates.add(token)
                else:
                    self.task.log_debug(
                        f"时间排轴槽位 {token}:{expected} 暂时未识别，"
                        f"死亡确认 {count}/{self._DEAD_SLOT_CONFIRM_REFRESHES}"
                    )

        newly_disabled = candidates - self.disabled_slots
        if not newly_disabled:
            return

        self.disabled_slots.update(newly_disabled)
        if self.combat_runtime is not None:
            for token in newly_disabled:
                self.combat_runtime.disable_actor(token, self._clock())
        for token in newly_disabled:
            self.dead_slot_evidence.pop(token, None)
        self._sync_task_team_slots()

        if self.pending is not None and self.pending[1] in newly_disabled:
            self.pending = None
            self.unconfirmed = False
        if self.active_slot in newly_disabled:
            self._clear_active()
        if self.forced_battle_token in newly_disabled:
            self.forced_battle_token = None
        if self.forced_main_control_slot in newly_disabled:
            self.forced_main_control_slot = None
            self.forced_main_control_until = 0.0
        for token in newly_disabled:
            self.state_until.pop(token, None)
            self.free_battle_once.discard(token)
            self.battle_phase_indices.pop(token, None)
            self.battle_retry_after.pop(token, None)

        if self.phase_planner.disable_slots(newly_disabled):
            self.task.log_info("时间排轴阶段: 爆发参与槽位失效，取消当前爆发计划")

        self._refresh_sp_threshold()
        details = [f"{token}:{self.team[int(token) - 1]}" for token in sorted(newly_disabled, key=int)]
        self.task.log_info(
            f"时间排轴屏蔽失效槽位 {details}，已跨 {self._DEAD_SLOT_CONFIRM_REFRESHES} 次刷新确认，保留原始槽位编号"
        )

    def _detect_team(self, deadline):
        team, stable = self.task.detect_team_stable(
            max_attempts=2,
            interval=0.1,
            confidence=2,
            deadline=deadline,
        )
        if stable and len(team) == 4 and any(member != "?" for member in team):
            self._configure_team(team, reset_runtime=True)

    def _observe_battle(self):
        if self.plan is None or self.cursor != 0:
            return
        if self._cycle_start is not None:
            elapsed = self.started - self._cycle_start
            if elapsed > 0:
                expected = self.plan.opening_damage if self._completed_cycles == 0 else self.plan.damage
                self.cycle_samples.append((elapsed, expected + self._cycle_bonus))
                self._completed_cycles += 1
                seconds = sum(sample[0] for sample in self.cycle_samples)
                damage = sum(sample[1] for sample in self.cycle_samples)
                self.task.log_info(
                    f"时间排轴DPS观测: 最近 {len(self.cycle_samples)} 轮平均窗口 "
                    f"{seconds / len(self.cycle_samples):.2f}s, 基准技能DPS估计 {damage / seconds:.1f} "
                    "（窗口实测，伤害未实测）"
                )
        self._cycle_start = self.started
        self._cycle_bonus = 0.0

    def _observe_bonus(self, kind, token=None):
        if self._cycle_start is None:
            return
        if kind == "ult":
            quote = self.damage_quotes.get(self.team[int(token) - 1])
            self._cycle_bonus += quote.ult if quote else 0
        else:
            active_names = self._active_team_names()
            if active_names and all(name in self.damage_quotes for name in active_names):
                self._cycle_bonus += min(self.damage_quotes[name].link for name in active_names)

    def _skip_active_state_slots(self):
        if not self.order:
            return
        now = self._clock()
        for _ in range(len(self.order)):
            token = self.order[self.cursor]
            if self._slot_available(token) and now >= self.state_until.get(token, 0):
                return
            self.cursor = (self.cursor + 1) % len(self.order)

    def _try_battle_token(self, token, sp, overflow=False, advance_cursor=True):
        now = self._clock()
        if not self._slot_available(token) or now < self.state_until.get(token, 0):
            return False
        if now < self.battle_retry_after.get(token, 0):
            return False
        if self.forced_main_control_slot == token and now < self.forced_main_control_until:
            return False

        profiles, sp_gate, expected_cost = self._battle_context(token)
        if self.plan is not None and sp_gate is not None:
            followup = next((item for item in self.plan.support_followups if item[0] == token), None)
            if followup is not None and self._slot_available(followup[1]):
                _, next_token, delay = followup
                next_profiles, _, _ = self._battle_context(next_token)
                if not next_profiles or now + delay < self.state_until.get(next_token, 0):
                    return False
                if any(now + delay < self.cooldowns.get(profile.skill_id, 0) for profile in next_profiles):
                    return False
                sp_gate = max(sp_gate, dict(self.plan.required_sp).get(token, sp_gate))
        if (
            not profiles
            or sp_gate is None
            or expected_cost is None
            or sp < 0
            or sp < sp_gate
            or not self._ready(profiles, slot=token, kind="battle")
        ):
            return False
        if not self.phase_planner.can_spend(token, "battle", sp, expected_cost):
            return False

        started = self._clock()
        if not self._stage_combat_action(token, "battle", profiles, started):
            return False
        self.phase_planner.start_if_ready(token, "battle")
        self._arm_action_feedback()
        self.task.send_key(token)
        self._note_action_attempt("battle", token)
        self._begin(profiles, started, slot=token, kind="battle")
        self.pending_advance_cursor = advance_cursor

        if expected_cost <= self.assume_success_sp_threshold:
            if self._wait_assumed_success_feedback():
                return True
            self._note_assumed_sp_spend(sp, expected_cost)
            self.pending_advance_cursor = True
            self._accept_battle_skill(token, advance_cursor=advance_cursor)
            suffix = ""
            if sp_gate != expected_cost:
                suffix = f"（门槛 {sp_gate:g} SP，预计净消耗 {expected_cost:g} SP）"
            self.task.log_info(
                f"时间排轴: 战技 {token} 消耗证据 {expected_cost:g} SP <= "
                f"{self.assume_success_sp_threshold:g}，按键后直接视为成功{suffix}"
            )
        elif expected_cost > 0:
            self.pending = (sp, token, expected_cost)
            if overflow:
                self.task.log_info(
                    f"时间排轴: 高技力防溢出 {sp:.1f}/{self.sp_pressure_threshold:.1f} SP，抢占尝试战技 {token}"
                )
        else:
            if self._wait_assumed_success_feedback():
                return True
            self._note_assumed_sp_spend(sp, 0.0)
            self.pending_advance_cursor = True
            self._accept_battle_skill(token, advance_cursor=advance_cursor)
            self.task.log_info(f"时间排轴: 零净消耗战技 {token} 按键后直接视为成功")
        return True

    def _wait_assumed_success_feedback(self):
        self.task.sleep(self._ASSUME_SUCCESS_PAUSE)
        if not callable(getattr(self.task, "probe_combat_action_block_reason", None)):
            return False
        for attempt in range(2):
            self.task.next_frame()
            if self._probe_action_feedback() is not None:
                return True
            if attempt == 0:
                self.task.sleep(self._ACTION_FEEDBACK_PROBE_GAP)
        return False

    def _try_planned_battle_skill(self, sp, overflow=False):
        if not self.order:
            return False
        return self._try_battle_token(
            self.order[self.cursor],
            sp,
            overflow=overflow,
            advance_cursor=True,
        )

    def _overflow_score(self, token):
        if not self._slot_available(token):
            return float("-inf")
        mechanic = self._mechanic_for_token(token)
        if mechanic is not None and not mechanic.generic_cycle_safe:
            return float("-inf")
        name = self.team[int(token) - 1]
        profiles = self.store.profiles(name, "battle")
        if not profiles:
            return float("-inf")
        quote = self.damage_quotes.get(name)
        damage = quote.battle if quote is not None else 0.0
        handoff = max(max(profile.handoff, 0.3) for profile in profiles)
        return damage / handoff

    def _try_overflow_battle_skill(self, sp, checkpoint):
        if sp < self.sp_pressure_threshold or not self.order:
            return False

        current = self.order[self.cursor]
        if self._try_battle_token(current, sp, overflow=True, advance_cursor=True):
            self.task.log_info(f"时间排轴: 高技力抢占[{checkpoint}]，释放当前计划战技 {current}")
            return True

        candidates = []
        for index in range(len(self.team)):
            token = str(index + 1)
            if token == current:
                continue
            score = self._overflow_score(token)
            if score != float("-inf"):
                candidates.append((score, token))
        candidates.sort(key=lambda item: (-item[0], int(item[1])))

        for _score, token in candidates:
            if self._try_battle_token(token, sp, overflow=True, advance_cursor=False):
                self.task.log_info(f"时间排轴: 高技力抢占[{checkpoint}]，当前计划战技不可用，插入安全收益战技 {token}")
                return True
        return False

    def _after_ultimate_mechanic(self, token, ended):
        mechanic = self._mechanic_for_token(token)
        if mechanic is None:
            return

        if mechanic.archetype == "multi_stage_battle":
            phases = self.store.battle_phase_profiles(self.team[int(token) - 1])
            if len(phases) > 1:
                self.battle_phase_indices[token] = 1
                self.forced_battle_token = token
                self.phase_planner.seek_action(token, "battle", "追形")
                self.task.log_info(f"时间排轴机制: 终结技 {token} 后战技切到第2段，优先尝试后续段")

        if mechanic.archetype == "consume_status_build_stack_burst":
            self.free_battle_once.add(token)
            self.forced_battle_token = token
            self.task.log_info(f"时间排轴机制: 终结技 {token} 后首次战技按免费强化段处理并优先释放")

        if mechanic.forced_main_control_seconds:
            self.forced_main_control_slot = token
            self.forced_main_control_until = ended + mechanic.forced_main_control_seconds
            self.task.log_info(
                f"时间排轴机制: 终结技 {token} 保护自身主控普攻窗口 "
                f"{mechanic.forced_main_control_seconds:.1f}s；"
                "仅抑制该角色自身战技，其他角色技能/终结技照常"
            )

    def step(self):
        """One refreshed HUD observation; no legacy strategy switches are read."""
        now = self._clock()
        if self.combat_runtime is not None:
            detector = getattr(self.task, "detect_current_char_index", None)
            current = detector() if detector is not None else None
            if isinstance(current, int) and 0 <= current < len(self.team):
                self.combat_runtime.observe_main_control(str(current + 1), now)
        if self._probe_action_feedback() is not None:
            return
        self._confirm_battle(now)
        if not self.team:
            self._hold(True)
            return

        self._skip_active_state_slots()
        sp = self._sample_sp()
        phase = self._observe_phase_sp(sp)

        burst_action = self.phase_planner.next_action
        if (
            phase == CombatPhase.BURST_READY
            and burst_action is not None
            and burst_action.kind == "battle"
            and self._slot_available(burst_action.slot)
            and self._try_battle_token(
                burst_action.slot,
                sp,
                advance_cursor=False,
            )
        ):
            self.task.log_info(
                f"时间排轴爆发: 开始 {self.phase_planner.active_plan.key}，"
                f"动作 {burst_action.actor}/{burst_action.label}"
            )
            return

        if self.forced_battle_token is not None:
            token = self.forced_battle_token
            if self._slot_available(token) and self._try_battle_token(
                token,
                sp,
                advance_cursor=False,
            ):
                return
            if not self._slot_available(token):
                self.forced_battle_token = None

        if self._try_overflow_battle_skill(sp, "主循环"):
            return

        active_names = self._active_team_names()
        link_profiles = {name: self.store.profiles(name, "link") for name in active_names}
        links = tuple(profile for profiles in link_profiles.values() for profile in profiles)
        if active_names and self._allowed(links, candidate_kind="link") and self.task.is_link_skill_ready():
            started = self._clock()
            self._arm_action_feedback()
            if self.task.use_link_skill():
                self._note_action_attempt("link")
                if links:
                    self._begin(links, started, kind="link")
                else:
                    self._clear_active(started)
                self._observe_bonus("link")
                missing = [name for name, profiles in link_profiles.items() if not profiles]
                self.task.log_info(
                    "时间排轴: 连携技按现有就绪检测释放" + (f"；缺少时间轴 {missing}，不阻断释放" if missing else "")
                )
                return

        ready_ults = []
        if self.forced_main_control_slot is not None and now >= self.forced_main_control_until:
            self.forced_main_control_slot = None
            self.forced_main_control_until = 0.0
        for token in self.ult_order:
            if not self._slot_available(token):
                continue
            profiles = self.store.profiles(self.team[int(token) - 1], "ult")
            if self._ready(profiles, slot=token, kind="ult") and self.task._find_battle_ult("ult_" + token):
                quote = self.damage_quotes.get(self.team[int(token) - 1])
                rate = quote.ult / max(max(p.actionable, 0.3) for p in profiles) if quote else 0
                ready_ults.append((rate, token, profiles))
        ready_ults.sort(key=lambda item: -item[0])
        for _rate, token, profiles in ready_ults:
            started = self._clock()
            if not self._stage_combat_action(token, "ult", profiles, started, energy_ready=True):
                continue
            self._arm_action_feedback()
            if self.task.use_ult(ult_sequence=token, wait_for_team_recovery=True):
                self._note_action_attempt("ult", token)
                ended = self._clock()
                if self.combat_runtime is not None:
                    self.combat_runtime.confirm(token, "ult", ended)
                self._observe_bonus("ult", token)
                self._observe_phase_action(token, "ult")
                self._set_cooldowns(profiles, started)
                self._activate_state(token, self.ult_state_specs.get(token), "终结技", started)
                self._after_ultimate_mechanic(token, ended)
                self._clear_active(ended)
                self.task.log_info(f"时间排轴: 终结技 {token} 动画结束后继续，HUD 动画锁 {ended - started:.2f}s")

                post_ult_sp = self._sample_sp(force=True)
                self._observe_phase_sp(post_ult_sp)
                if self.forced_battle_token is not None:
                    forced = self.forced_battle_token
                    if self._slot_available(forced) and self._try_battle_token(
                        forced,
                        post_ult_sp,
                        advance_cursor=False,
                    ):
                        self.task.log_info(f"时间排轴: 终结技恢复插入机制战技 {forced}")
                        return
                if self._try_overflow_battle_skill(post_ult_sp, "终结技恢复"):
                    return
                return
            if self.combat_runtime is not None:
                self.combat_runtime.cancel(token, "ult")

        if self._try_planned_battle_skill(sp):
            return
        self._hold(True)

    def run(self, start_sleep=None, no_battle=False, deadline=None):
        task = self.task
        task.exit_check_count = 0
        reset_enemy_presence_probe(task)
        task.mouse_up(key="left")
        try:
            if self.store is None:
                self.store = load_skill_timings()
            task.log_info("技能时间排轴接管战斗策略")
            entered = task.active_time()
            ready_at = entered + max(0, start_sleep or 0)
            next_exit, next_team, next_lock = entered, entered, entered
            next_team_refresh = entered
            next_normal_attack = entered
            exit_pending = False
            while True:
                now = task.active_time()
                if deadline is not None and now >= deadline:
                    return False
                task.next_frame()
                if now >= next_exit:
                    next_exit = now + 0.5
                    condition = task._check_single_exit_condition()
                    exit_pending = bool(condition)
                    if task.is_combat_ended(condition):
                        task._recommend_detector_in_combat = False
                        task.log_info("自动战斗结束!", notify=task.get_battle_config("完成通知"))
                        return True
                if exit_pending:
                    self._hold(False)
                    task.sleep(0.1)
                    continue
                if no_battle:
                    self._hold(False)
                    task.sleep(0.1)
                    continue
                if self._enemy_operation_paused():
                    force_normal_attack = now >= next_normal_attack
                    self._hold(True, force=force_normal_attack)
                    if force_normal_attack:
                        next_normal_attack = now + self._NORMAL_ATTACK_REASSERT_INTERVAL
                    if now >= next_lock:
                        next_lock = now + 1
                        task.click(key="middle")
                    task.sleep(0.05)
                    continue
                force_normal_attack = now >= next_normal_attack
                self._hold(True, force=force_normal_attack)
                if force_normal_attack:
                    next_normal_attack = now + self._NORMAL_ATTACK_REASSERT_INTERVAL
                if now < ready_at:
                    task.sleep(0.1)
                    continue
                if not self.team and now >= next_team:
                    next_team = now + 1
                    detect_deadline = min(now + 0.3, deadline) if deadline is not None else now + 0.3
                    self._detect_team(detect_deadline)
                    next_team_refresh = task.active_time() + 1
                elif self.team and now >= next_team_refresh:
                    next_team_refresh = now + 1
                    refresh_deadline = min(now + 0.2, deadline) if deadline is not None else now + 0.2
                    self._refresh_team_slots(refresh_deadline)
                if deadline is not None and task.active_time() >= deadline:
                    return False
                self.step()
                if self._allowed() and task.active_time() >= next_lock:
                    next_lock = task.active_time() + 1
                    task.click(key="middle")
                remaining = deadline - task.active_time() if deadline is not None else 0.05
                if remaining > 0:
                    task.sleep(min(0.05, remaining))
        except (OSError, ValueError, KeyError) as exc:
            task.log_warning(f"技能时间排轴停止: {exc}")
            return False
        finally:
            task.mouse_up(key="left")
            self._holding = False
