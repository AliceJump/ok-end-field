from src.data.world_map import stages_list
from qfluentwidgets import FluentIcon
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.daily.daily_battle_mixin import DailyBattleMixin
import re


class BattleTask(DailyBattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "刷体力"
        self.description = "刷体力任务"
        self.icon = FluentIcon.BRIGHTNESS

    def run(self):
        if self.battle():
            self.log_info("刷体力结束!", notify=self.config.get("后台结束战斗通知") and self.in_bg())
        else:
            self.log_info("未检测到刷体力正常结束,可能未进入战斗或战斗异常,请检查")
