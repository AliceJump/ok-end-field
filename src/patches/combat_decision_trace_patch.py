from __future__ import annotations

_PATCH_INSTALLED = False


def _task_trace_enabled(task) -> bool:
    try:
        from src.core.BattleConfig import KEY_TIMING_DECISION_WINDOW, KEY_TIMING_ROTATION

        return bool(
            task.get_battle_config(KEY_TIMING_ROTATION, False)
            and task.get_battle_config(KEY_TIMING_DECISION_WINDOW, False)
        )
    except Exception:
        return False


def _actor_for_token(logic, token) -> str:
    try:
        index = int(token) - 1
        name = logic.team[index]
        if name and name != "?":
            return name
    except (TypeError, ValueError, IndexError):
        pass
    return "未知角色"


def _task_actor_for_sequence(task, sequence) -> str:
    team = getattr(task, "_battle_team", []) or []
    try:
        actor = team[int(sequence) - 1]
        if actor and actor != "?":
            return actor
    except (TypeError, ValueError, IndexError):
        pass
    return "未知角色"


def _action_name(kind: str | None) -> str:
    return {
        "battle": "战技",
        "ult": "终结技",
        "link": "连携技",
    }.get(kind, "技能动作")


def _publish(logic, actor, action, state, reason, detail="", *, dedupe_key=None):
    if not _task_trace_enabled(logic.task):
        return
    from src.gui.combat_decision_window import publish_combat_decision

    publish_combat_decision(
        actor,
        action,
        state,
        reason,
        detail,
        dedupe_key=dedupe_key,
    )


def _active_timing_detail(logic) -> tuple[str, str, float, float | None]:
    elapsed = max(0.0, logic._clock() - logic.started)
    actor = _actor_for_token(logic, logic.active_slot) if logic.active_slot is not None else "上一动作"
    action = _action_name(logic.active_kind)
    if not logic.active:
        return actor, action, elapsed, None
    required = max(profile.handoff for profile in logic.active)
    return actor, action, elapsed, required


def _effect_timing_sentence(logic) -> str:
    known = [profile.effect_start for profile in logic.active if profile.effect_start is not None]
    if not known:
        return "没有可靠的首次实际生效时间，因此使用较保守的可接续时间。"
    return f"技能时间数据中可确认的首次实际生效时间约为 {max(known):.3f}s。"


def _describe_battle_wait(logic, token, sp) -> tuple[str, str, str]:
    actor = _actor_for_token(logic, token)
    now = logic._clock()

    if not logic._slot_available(token):
        return actor, "跳过", "该角色当前不可用或尚未完成识别。"
    if now < logic.state_until.get(token, 0):
        return actor, "等待", "该角色仍处于会替换战技的特殊状态，暂不按普通战技处理。"
    retry_at = logic.battle_retry_after.get(token, 0)
    if now < retry_at:
        return actor, "等待", f"上一轮释放没有确认成功，约 {retry_at - now:.2f}s 后再尝试。"
    if logic.forced_main_control_slot == token and now < logic.forced_main_control_until:
        return actor, "等待", "该角色当前需要保留主控普攻窗口，暂时不释放自身战技。"

    profiles, sp_gate, expected_cost = logic._battle_context(token)
    if not profiles:
        return actor, "跳过", "没有找到该角色战技的可用时间数据。"
    if sp_gate is None or expected_cost is None:
        return actor, "等待", "当前战技的技力门槛或预计消耗还不能可靠确定。"
    if sp < 0:
        return actor, "等待", "当前技力还没有可靠识别结果。"
    if sp < sp_gate:
        return actor, "等待", f"当前技力约 {sp:.1f} SP，至少需要 {sp_gate:g} SP 才会尝试释放。"

    allowed = logic._allowed(profiles, candidate_slot=token, candidate_kind="battle")
    if not allowed:
        previous_actor, previous_action, elapsed, required = _active_timing_detail(logic)
        if required is not None:
            return (
                actor,
                "等待",
                f"{previous_actor}的{previous_action}还没到允许下一动作接入的时间点"
                f"（已执行 {elapsed:.3f}s / 至少需要 {required:.3f}s）。",
            )
        return actor, "等待", "上一动作仍未到可以安全接续下一技能的时间点。"

    cooldown_left = max((logic.cooldowns.get(profile.skill_id, 0) - now for profile in profiles), default=0.0)
    if cooldown_left > 0:
        return actor, "等待", f"该角色战技仍在冷却，约还需 {cooldown_left:.2f}s。"

    if not logic.phase_planner.can_spend(token, "battle", sp, expected_cost):
        return actor, "等待", "当前阶段正在为后续动作保留技力，所以暂不花费这次战技消耗。"

    return (
        actor,
        "准备释放",
        f"当前技力约 {sp:.1f} SP，已达到 {sp_gate:g} SP 门槛；"
        "技能冷却和动作衔接时间也允许，现在准备尝试该角色战技。",
    )


