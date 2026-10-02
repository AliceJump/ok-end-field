"""Independent, conservative timeline scheduler using the existing battle monitors."""

from __future__ import annotations

from collections import deque

from src.data.skill_rotation import generate_damage_rotation
from src.data.skill_timing import SkillTiming, load_skill_timings
from src.data.timing_dps import build_options, load_damage_quotes, optimize_cycle


class TimedCombatLogic:
    _NORMAL_ATTACK_REASSERT_INTERVAL = 0.25
    _FULL_SKILL_SP = 295.0
    _DEFAULT_ASSUME_SUCCESS_SP_THRESHOLD = 25.0
    _ASSUME_SUCCESS_PAUSE = 0.1
    _SP_ERROR_MARGIN = 5.0
    _NATURAL_SP_PER_SECOND = 8.0

    def __init__(self, task, store=None):
        self.task = task
        self.store = store
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

    def _allowed(self, candidates=(), candidate_slot=None, candidate_kind=None):
        if self.pending is not None:
            return False
        if not self.active:
            return True
        elapsed = self.task.active_time() - self.started
        if self.unconfirmed:
            return all(profile.allows(elapsed, ()) for profile in self.active)

        # Different operators can act once the previous skill has actually
        # started producing gameplay effects. Link ownership is unknown, but
        # its HUD readiness is authoritative enough after the current cast
        # commits. Same-operator continuation stays conservative.
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
        now = self.task.active_time()
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
        self.started = self.task.active_time() if now is None else now
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
            + (
                ""
                if spec.end_cooldown is None
                else f"，主动中止冷却约 {spec.end_cooldown:g}s"
            )
        )

    def _accept_battle_skill(self, token):
        self._observe_battle()
        self._set_cooldowns()
        self._activate_state(token, self.state_specs.get(token), "战技")
        self.cursor = (self.cursor + 1) % len(self.order)

    def _confirm_battle(self, now):
        if self.pending is None:
            return
        before_sp, token, expected_cost = self.pending
        current_sp = self.task.get_skill_bar_sp()
        elapsed = max(0.0, now - self.started)
        minimum_drop = max(
            2.0,
            expected_cost
            - self.assume_success_sp_threshold
            - self._NATURAL_SP_PER_SECOND * elapsed,
        )
        if current_sp >= 0 and before_sp - current_sp >= minimum_drop:
            self.pending = None
            self._accept_battle_skill(token)
            self.task.log_info(
                f"时间排轴: 战技 {token} 技力消耗已确认 "
                f"({before_sp:.1f}->{current_sp:.1f}, 阈值 {minimum_drop:.1f})"
            )
        elif now - self.started >= 0.8:
            self.pending = None
            self.unconfirmed = True
            # Keep the same feeder/consumer position and the complete timeline
            # guard. A sent key alone is not proof that the skill was accepted.
            self.task.log_info(f"时间排轴: 战技 {token} 未确认消耗，保护窗口结束后重试")

    def _set_cooldowns(self, profiles=None, started=None):
        profiles = self.active if profiles is None else profiles
        started = self.started if started is None else started
        for profile in profiles:
            self.cooldowns[profile.skill_id] = started + profile.cooldown

    def _slot_available(self, token):
        return token not in self.disabled_slots

    def _active_team_names(self):
        return [
            name
            for index, name in enumerate(self.team, 1)
            if str(index) not in self.disabled_slots and name != "?"
        ]

    def _refresh_sp_threshold(self):
        active_names = self._active_team_names()
        active_gains = {
            name: self.normal_attack_sp_gains.get(name)
            for name in active_names
        }
        known_gains = [value for value in active_gains.values() if value is not None]
        has_unknown = any(value is None for value in active_gains.values())
        fallback_gain = self.store.global_normal_attack_sp_gain() if active_names and has_unknown else None
        threshold_gain = max(
            known_gains + ([fallback_gain] if fallback_gain is not None else []),
            default=self._DEFAULT_ASSUME_SUCCESS_SP_THRESHOLD - self._SP_ERROR_MARGIN,
        )
        self.assume_success_sp_threshold = threshold_gain + self._SP_ERROR_MARGIN
        self.task.log_info(
            f"时间排轴技力确认阈值: 存活槽位重击回复 {active_gains}, "
            f"最大值 {threshold_gain:g} + 误差 {self._SP_ERROR_MARGIN:g} = "
            f"{self.assume_success_sp_threshold:g} SP"
        )

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
            if current != "?" and current != expected
        ]
        if mismatches:
            self.task.log_debug(f"时间排轴忽略槽位刷新，已知角色位置不匹配: {mismatches}")
            return

        newly_disabled = {
            str(index + 1)
            for index, (expected, current) in enumerate(zip(self.team, detected))
            if expected != "?" and current == "?" and str(index + 1) not in self.disabled_slots
        }
        if not newly_disabled:
            return

        self.disabled_slots.update(newly_disabled)
        self.task._battle_team_disabled_slots = {
            int(token) - 1 for token in self.disabled_slots
        }

        if self.pending is not None and self.pending[1] in newly_disabled:
            self.pending = None
            self.unconfirmed = False
        if self.active_slot in newly_disabled:
            self._clear_active()
        for token in newly_disabled:
            self.state_until.pop(token, None)

        self._refresh_sp_threshold()
        details = [
            f"{token}:{self.team[int(token) - 1]}"
            for token in sorted(newly_disabled, key=int)
        ]
        self.task.log_info(
            f"时间排轴屏蔽失效槽位 {details}，保留原始槽位编号，后续不再调度这些位置"
        )

    def _detect_team(self, deadline):
        team, stable = self.task.detect_team_stable(
            max_attempts=2,
            interval=0.1,
            confidence=2,
            deadline=deadline,
        )
        if stable and len(team) == 4 and "?" not in team:
            self.team = team
            self.task._battle_team = team
            self.ult_order = generate_damage_rotation(team)
            self.order = [token for token in self.ult_order if self.store.profiles(team[int(token) - 1], "battle")]
            self.task.log_info(f"时间排轴队伍: {team}, 战技顺序: {self.order}")

            self.disabled_slots.clear()
            self.task._battle_team_disabled_slots = set()
            self.normal_attack_sp_gains = self.store.team_normal_attack_sp_gains(team)
            self._refresh_sp_threshold()

            self.state_specs = {
                str(index + 1): self.store.battle_state(name)
                for index, name in enumerate(team)
            }
            self.ult_state_specs = {
                str(index + 1): self.store.ultimate_state(name)
                for index, name in enumerate(team)
            }
            for source, specs in (("战技", self.state_specs), ("终结技", self.ult_state_specs)):
                for token, spec in specs.items():
                    if spec is not None:
                        self.task.log_info(
                            f"时间排轴状态{source}: {team[int(token) - 1]}({token}) "
                            f"{spec.base_skill_id} -> {spec.end_skill_id}, "
                            + ", ".join(spec.evidence)
                        )

            self.damage_quotes = load_damage_quotes(team)
            self.plan = optimize_cycle(build_options(team, self.store, self.damage_quotes))
            if self.plan is not None:
                self.order = list(self.plan.slots)
                self.task.log_info(
                    f"时间排轴DPS规划: 战技 {self.order}, 可重复窗口 {self.plan.seconds:.2f}s, "
                    f"预计战技伤害 {self.plan.damage:.0f}, 战技增量DPS {self.plan.dps:.1f}"
                )
            else:
                self.task.log_info("时间排轴DPS规划: 数据或状态映射不足，保留原伤害顺序")

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
                # Unknown owner: credit the lower bound among currently active slots.
                self._cycle_bonus += min(self.damage_quotes[name].link for name in active_names)

    def _skip_active_state_slots(self):
        if not self.order:
            return
        now = self.task.active_time()
        for _ in range(len(self.order)):
            token = self.order[self.cursor]
            if self._slot_available(token) and now >= self.state_until.get(token, 0):
                return
            self.cursor = (self.cursor + 1) % len(self.order)

    def _try_planned_battle_skill(self, sp, overflow=False):
        token = self.order[self.cursor]
        if not self._slot_available(token) or self.task.active_time() < self.state_until.get(token, 0):
            return False

        profiles = self.store.profiles(self.team[int(token) - 1], "battle")
        point_costs = [profile.skill_points for profile in profiles]
        sp_costs = [profile.sp_cost for profile in profiles]
        max_sp_cost = None if None in sp_costs else max(sp_costs)
        if max_sp_cost is None:
            max_sp_cost = None if None in point_costs else max(point_costs) * 100.0
        if (
            max_sp_cost is None
            or sp < 0
            or sp < max_sp_cost
            or not self._ready(profiles, slot=token, kind="battle")
        ):
            return False

        started = self.task.active_time()
        self.task.send_key(token)
        self._begin(profiles, started, slot=token, kind="battle")

        if max_sp_cost <= self.assume_success_sp_threshold:
            # A combo finisher can refund enough SP to hide a small skill cost.
            # For costs no larger than the current team's largest possible
            # finisher refund plus 5 SP visual error, trust the accepted input.
            self.task.sleep(self._ASSUME_SUCCESS_PAUSE)
            self._accept_battle_skill(token)
            self.task.log_info(
                f"时间排轴: 战技 {token} 消耗 {max_sp_cost:g} SP <= "
                f"{self.assume_success_sp_threshold:g}，按键后直接视为成功"
            )
        elif max_sp_cost > 0:
            self.pending = (sp, token, max_sp_cost)
            if overflow:
                self.task.log_info(f"时间排轴: 技力已满，防溢出抢占尝试战技 {token}")
        else:
            self.task.sleep(self._ASSUME_SUCCESS_PAUSE)
            self._accept_battle_skill(token)
            self.task.log_info(f"时间排轴: 零消耗战技 {token} 按键后直接视为成功")
        return True

    def step(self):
        """One refreshed HUD observation; no legacy strategy switches are read."""
        now = self.task.active_time()
        self._confirm_battle(now)
        if not self.team or not self.order:
            self._hold(True)
            return

        self._skip_active_state_slots()
        sp = self.task.get_skill_bar_sp()
        # Prevent full-SP starvation: once the HUD is effectively full, the
        # next planned battle skill gets priority over link/ult as soon as the
        # current cast has committed. get_skill_bar_sp() already uses the
        # optimized 1+0 / n+1 / worst-case 3+1 staged bar scan.
        if sp >= self._FULL_SKILL_SP and self._try_planned_battle_skill(sp, overflow=True):
            return

        # The existing link detector cannot identify its owner. Protect the
        # longest possible team link timeline instead of inventing an owner.
        active_names = self._active_team_names()
        links = tuple(profile for name in active_names for profile in self.store.profiles(name, "link"))
        if (
            active_names
            and all(self.store.profiles(name, "link") for name in active_names)
            and links
            and self._allowed(links, candidate_kind="link")
            and self.task.is_link_skill_ready()
        ):
            started = self.task.active_time()
            if self.task.use_link_skill():
                self._begin(links, started, kind="link")
                self._observe_bonus("link")
                self.task.log_info("时间排轴: 连携技按现有就绪检测释放")
                return

        ready_ults = []
        for token in self.ult_order:
            if not self._slot_available(token):
                continue
            profiles = self.store.profiles(self.team[int(token) - 1], "ult")
            if self._ready(profiles, slot=token, kind="ult") and self.task._find_battle_ult("ult_" + token):
                quote = self.damage_quotes.get(self.team[int(token) - 1])
                rate = quote.ult / max(max(p.actionable, 0.3) for p in profiles) if quote else 0
                ready_ults.append((rate, token, profiles))
        # Sort simultaneously ready ults by their own damage/time. The existing
        # monitor remains authoritative and unknown utility skills still cast.
        ready_ults.sort(key=lambda item: -item[0])
        for _rate, token, profiles in ready_ults:
            started = self.task.active_time()
            if self.task.use_ult(ult_sequence=token, wait_for_team_recovery=True):
                ended = self.task.active_time()
                self._observe_bonus("ult", token)
                self._set_cooldowns(profiles, started)
                self._activate_state(token, self.ult_state_specs.get(token), "终结技", started)
                self._clear_active(ended)
                self.task.log_info(
                    f"时间排轴: 终结技 {token} 动画结束后继续，HUD 动画锁 {ended - started:.2f}s"
                )
                return

        if self._try_planned_battle_skill(sp):
            return
        self._hold(True)

    def run(self, start_sleep=None, no_battle=False, deadline=None):
        task = self.task
        task.exit_check_count = 0
        # Clear a possible held button from a previous run before any loading.
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
                    # Pass false as well, so an intervening valid HUD resets
                    # the existing consecutive-exit confirmation counter.
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
                # Timed mode only schedules skill handoffs. Reassert the normal
                # attack periodically because skill animations, focus changes or
                # the game itself may drop a previous synthetic LBUTTONDOWN while
                # our local state still says it is held.
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
                # Refresh monitors even through long channels. Do not call the
                # legacy approach_enemy dodge while a timeline is protected.
                remaining = deadline - task.active_time() if deadline is not None else 0.05
                if remaining > 0:
                    task.sleep(min(0.05, remaining))
        except (OSError, ValueError, KeyError) as exc:
            task.log_warning(f"技能时间排轴停止: {exc}")
            return False
        finally:
            task.mouse_up(key="left")
            self._holding = False
