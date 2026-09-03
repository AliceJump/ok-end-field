import traceback

import pyautogui

from src.core.BaseEfTask import BaseEfTask
from src.core.BattleConfig import (
    KEY_COND_ENABLED,
    KEY_COND_SEQUENCE,
    KEY_INSTANT_LINK,
    KEY_INSTANT_ULT,
    KEY_SKILL_ALLOWLIST,
)
from src.core.rotation_ast import iter_actions, normalize_ast
from src.data.FeatureList import FeatureList as fL
from src.data.skill_allowlist import generate_skill_sequence
from src.image.recommend_skill_detector import get_recommend_skill_detector


class _TaskProbe:
    """CondProbe 适配器：把 task 的检测方法适配为 rotation_ast 的探针接口。"""

    def __init__(self, task: BaseEfTask):
        self._task = task

    def ult_available(self, n: int) -> bool:
        # 对应 battle_mixin.use_ult 内的 find_one("ult_" + n)
        return bool(self._task.find_one("ult_" + str(n)))

    def link_available(self) -> bool:
        # 对应 battle_mixin.use_link_skill 的检测参数
        return bool(
            self._task.find_one(
                fL.default_link_skill, threshold=0.7, vertical_variance=0.005, horizontal_variance=0.005
            )
        )

    def skill_count(self) -> int:
        return self._task.get_skill_bar_count()