def install_combat_decision_trace_patch():
    """Install read-only observation hooks around the timing scheduler.

    The wrappers never change a return value, input order, sleep duration or key
    operation. They only translate existing scheduler state into user-facing text.
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from src.data.combat_observation import ActionBlockReason
    from src.tasks.mixin.battle_mixin import BattleMixin
    from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic

    original_run = TimedCombatLogic.run
    original_step = TimedCombatLogic.step
    original_allowed = TimedCombatLogic._allowed
    original_try_battle_token = TimedCombatLogic._try_battle_token
    original_enemy_paused = TimedCombatLogic._enemy_operation_paused
    original_probe_feedback = TimedCombatLogic._probe_action_feedback
    original_link_ready = BattleMixin.is_link_skill_ready
    original_use_link = BattleMixin.use_link_skill
    original_use_ult = BattleMixin.use_ult

    def run_with_decision_window(self, *args, **kwargs):
        enabled = _task_trace_enabled(self.task)
        if enabled:
            from src.gui.combat_decision_window import clear_combat_decisions, show_combat_decision_window

            clear_combat_decisions()
            shown = show_combat_decision_window()
            _publish(
                self,
                "系统",
                "技能时间排轴",
                "开始",
                "实时决策展示已开启；这里只旁路观察现有判断，不改变任何技能调度。",
                "已打开独立 Qt 窗口。" if shown else "当前没有可用的 Qt 应用窗口，决策数据仍会在内存中记录。",
                dedupe_key=("session", "start"),
            )
        try:
            return original_run(self, *args, **kwargs)
        finally:
            if enabled:
                _publish(
                    self,
                    "系统",
                    "战斗调度",
                    "结束",
                    "本次时间排轴已经退出。",
                    dedupe_key=("session", "end"),
                )

    def allowed_with_link_reason(self, candidates=(), candidate_slot=None, candidate_kind=None):
        result = original_allowed(
            self,
            candidates,
            candidate_slot=candidate_slot,
            candidate_kind=candidate_kind,
        )
        if candidate_kind != "link" or not _task_trace_enabled(self.task):
            return result

        if self.pending is not None:
            before_sp, token, _expected_cost = self.pending
            actor = _actor_for_token(self, token)
            _publish(
                self,
                "未知角色",
                "连携技",
                "等待",
                f"{actor}的战技刚刚尝试释放，还在确认技力是否真的发生消耗；确认前不会插入连携。",
                f"释放前技力约 {before_sp:.1f} SP。",
                dedupe_key=("link", "pending-battle", token),
            )
            return result

        if not self.active:
            _publish(
                self,
                "未知角色",
                "连携技",
                "允许尝试",
                "当前没有尚未完成的技能动作，时间排轴本身允许检查连携是否可用。",
                "实际由哪名角色释放仍要由游戏当前的连携候选决定。",
                dedupe_key=("link", "no-active-action"),
            )
            return result

        actor, action, elapsed, required = _active_timing_detail(self)
        detail = _effect_timing_sentence(self)
        if required is not None:
            detail += f" 当前已执行 {elapsed:.3f}s；允许其他角色接手的时间点约为 {required:.3f}s。"
        if result:
            reason = f"{actor}的{action}已经进行到足够稳定的阶段，允许其他角色接手；现在可以继续检查连携提示。"
            state = "允许尝试"
            key = ("link", "cross-actor-ready", self.active_kind, self.active_slot)
        else:
            reason = f"{actor}的{action}还没进行到允许其他角色接手的时间点，所以即使连携即将可用也暂不插入。"
            state = "等待"
            key = ("link", "cross-actor-wait", self.active_kind, self.active_slot)
        _publish(self, "未知角色", "连携技", state, reason, detail, dedupe_key=key)
        return result

    def try_battle_token_with_reason(self, token, sp, overflow=False, advance_cursor=True):
        if _task_trace_enabled(self.task):
            actor, state, reason = _describe_battle_wait(self, token, sp)
            _publish(
                self,
                actor,
                "战技",
                state,
                reason,
                "这是当前调度器已有条件的解释，不会额外改变角色顺序或释放时机。",
                dedupe_key=("battle", token, state, reason.split("（", 1)[0]),
            )

        result = original_try_battle_token(
            self,
            token,
            sp,
            overflow=overflow,
            advance_cursor=advance_cursor,
        )
        if result and _task_trace_enabled(self.task):
            actor = _actor_for_token(self, token)
            if self.pending is not None and self.pending[1] == token:
                state = "等待确认"
                reason = "战技操作已经发送，正在通过技力变化确认这次释放是否真正成功。"
            else:
                state = "已执行"
                reason = "战技操作已经发送，并已按当前的释放确认规则接受为成功。"
            _publish(
                self,
                actor,
                "战技",
                state,
                reason,
                dedupe_key=("battle", token, state),
            )
        return result

    def enemy_paused_with_reason(self):
        was_paused = self.enemy_pause_started is not None
        result = original_enemy_paused(self)
        if _task_trace_enabled(self.task):
            if result:
                _publish(
                    self,
                    "当前主控角色",
                    "持续普攻与索敌",
                    "等待敌人",
                    "战斗界面仍存在，但当前没有检测到可靠的敌人证据；暂停技能调度，同时继续普攻和中键索敌。",
                    "状态计时和技能冷却仍继续流逝。",
                    dedupe_key=("enemy", "absent"),
                )
            elif was_paused:
                _publish(
                    self,
                    "系统",
                    "技能调度",
                    "恢复",
                    "重新检测到敌人，恢复技能调度。",
                    dedupe_key=("enemy", "present-again"),
                )
        return result

    def probe_feedback_with_reason(self):
        attempt = self.last_action_attempt
        reason = original_probe_feedback(self)
        if reason is None or not _task_trace_enabled(self.task):
            return reason

        _time, kind, token = attempt if attempt is not None else (None, None, None)
        actor = _actor_for_token(self, token) if token is not None else "未知角色"
        action = _action_name(kind)
        if reason == ActionBlockReason.TOO_FAR:
            text = "游戏反馈目标距离过远，本次尚未开始的技能动作已取消，并进入距离恢复处理。"
        elif reason == ActionBlockReason.DURING_SKILL:
            text = "游戏反馈当前仍处于技能期间，暂时不能释放这个动作；取消未证实的时间轴并稍后重试。"
        else:
            text = "游戏反馈本次技能动作受到阻止，调度器已取消这次尚未确认的尝试。"
        _publish(
            self,
            actor,
            action,
            "执行受阻",
            text,
            dedupe_key=("blocked", kind, token, str(reason)),
        )
        return reason

    def step_with_idle_reason(self):
        if not _task_trace_enabled(self.task):
            return original_step(self)

        from src.gui.combat_decision_window import combat_decision_revision

        before = combat_decision_revision()
        result = original_step(self)
        if combat_decision_revision() == before:
            _publish(
                self,
                "当前主控角色",
                "持续普攻",
                "等待",
                "当前没有更高优先级且可以立即执行的技能动作，保持普攻并等待技力、冷却、连携提示或动作时间变化。",
                dedupe_key=("idle", "normal-attack"),
            )
        return result

    def link_ready_with_reason(task_self, *args, **kwargs):
        result = original_link_ready(task_self, *args, **kwargs)
        if _task_trace_enabled(task_self):
            from src.gui.combat_decision_window import publish_combat_decision

            if result:
                publish_combat_decision(
                    "未知角色",
                    "连携技",
                    "检测到可用",
                    "游戏界面已经出现可释放的连携提示；现有检测只能确认“有连携可按”，不能确认这次实际会由哪名角色执行。",
                    dedupe_key=("link", "ui-ready"),
                )
            else:
                publish_combat_decision(
                    "未知角色",
                    "连携技",
                    "等待提示",
                    "时间排轴已经允许检查连携，但游戏界面当前还没有检测到可释放的连携提示。",
                    dedupe_key=("link", "ui-not-ready"),
                )
        return result

    def use_link_with_reason(task_self, *args, **kwargs):
        result = original_use_link(task_self, *args, **kwargs)
        if result and _task_trace_enabled(task_self):
            from src.gui.combat_decision_window import publish_combat_decision

            publish_combat_decision(
                "未知角色",
                "连携技",
                "已发送操作",
                "游戏已显示可用连携提示，并且时间排轴允许其他角色接手，所以发送了一次连携操作。",
                "当前代码无法确认多个同时可用的连携中最终是哪名角色实际执行，因此这里明确保持为“未知角色”。",
                dedupe_key=("link", "pressed"),
            )
        return result

    def use_ult_with_reason(task_self, *args, **kwargs):
        if "ult_sequence" in kwargs:
            ult_sequence = kwargs["ult_sequence"]
        elif args:
            ult_sequence = args[0]
        else:
            ult_sequence = None

        if _task_trace_enabled(task_self) and ult_sequence is not None:
            from src.gui.combat_decision_window import publish_combat_decision

            actor = _task_actor_for_sequence(task_self, ult_sequence)
            publish_combat_decision(
                actor,
                "终结技",
                "准备释放",
                "该角色终结技已经显示为可用，技能时间限制也允许衔接；时间排轴现在准备执行它。",
                dedupe_key=("ult", ult_sequence, "ready"),
            )

        result = original_use_ult(task_self, *args, **kwargs)
        if result and _task_trace_enabled(task_self) and ult_sequence is not None:
            from src.gui.combat_decision_window import publish_combat_decision

            actor = _task_actor_for_sequence(task_self, ult_sequence)
            publish_combat_decision(
                actor,
                "终结技",
                "已执行",
                "终结技操作已经发送，并完成现有的动画与队伍界面恢复等待。",
                dedupe_key=("ult", ult_sequence, "done"),
            )
        return result

    TimedCombatLogic.run = run_with_decision_window
    TimedCombatLogic.step = step_with_idle_reason
    TimedCombatLogic._allowed = allowed_with_link_reason
    TimedCombatLogic._try_battle_token = try_battle_token_with_reason
    TimedCombatLogic._enemy_operation_paused = enemy_paused_with_reason
    TimedCombatLogic._probe_action_feedback = probe_feedback_with_reason
    BattleMixin.is_link_skill_ready = link_ready_with_reason
    BattleMixin.use_link_skill = use_link_with_reason
    BattleMixin.use_ult = use_ult_with_reason
    _PATCH_INSTALLED = True
