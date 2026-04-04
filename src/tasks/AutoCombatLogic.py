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

    def run(self, start_sleep: float = None, no_battle: bool = False):
        task = self.task
        if not task.in_combat(required_yellow=1):
            task.log_info("未检测到战斗状态,退出自动战斗")
            task.sleep(0.5)
            return False
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

            raw_skill_config = task.config.get("技能释放", "123")
            start_trigger_count = task.config.get("启动技能点数", 2)
            skill_sequence = task._parse_skill_sequence(raw_skill_config)

            task.log_info(f"战斗配置: 技能序列={skill_sequence}, 启动点数={start_trigger_count}")

            if task.debug:
                task.screenshot("enter_combat")
            task.active_and_send_mouse_delta(activate=True, only_activate=True)
            task.sleep(0.1)
            task.click(key="middle")

            pyautogui.mouseDown()
            if start_sleep is not None:
                time.sleep(start_sleep)
            else:
                wait_time = task.config.get("进入战斗后的初始等待时间", 3)
                time.sleep(wait_time)

        while True:
            if task._check_single_exit_condition():
                if task.debug:
                    task.screenshot("out_of_combat")
                task.log_info("自动战斗结束!", notify=task.config.get("后台结束战斗通知") and task.in_bg())
                task.log_info("退出战斗主循环")
                self._end = True
                pyautogui.mouseUp()
                break
            if no_battle:
                task.sleep(0.5)
                continue
            task.approach_enemy()
            if not self.rotation_enabled or not self.rotation_active:
                if task.use_link_skill() or task.use_ult():
                    continue

                skill_count = task.get_skill_bar_count()

                if skill_count >= start_trigger_count:
                    for skill_key in skill_sequence:

                        if not task.in_combat():
                            break

                        while True:
                            current_points = task.get_skill_bar_count()
                            time_since_last_skill = time.time() - task.last_skill_time

                            if current_points >= 1 and time_since_last_skill >= 1.0:
                                break

                            if task.use_link_skill() or task.use_ult():
                                continue

                            if current_points < 0 and (task.ocr_lv() or not task.in_team()):
                                break

                            task.approach_enemy()
                            time.sleep(0.05)

                        if not task.in_combat():
                            break

                        task.send_key(skill_key)  # 确认使用send_key：技能键为游戏固定不可配置键，不经过KeyConfigManager管理
                        task.last_skill_time = time.time()
                        task.last_op_time = time.time()

                        task.log_info(f"Used skill {skill_key}")
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
        return True