class AutoCombatLogic:
    def __init__(self, task: BaseEfTask):
        self.rotation_active = None
        self.skill_sequence = None
        self.rotation_enabled = None
        self.task = task
        self._normal_attack_hold_enabled = False
        self.normal_skill_sequence: list = []
        self.normal_start_trigger: int = 2
        self.normal_skill_index: int = 0
        self._last_search_time = 0
        self._search_interval = 1.0
        # 实时条件状态
        self.cond_rotation_enabled = False
        self.cond_ast: list = []
        self._cond_iter = None
        self._cond_probe = None
        # 立即释放开关（本帧无动作时生效）
        self.instant_ult_enabled = False
        self.instant_link_enabled = False
        # 战技失败暂存：技力不足时保留 token 下帧重试，不推进生成器
        self._pending_skill_token = None
        self._pending_skill_frames = 0  # 已重试帧数

    _SKILL_RETRY_MAX_FRAMES = 5  # 技力不足最大等待帧数（≈0.5s）
    _LOW_RES_WARN_INTERVAL = 5.0  # 低分辨率未进入战斗时警告间隔（秒）
    _SECOND_EXIT_THRESHOLD = 1.0  # 进入战斗后该秒数内退出视为“秒退”（等同未进入战斗）
    _TEAM_DETECT_INTERVAL = 1.0  # 战斗主循环中的队伍识别间隔（秒）
    _TEAM_DETECT_MAX_ATTEMPTS = 6  # 战斗主循环中的队伍识别尝试上限

    def _sync_normal_attack_hold(self):
        if self._normal_attack_hold_enabled:
            self.task.active_and_send_mouse_delta(activate=True, only_activate=True)
            pyautogui.mouseDown()
        else:
            pyautogui.mouseUp()

    def _do_normal_combat_frame(self):
        """执行一帧普通战斗逻辑（非排轴模式 / normal_[n] 临时模式共用）。"""
        task = self.task
        if task.use_link_skill():
            return
        # 推荐技能：优先级仅次于连携技，高于终结技
        if task.use_recommend_skill():
            return
        if task.use_ult():
            return

        skill_count = task.get_skill_bar_count()
        if skill_count < self.normal_start_trigger:
            task.approach_enemy()
            task.next_frame()
            return

        if self.normal_skill_index >= len(self.normal_skill_sequence):
            self.normal_skill_index = 0

        current_points = task.get_skill_bar_count()
        if current_points < 1:
            if task.use_ult():
                return
            if current_points < 0 and (task.ocr_lv() or not task.in_team()):
                self.normal_skill_index = 0
                return
            task.approach_enemy()
            task.next_frame()
            return

        if not task.in_combat():
            return

        skill_key = self.normal_skill_sequence[self.normal_skill_index]
        task.send_key(skill_key)  # 确认使用send_key：技能键为游戏固定不可配置键，不经过KeyConfigManager管理
        task.log_info(f"Used skill {skill_key}")
        self.normal_skill_index += 1

    def _periodic_search(self):
        now = self.task.active_time()

        if now - self._last_search_time < 1:
            return

        self._last_search_time = now

        self.task.click(key="middle")

    def _exec_rotation_token(self, token: str, deadline):
        """执行单个排轴 token（普通排轴与实时条件共用）。

        Args:
            token: 动作 token（ult_N / sleep_N / normal_N / e / 1~4）。
            deadline: run() 的全局 deadline，供 normal_ 内嵌循环判断超时。

        Returns:
            tuple[bool, str]: (success, signal)。
                success —— 是否成功执行动作（普通排轴据此推进并刷新 5 秒计时；
                           实时条件不卡住，无论成败都取下一 token）。
                signal —— "" 正常 / "break" 战斗结束需跳出主循环 /
                          "return_false" deadline 到达需 return False。
        """
        task = self.task

        if token.startswith("ult_"):
            ult_sequence = token[4:]
            if task.use_ult(ult_sequence=ult_sequence):
                task.log_info(f"释放终极技 {token}")
                return True, ""
            return False, ""

        if token.startswith("sleep_"):
            sleep_time = float(token[6:])
            if deadline is not None:
                remaining = deadline - task.active_time()
                if remaining <= 0:
                    task.log_info("自动战斗达到最大等待时间")
                    return True, "return_false"
                if sleep_time > remaining:
                    task.log_info(f"等待被截断至截止时间（原 {sleep_time:.3f} 秒，剩余 {remaining:.3f} 秒）")
                    task.sleep(remaining)
                    task.log_info("自动战斗达到最大等待时间")
                    return True, "return_false"
            task.log_info(f"等待 {sleep_time:.3f} 秒")
            task.sleep(sleep_time)
            return True, ""

        if token.startswith("normal_"):
            normal_duration = float(token[7:])
            task.log_info(f"临时切换普通战斗 {normal_duration:.3f} 秒")
            self.normal_skill_index = 0
            normal_end_time = task.active_time() + normal_duration
            while task.active_time() < normal_end_time:
                if deadline is not None and task.active_time() >= deadline:
                    task.log_info("自动战斗达到最大等待时间")
                    return True, "return_false"
                self._periodic_search()
                self._sync_normal_attack_hold()
                now_check = task.active_time()
                if now_check - self._last_exit_check_time >= self._exit_check_interval:
                    self._last_exit_check_time = now_check
                    if task._check_single_exit_condition():
                        self._end = True
                        return True, "break"
                task.approach_enemy()
                task.next_frame()
                self._do_normal_combat_frame()
            task.log_info("普通战斗临时模式结束")
            return True, ""

        if token == "e":
            if task.use_link_skill():
                task.log_info(f"释放连携技 {token}")
                return True, ""
            return False, ""

        # 数字战技 1/2/3/4
        if task.get_skill_bar_count() >= 1:
            task.send_key(token)  # 确认使用send_key：技能键为游戏固定不可配置键，不经过KeyConfigManager管理
            task.log_info(f"释放技能 {token}")
            return True, ""
        return False, ""

    def _do_conditional_rotation_step(self, deadline) -> tuple[str, bool]:
        """执行实时条件的一个动作 token。

        - 生成器耗尽（一轮遍历完）→ 重建（新一轮，重新求值所有条件）。
        - 战技 digit token 失败（技力不足）→ 暂存，下帧重试同一 token。
        - 其他 token（ult/e/sleep/normal）失败即跳过，不重试。
        - 无超时回退：持续运行至战斗结束。

        Returns:
            tuple[signal, had_action]:
                signal —— "" 正常 / "break" / "return_false"（来自 normal_ 内嵌循环）。
                had_action —— 本帧是否产出了条件动作（供立即释放判断）。
        """
        # 战技重试：上一帧 digit token 因技力不足失败，本帧重试同一 token（上限 15 帧）
        if self._pending_skill_token is not None:
            token = self._pending_skill_token
            self._pending_skill_frames += 1
            if self._pending_skill_frames >= self._SKILL_RETRY_MAX_FRAMES:
                self.task.log_info(f"技力不足超时 {self._pending_skill_frames} 帧，跳过战技 {token}")
                self._pending_skill_token = None
                self._pending_skill_frames = 0
                return "", False

            self._pending_skill_token = None  # 先清掉，若仍失败下面会重设
            success, signal = self._exec_rotation_token(token, deadline)
            if not success and signal == "":
                # 仍然技力不足，继续暂存等待下帧
                self._pending_skill_token = token
                return "", True  # had_action=True 阻断立即释放
            self._pending_skill_frames = 0
            return signal, True

        if self._cond_iter is None:
            self._cond_probe = _TaskProbe(self.task)
            self._cond_iter = iter_actions(self.cond_ast, self._cond_probe)

        try:
            token = next(self._cond_iter)
        except StopIteration:
            # 新一轮：重新求值（场上状态可能已变）；本帧不动作，下帧再取
            self._cond_iter = iter_actions(self.cond_ast, self._cond_probe)
            return "", False

        success, signal = self._exec_rotation_token(token, deadline)
        # 战技失败且非 fatal signal → 暂存重试，不推进生成器
        if not success and signal == "" and token.isdigit():
            self._pending_skill_token = token
            self._pending_skill_frames = 0
        return signal, True

    def _do_instant_release(self):
        """本帧无条件动作时，按开关尝试立即释放终结技 / 连携技。

        优先级：终结技 > 连携技。与 _do_normal_combat_frame 一致使用无参检测+释放。
        """
        task = self.task
        if self.instant_ult_enabled and task.use_ult():
            task.log_info("立即释放终结技")
            return
        if self.instant_link_enabled and task.use_link_skill():
            task.log_info("立即释放连携技")
            return

    def _is_low_resolution(self) -> bool:
        """当前画面分辨率是否低于 1080p。"""
        try:
            height = self.task.height
        except (AttributeError, TypeError):
            return False
        return bool(height) and height < 1080

    def _warn_low_resolution_if_due(self):
        """分辨率低于 1080p 且处于“未进入战斗”（含秒退）状态时，每 5 秒警告一次。

        计时状态挂在 task 上，保证触发式调度（AutoCombatTask）与 auto_battle 循环
        （每次新建本实例）都能跨次持续计时。
        """
        task = self.task
        if not self._is_low_resolution():
            return
        now = task.active_time()
        last_warn = getattr(task, "_last_low_res_warn_time", 0.0)
        if now - last_warn >= self._LOW_RES_WARN_INTERVAL:
            task._last_low_res_warn_time = now
            task.log_warning("1080p以下自动战斗匹配不良，请切换1080p以及以上分辨率", notify=True)

    def run(self, start_sleep: float = None, no_battle: bool = False, deadline: float = None):
        self._last_exit_check_time = 0
        self._exit_check_interval = 0.5
        self._last_team_detect_time = 0
        self._team_detect_attempts = 0
        task = self.task
        task._battle_team=None
        if not task.in_combat(required_yellow=1):
            # 非战斗状态：清标记，下次进入战斗时才会复位推荐技能检测器
            task._recommend_detector_in_combat = False
            now = task.active_time()
            last = getattr(task, "_last_no_combat_log_time", 0)
            if now - last >= 5:
                task._last_no_combat_log_time = now
            # 一直未进入战斗：分辨率低于 1080p 时每 5 秒警告一次
            self._warn_low_resolution_if_due()
            task.sleep(0.5)
            return False

        # 已确认进入战斗，记录进入时刻（用于“秒退”判定）
        combat_enter_time = task.active_time()

        # 非战斗 → 战斗 转换时复位推荐技能检测器，每场战斗仅一次：
        # 上一场结束时可能残留 active 标签（战斗外不调用 detect，不会自复位），
        # 新战斗首个白圈周期才能重新产生上升沿。用 task 上的标记保证
        # auto_battle 在同一场战斗内重入 run() 时不再复位，避免同周期白圈
        # 因 active 被清空而重复触发。
        if not getattr(task, "_recommend_detector_in_combat", False):
            get_recommend_skill_detector().reset()
            task._recommend_detector_in_combat = True

        # 初始化普通战斗配置属性（排轴与普通模式共用）
        self.normal_skill_sequence = task.get_battle_config("技能释放", ["1", "2", "3"])
        self.normal_start_trigger = task.get_battle_config("启动技能点数", 2)
        self.normal_skill_index = 0

        # ── 自动技能列表：标记是否需要后续处理 ──
        _skill_allowlist_enabled = task.get_battle_config(KEY_SKILL_ALLOWLIST, False)

        # 模式初始化：实时条件 > 排轴 > 普通
        # 实时条件优先：启用时自动忽略普通排轴
        self.cond_rotation_enabled = task.get_battle_config(KEY_COND_ENABLED, False)
        self.rotation_enabled = False
        self.rotation_active = True
        self._cond_iter = None
        self._cond_probe = None
        self._pending_skill_token = None

        if self.cond_rotation_enabled:
            raw_ast = task.get_battle_config(KEY_COND_SEQUENCE, [])
            self.cond_ast, warnings = normalize_ast(raw_ast)
            for w in warnings:
                task.log_info(f"实时条件配置警告: {w}")
            if not self.cond_ast:
                task.log_info("实时条件序列为空或全部非法，回退普通模式")
                self.cond_rotation_enabled = False

        # 立即释放开关（仅在实时条件启用时生效）
        self.instant_ult_enabled = self.cond_rotation_enabled and task.get_battle_config(KEY_INSTANT_ULT, False)
        self.instant_link_enabled = self.cond_rotation_enabled and task.get_battle_config(KEY_INSTANT_LINK, False)

        if self.cond_rotation_enabled:
            task.log_info(f"实时条件已启用，AST 节点数={len(self.cond_ast)}（忽略普通排轴）")
            if self.instant_ult_enabled:
                task.log_info("立即释放终结技 已启用")
            if self.instant_link_enabled:
                task.log_info("立即释放连携技 已启用")
        else:
            self.rotation_enabled = task.get_battle_config("启用排轴", False)
            if self.rotation_enabled:
                skill_sequence_config = task.get_battle_config("排轴序列", "")
                task.log_info(f"排轴已启用，排轴序列配置: '{skill_sequence_config}'")
                self.skill_sequence = self.task._parse_skill_sequence(skill_sequence_config)
                self.skill_index = 0
                if not self.skill_sequence:
                    self.rotation_active = False
                self.task.log_info(f"解析后的排轴技能序列: {self.skill_sequence}")
                self.last_rotation_ok_time = task.active_time()

        if not no_battle:
            task.log_info("检测到进入战斗,开始自动战斗流程")
            task.log_info(f"战斗配置: 技能序列={self.normal_skill_sequence}, 启动点数={self.normal_start_trigger}")

            if task.debug:
                task.screenshot("enter_combat")
            task.active_and_send_mouse_delta(activate=True, only_activate=True)
            task.sleep(0.1)
            task.click(key="middle")
            self._normal_attack_hold_enabled = True
            self._sync_normal_attack_hold()

            # 初始等待期间持续尝试识别队伍，识别出就不再识别
            _target_sleep = (
                start_sleep if start_sleep is not None else task.get_battle_config("进入战斗后的初始等待时间", 3)
            )
            _sleep_end = task.active_time() + _target_sleep
            while task.active_time() < _sleep_end:
                # 已识别出队伍则跳出等待
                if getattr(task, '_battle_team', None):
                    break
                # 尝试识别
                if _skill_allowlist_enabled:
                    try:
                        team, stable = task.detect_team_stable(deadline=_sleep_end)
                        if stable and team and all(m != "?" for m in team):
                            skill_sequence = generate_skill_sequence(team)
                            task._battle_team, self.normal_skill_sequence = team, skill_sequence
                            task.log_info(f"初始等待期间识别到队伍: {team}")
                            task.log_info(f"自动技能列表已生成: {self.normal_skill_sequence}")
                            break
                    except Exception as exc:
                        task._battle_team = None
                        task.log_info(f"队伍识别或自动技能列表生成失败: {exc}")
                retry_delay = min(0.2, _sleep_end - task.active_time())
                if retry_delay > 0:
                    task.sleep(retry_delay)

            # 剩余等待时间
            remaining = _sleep_end - task.active_time()
            if remaining > 0:
                task.sleep(remaining)

        try:
            while True:
                if deadline is not None and task.active_time() >= deadline:
                    task.log_info("自动战斗达到最大等待时间")
                    return False
                self._periodic_search()
                self._sync_normal_attack_hold()

                now = task.active_time()
                if now - self._last_exit_check_time >= self._exit_check_interval:
                    self._last_exit_check_time = now

                    exit_condition = task._check_single_exit_condition()
                    if exit_condition:
                        if task.debug:
                            task.screenshot("out_of_combat")
                        if task.is_combat_ended(exit_condition):
                            # 秒退：进入战斗后很快退出，同样视为未进入战斗，低分辨率警告继续
                            if task.active_time() - combat_enter_time < self._SECOND_EXIT_THRESHOLD:
                                self._warn_low_resolution_if_due()
                            task.log_info("自动战斗结束!", notify=task.get_battle_config("完成通知"))
                            task.log_info("退出战斗主循环")
                            self._end = True
                            self._normal_attack_hold_enabled = False
                            self._sync_normal_attack_hold()
                            # 战斗结束确认：清战斗标记，下次进入战斗（含结算未出现时
                            # auto_battle 直接重入的新战斗）才会复位推荐技能检测器
                            task._recommend_detector_in_combat = False
                            break
                if no_battle:
                    self._normal_attack_hold_enabled = False
                    self._sync_normal_attack_hold()
                    task.sleep(0.5)
                    continue

                # 按间隔尝试稳定识别队伍，识别成功或达到尝试上限后停止扫描
                team_detect_due = now - self._last_team_detect_time >= self._TEAM_DETECT_INTERVAL
                team_detect_available = self._team_detect_attempts < self._TEAM_DETECT_MAX_ATTEMPTS
                if (
                    not getattr(task, '_battle_team', None)
                    and _skill_allowlist_enabled
                    and team_detect_due
                    and team_detect_available
                ):
                    self._team_detect_attempts += 1
                    try:
                        team, stable = task.detect_team_stable()
                        if stable and team and all(m != "?" for m in team):
                            skill_sequence = generate_skill_sequence(team)
                            task._battle_team, self.normal_skill_sequence = team, skill_sequence
                            task.log_info(f"战斗中识别到队伍: {team}")
                            task.log_info(f"自动技能列表已生成: {self.normal_skill_sequence}")
                    except Exception as exc:
                        task._battle_team = None
                        task.log_info(f"队伍识别或自动技能列表生成失败: {exc}")
                    finally:
                        self._last_team_detect_time = task.active_time()

                task.approach_enemy()
                task.next_frame()

                # ── 模式分发：实时条件 > 普通排轴 > 普通 ──────────────
                if self.cond_rotation_enabled:
                    signal, had_action = self._do_conditional_rotation_step(deadline)
                    if signal == "return_false":
                        return False
                    if signal == "break":
                        break
                    # 本帧无条件动作产出时，按开关尝试立即释放终结技 / 连携技
                    if not had_action:
                        self._do_instant_release()
                elif self.rotation_enabled and self.rotation_active:
                    if task.active_time() - self.last_rotation_ok_time >= 5:
                        self.rotation_active = False
                        task.log_info("排轴超时，切换为普通模式")
                    now_skill = self.skill_sequence[self.skill_index]
                    success, signal = self._exec_rotation_token(now_skill, deadline)
                    if signal == "return_false":
                        return False
                    if signal == "break":
                        break
                    if success:
                        self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                        self.last_rotation_ok_time = task.active_time()
                else:
                    self._do_normal_combat_frame()
        except Exception as exc:
            task.log_info(f"自动战斗发生异常: {exc}")
            task.log_info(traceback.format_exc())
        finally:
            self._normal_attack_hold_enabled = False
            self._sync_normal_attack_hold()
        return True
