import threading
import pyautogui
import traceback
from src.core.BaseEfTask import BaseEfTask
from src.core.rotation_ast import iter_actions, normalize_ast


class _TaskProbe:
    """CondProbe 适配器：把 task 的检测方法适配为 rotation_ast 的探针接口。"""

    def __init__(self, task: BaseEfTask):
        self._task = task

    def ult_available(self, n: int) -> bool:
        # 对应 battle_mixin.use_ult 内的 find_one("ult_" + n)
        return bool(self._task.find_one("ult_" + str(n)))

    def link_available(self) -> bool:
        # 对应 battle_mixin.use_link_skill 的检测参数
        return bool(self._task.find_one(
            "default_link_skill", threshold=0.7, vertical_variance=0.005, horizontal_variance=0.005
        ))

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
        # 条件排轴（实时条件）状态
        self.cond_rotation_enabled = False
        self.cond_ast: list = []
        self._cond_iter = None
        self._cond_probe = None

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
        """执行单个排轴 token（普通排轴与条件排轴共用）。

        Args:
            token: 动作 token（ult_N / sleep_N / normal_N / e / 1~4）。
            deadline: run() 的全局 deadline，供 normal_ 内嵌循环判断超时。

        Returns:
            tuple[bool, str]: (success, signal)。
                success —— 是否成功执行动作（普通排轴据此推进并刷新 5 秒计时；
                           条件排轴不卡住，无论成败都取下一 token）。
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

    def _do_conditional_rotation_step(self, deadline) -> str:
        """执行条件排轴的一个动作 token。

        - 生成器耗尽（一轮遍历完）→ 重建（新一轮，重新求值所有条件）。
        - 不卡住：动作失败即取下一 token（生成器 next）。
        - 无超时回退：持续运行至战斗结束。

        Returns:
            signal: "" 正常 / "break" / "return_false"（来自 normal_ 内嵌循环）。
        """
        if self._cond_iter is None:
            self._cond_probe = _TaskProbe(self.task)
            self._cond_iter = iter_actions(self.cond_ast, self._cond_probe)

        try:
            token = next(self._cond_iter)
        except StopIteration:
            # 新一轮：重新求值（场上状态可能已变）；本帧不动作，下帧再取
            self._cond_iter = iter_actions(self.cond_ast, self._cond_probe)
            return ""

        success, signal = self._exec_rotation_token(token, deadline)
        # 条件排轴：生成器已 next 即「推进」，不维护 last_rotation_ok_time，不超时回退
        return signal

    def run(self, start_sleep: float = None, no_battle: bool = False, deadline: float = None):
        self._last_exit_check_time = 0
        self._exit_check_interval = 0.5
        task = self.task
        if not task.in_combat(required_yellow=1):
            now = task.active_time()
            last = getattr(task, '_last_no_combat_log_time', 0)
            if now - last >= 5:
                task._last_no_combat_log_time = now
            task.sleep(0.5)
            return False

        # 初始化普通战斗配置属性（排轴与普通模式共用）
        self.normal_skill_sequence = task.get_battle_config("技能释放", ["1", "2", "3"])
        self.normal_start_trigger = task.get_battle_config("启动技能点数", 2)
        self.normal_skill_index = 0

        # ── 模式初始化：条件排轴 > 普通排轴 > 普通 ──────────────
        # 条件排轴优先：启用时自动忽略普通排轴
        self.cond_rotation_enabled = task.get_battle_config("启用条件排轴", False)
        self.rotation_enabled = False
        self.rotation_active = True
        self._cond_iter = None
        self._cond_probe = None

        if self.cond_rotation_enabled:
            raw_ast = task.get_battle_config("条件排轴序列", [])
            self.cond_ast, warnings = normalize_ast(raw_ast)
            for w in warnings:
                task.log_info(f"条件排轴配置警告: {w}")
            if not self.cond_ast:
                task.log_info("条件排轴序列为空或全部非法，回退普通模式")
                self.cond_rotation_enabled = False

        if self.cond_rotation_enabled:
            task.log_info(f"条件排轴已启用，AST 节点数={len(self.cond_ast)}（忽略普通排轴）")
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
            if start_sleep is not None:
                task.sleep(start_sleep)
            else:
                wait_time = task.get_battle_config("进入战斗后的初始等待时间", 3)
                task.sleep(wait_time)

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

                    if task._check_single_exit_condition():
                        if task.debug:
                            task.screenshot("out_of_combat")
                        if task.is_combat_ended():
                            task.log_info("自动战斗结束!", notify=task.get_battle_config("后台结束战斗通知") and task.in_bg())
                            task.log_info("退出战斗主循环")
                            self._end = True
                            self._normal_attack_hold_enabled = False
                            self._sync_normal_attack_hold()
                            break
                if no_battle:
                    self._normal_attack_hold_enabled = False
                    self._sync_normal_attack_hold()
                    task.sleep(0.5)
                    continue
                task.approach_enemy()
                task.next_frame()

                # ── 模式分发：条件排轴 > 普通排轴 > 普通 ──────────────
                if self.cond_rotation_enabled:
                    signal = self._do_conditional_rotation_step(deadline)
                    if signal == "return_false":
                        return False
                    if signal == "break":
                        break
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
