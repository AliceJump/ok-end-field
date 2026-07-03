import time
from qfluentwidgets import FluentIcon
from src.icons import Icons
from src.data.FeatureList import FeatureList as fL
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.battle_mixin import BattleMixin
from src.data.world_map import permanent_dict, YINGTUO_MONUMENT
from src.data.world_map_utils import get_world_map_text
class YingTuoTask(BattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "影拓丰碑"
        self.group_name = "战斗"
        self.description = "自动完成当前所有普通影拓丰碑关卡"
        self.icon = Icons.BATTLE
        self.index = 0
        self.yingtuo_list = permanent_dict[YINGTUO_MONUMENT]
        self.support_schedule_task = True
    def run(self):
        self.ensure_main(time_out=400)
        if not self.enter_yingtuo():
            return
        while self.find_normal_challenge():
            self.log_info("找到普通关卡，进入挑战页面")
            self.wait_ocr(match=self.target, time_out=3, box=self.box.top_left, log=True)
            results = self.find_feature(feature=fL.yingtuo_not_cleared_icon, box=self.box_of_screen(0.033, 0.133, 0.058, 0.778))
            for result in results:
                self.click(result, after_sleep=0.5)
                self.log_info("进入挑战页面，开始挑战")
                if not self.wait_click_feature(feature=fL.to_max_produce_num, box=self.box_of_screen(0.934, 0.881, 0.977, 0.965), time_out=10, raise_if_not_found=False, after_sleep=2):
                    self.log_info("未能找到挑战开始按钮")
                    raise Exception("未能找到挑战开始按钮")
                if not self.wait_click_feature(feature=fL.give_gift, box=self.box_of_screen(0.934, 0.881, 0.977, 0.965), time_out=10, raise_if_not_found=False):
                    self.log_info("未能进入战斗")
                    raise Exception("未能进入战斗")
                if not self.battle_and_exit():
                    self.log_info("战斗过程中发生错误，返回失败")
                    raise Exception("战斗过程中发生错误")
                if not self.wait_feature(feature=fL.to_max_produce_num, box=self.box_of_screen(0.934, 0.881, 0.977, 0.965), time_out=60, raise_if_not_found=False):
                    self.log_info("未能找到挑战开始按钮，返回失败")
                    raise Exception("未能找到挑战开始按钮")
                self.log_info("挑战完成，继续寻找下一个普通关卡")
            if not self.safe_back(feature=fL.yingtuo_monument, box=self.box_of_screen(0.002, 0.750, 0.990, 0.783)):
                self.log_info("未能安全返回，任务结束")
                return
        self.log_info("影拓丰碑任务完成", notify=True)
    def enter_yingtuo(self):
        self.log_info("开始影拓丰碑任务", notify=True)
        self.press_key("f8", after_sleep=2)
        # 如果当前不在常驻战斗页，先进入常驻战斗页
        if not self.find_feature(feature=fL.yingtuo_entrance):
            if not self.wait_click_feature(feature=fL.resident_icon, time_out=10, raise_if_not_found=False, after_sleep=2):
                self.log_info("未能进入常驻战斗页，任务结束", notify=True)
                return False
        if not self.wait_click_feature(feature=fL.yingtuo_entrance, time_out=10, raise_if_not_found=False):
            self.log_info("未能找到影拓入口，任务结束", notify=True)
            return False
        if not self.wait_feature(feature=fL.yingtuo_monument, time_out=10, raise_if_not_found=False):
            self.log_info("未能找到影拓丰碑活动页标志，任务结束", notify=True)
            return False
        self.sleep(1)
        self.log_info("成功进入影拓丰碑页面")
        return True
    def find_normal_challenge(self):
        if self.index >= len(self.yingtuo_list):
            self.log_info("已完成所有普通关卡")
            return None
        self.target = get_world_map_text(self.lang, self.yingtuo_list[self.index])
        self.log_info(f"寻找{self.target}关卡")
        for _ in range(3):  # 尝试多次寻找，增加成功率
            if self.wait_click_ocr(match=self.target, box=self.box_of_screen(0.013, 0.759, 0.991, 0.824), time_out=2, raise_if_not_found=False, after_sleep=2):
                self.log_info(f"找到{self.target}关卡")
                self.index += 1
                return self.target
            self.scroll_relative(0.5, 0.5, -5)
        return None
    def battle_and_exit(self):
        end_time = time.time()
        while not self.wait_feature(feature=fL.battle_space_left, time_out=1, raise_if_not_found=False):
            if time.time() - end_time > 300:
                self.log_info("等待超时，进入协议空间超时")
                return False
        self.auto_battle()
        if not self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_0ba18905, box=self.box.bottom_right, log=True, recheck_time=1):
            self.log_info("未能退出按钮")
            return False
        return True
        
