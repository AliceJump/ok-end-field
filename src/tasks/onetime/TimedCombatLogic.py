"""Independent, conservative timeline scheduler using the existing battle monitors."""

from __future__ import annotations

from collections import deque

from src.data.skill_rotation import generate_damage_rotation
from src.data.skill_timing import SkillTiming, load_skill_timings
from src.data.timing_dps import build_options, load_damage_quotes, optimize_cycle


class TimedCombatLogic:
    _NORMAL_ATTACK_REASSERT_INTERVAL = 0.25
    _FULL_SKILL_POINTS = 3

    def __init__(self, task, store=None):
        self.task = task
        self.store = store
        self.team = []
        self.order = []
        self.ult_order = ["1", "2", "3", "4"]
        self.cursor = 0
        self.active: tuple[SkillTiming, ...] = ()
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

    def _allowed(self, candidates=()):
        elapsed = self.task.active_time() - self.started
        if self.unconfirmed:
            candidates = ()
        return self.pending is None and all(profile.allows(elapsed, candidates) for profile in self.active)

    def _ready(self, profiles):
        now = self.task.active_time()
        return (
            bool(profiles)
            and self._allowed(profiles)
            and all(now >= self.cooldowns.get(profile.skill_id, 0) for profile in profiles)
        )

    def _begin(self, profiles, started):
        self.active = profiles
        self.started = started
        self.unconfirmed = False

    def _confirm_battle(self, now):
        if self.pending is None:
            return
        before, token = self.pending
        points = self.task.get_skill_bar_count()
        if 0 <= points < before:
            self.pending = None
            self._observe_battle()
            self.cursor = (self.cursor + 1) % len(self.order)
            self._set_cooldowns()
            self.task.log_info(f"时间排轴: 战技 {token} 技力消耗已确认")
        elif now - self.started >= 0.8:
            self.pending = None
            self.unconfirmed = True
            # Keep the same feeder/consumer position and the complete timeline
            # guard. A sent key alone is not proof that the skill was accepted.
            self.task.log_info(f"时间排轴: 战技 {token} 未确认消耗，保护窗口结束后重试")

    def _set_cooldowns(self):
        for profile in self.active:
            self.cooldowns[profile.skill_id] = self.started + profile.cooldown

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
        elif all(name in self.damage_quotes for name in self.team):
            # Unknown owner: credit the lower bound, never four links for one key.
            self._cycle_bonus += min(self.damage_quotes[name].link for name in self.team)

    def _try_planned_battle_skill(self, points, overflow=False):
        token = self.order[self.cursor]
        profiles = self.store.profiles(self.team[int(token) - 1], "battle")
        costs = [profile.skill_points for profile in profiles]
        if not (self._ready(profiles) and None not in costs and points >= max(costs)):
            return False

        started = self.task.active_time()
        self.task.send_key(token)
        self._begin(profiles, started)
        if max(costs) > 0:
            self.pending = (points, token)
            if overflow:
                self.task.log_info(f"时间排轴: 技力已满，防溢出抢占尝试战技 {token}")
        else:
            # No resource transition exists for a zero-cost skill. This is
            # explicitly an attempt; use its timeline/CD to prevent spam.
            self._observe_battle()
            self.cursor = (self.cursor + 1) % len(self.order)
            self._set_cooldowns()
            self.task.log_info(f"时间排轴: 尝试零消耗战技 {token}")
        return True

    def step(self):
        """One refreshed HUD observation; no legacy strategy switches are read."""
        now = self.task.active_time()
        self._confirm_battle(now)
        if not self.team or not self.order:
            self._hold(True)
            return

        points = self.task.get_skill_bar_count()
        # Prevent full-SP starvation: once the HUD shows all three bars, the
        # next planned battle skill gets priority over link/ult as soon as the
        # current timeline permits it. This preserves order and never bypasses
        # exclusive/actionable guards.
        if points >= self._FULL_SKILL_POINTS and self._try_planned_battle_skill(points, overflow=True):
            return

        # The existing link detector cannot identify its owner. Protect the
        # longest possible team link timeline instead of inventing an owner.
        links = tuple(profile for name in self.team for profile in self.store.profiles(name, "link"))
        if (
            all(self.store.profiles(name, "link") for name in self.team)
            and links
            and self._allowed(links)
            and self.task.is_link_skill_ready()
        ):
            started = self.task.active_time()
            if self.task.use_link_skill():
                self._begin(links, started)
                self._observe_bonus("link")
                self.task.log_info("时间排轴: 连携技按现有就绪检测释放")
                return

        ready_ults = []
        for token in self.ult_order:
            profiles = self.store.profiles(self.team[int(token) - 1], "ult")
            if self._ready(profiles) and self.task._find_battle_ult("ult_" + token):
                quote = self.damage_quotes.get(self.team[int(token) - 1])
                rate = quote.ult / max(max(p.actionable, 0.3) for p in profiles) if quote else 0
                ready_ults.append((rate, token, profiles))
        # Sort simultaneously ready ults by their own damage/time. The existing
        # monitor remains authoritative and unknown utility skills still cast.
        ready_ults.sort(key=lambda item: -item[0])
        for _rate, token, profiles in ready_ults:
            started = self.task.active_time()
            if self.task.use_ult(ult_sequence=token, wait_for_team_recovery=False):
                self._begin(profiles, started)
                self._observe_bonus("ult", token)
                self._set_cooldowns()
                self.task.log_info(f"时间排轴: 终结技 {token} 按现有就绪检测释放")
                return

        if self._try_planned_battle_skill(points):
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
