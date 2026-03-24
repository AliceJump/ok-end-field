import time
import threading
import pyautogui
from src.tasks.BaseEfTask import BaseEfTask


class AutoCombatLogic:
    def __init__(self, task: BaseEfTask):
        self.task = task

    def run(self, start_sleep: float = None, no_battle: bool = False):
        task = self.task
        if not task.in_combat(required_yellow=1):
            task.log_info("未检测到战斗状态,退出自动战斗")
            task.sleep(0.5)
            return False

        if not no_battle:
            task.log_info("检测到进入战斗,开始自动战斗流程")

            raw_skill_config = task.config.get("技能释放", "123")
            start_trigger_count = task.config.get("启动技能点数", 2)
            skill_sequence = task._parse_skill_sequence(raw_skill_config)

            task.log_info(f"战斗配置: 技能序列={skill_sequence}, 启动点数={start_trigger_count}")

            if task.debug:
                task.screenshot("enter_combat")

            task.click(key="middle")

            pyautogui.mouseDown()
            if start_sleep is not None:
                time.sleep(start_sleep)
            else:
                wait_time = task.config.get("进入战斗后的初始等待时间", 3)
                time.sleep(wait_time)

            while True:

                if task.is_combat_ended():
                    if task.debug:
                        task.screenshot("out_of_combat")

                    task.log_info("自动战斗结束!", notify=task.config.get("后台结束战斗通知") and task.in_bg())
                    task.log_info("退出战斗主循环")
                    self._end=True
                    pyautogui.mouseUp()
                    break

                task.handle_no_damage_number_actions()

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

                            task.handle_no_damage_number_actions()
                            time.sleep(0.05)

                        if not task.in_combat():
                            break

                        task.send_key(skill_key)
                        task.last_skill_time = time.time()
                        task.last_op_time = time.time()

                        task.log_info(f"Used skill {skill_key}")

        return True
