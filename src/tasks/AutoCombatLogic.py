import time
import threading
import pyautogui
from src.tasks.BaseEfTask import BaseEfTask


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

    def _sync_normal_attack_hold(self):
        if self._normal_attack_hold_enabled:
            self.task.active_and_send_mouse_delta(activate=True,only_activate=True)
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
        task.send_key(skill_key)
        task.log_info(f"Used skill {skill_key}")
        self.normal_skill_index += 1

    def run(self, start_sleep: float = None, no_battle: bool = False):
        self._last_exit_check_time = 0
        self._exit_check_interval = 0.5
        task = self.task
        if not task.in_combat(required_yellow=1):
            now = time.time()
            last = getattr(task, '_last_no_combat_log_time', 0)
            if now - last >= 5:
                task.log_info("未检测到战斗状态,退出自动战斗")
                task._last_no_combat_log_time = now
            task.sleep(0.5)
            return False

        # 初始化普通战斗配置属性（排轴与普通模式共用）
        self.normal_skill_sequence = task.config.get("技能释放", ["1", "2", "3"])
        self.normal_start_trigger = task.config.get("启动技能点数", 2)
        self.normal_skill_index = 0

        self.rotation_enabled = False
        self.rotation_active = True
        self.rotation_enabled = self.task.config.get("启用排轴", False)
        if self.rotation_enabled:
            skill_sequence_config = self.task.config.get("排轴序列", "")
            self.task.log_info(f"排轴已启用，排轴序列配置: '{skill_sequence_config}'")
            self.skill_sequence = self.task._parse_skill_sequence(skill_sequence_config)
            self.skill_index = 0
            if not self.skill_sequence:
                self.rotation_active = False
            self.task.log_info(f"解析后的排轴技能序列: {self.skill_sequence}")
            self.last_rotation_ok_time = time.time()

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
                time.sleep(start_sleep)
            else:
                wait_time = task.config.get("进入战斗后的初始等待时间", 3)
                time.sleep(wait_time)

        try:
            while True:
                self._sync_normal_attack_hold()

                now = time.time()
                if now - self._last_exit_check_time >= self._exit_check_interval:
                    self._last_exit_check_time = now

                    if task._check_single_exit_condition():
                        if task.debug:
                            task.screenshot("out_of_combat")
                        task.log_info("自动战斗结束!", notify=task.config.get("后台结束战斗通知") and task.in_bg())
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
                if not self.rotation_enabled or not self.rotation_active:
                    self._do_normal_combat_frame()
                else:
                    if time.time() - self.last_rotation_ok_time >= 5:
                        self.rotation_active = False
                        task.log_info("排轴超时，切换为普通模式")
                    now_skill = self.skill_sequence[self.skill_index]
                    if now_skill.startswith("ult_"):
                        ult_sequence = now_skill[4:]
                        if task.use_ult(ult_sequence=ult_sequence):
                            task.log_info(f"排轴释放终极技 {now_skill}")
                            self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                            self.last_rotation_ok_time = time.time()
                            continue
                    elif now_skill.startswith("sleep_"):
                        sleep_time = float(now_skill[6:])
                        task.log_info(f"排轴等待 {sleep_time} 秒")
                        time.sleep(sleep_time)
                        self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                        self.last_rotation_ok_time = time.time()
                        continue
                    elif now_skill.startswith("normal_"):
                        normal_duration = float(now_skill[7:])
                        task.log_info(f"排轴临时切换普通战斗 {normal_duration} 秒")
                        self.normal_skill_index = 0
                        normal_end_time = time.time() + normal_duration
                        while time.time() < normal_end_time:
                            self._sync_normal_attack_hold()
                            now_check = time.time()
                            if now_check - self._last_exit_check_time >= self._exit_check_interval:
                                self._last_exit_check_time = now_check
                                if task._check_single_exit_condition():
                                    self._end = True
                                    break
                            self._do_normal_combat_frame()
                        if getattr(self, "_end", False):
                            break
                        task.log_info("普通战斗临时模式结束，恢复排轴")
                        self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                        self.last_rotation_ok_time = time.time()
                        continue
                    elif now_skill == 'e':
                        if task.use_link_skill():
                            task.log_info(f"排轴释放连携技 {now_skill}")
                            self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                            self.last_rotation_ok_time = time.time()
                            continue
                    else:
                        if task.get_skill_bar_count() >= 1:
                            task.send_key(now_skill)  # 确认使用send_key：技能键为游戏固定不可配置键，不经过KeyConfigManager管理
                            task.log_info(f"排轴释放技能 {now_skill}")
                            self.skill_index = (self.skill_index + 1) % len(self.skill_sequence)
                            self.last_rotation_ok_time = time.time()
                            continue
        finally:
            self._normal_attack_hold_enabled = False
            self._sync_normal_attack_hold()
        return True
