import re
from qfluentwidgets import FluentIcon
from ok import TriggerTask, Logger

from src.tasks.AutoCombatLogic import AutoCombatLogic
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.battle_mixin import BattleMixin

logger = Logger.get_logger(__name__)


# 自动战斗主逻辑独立类

# 原有任务类调用独立逻辑
class AutoCombatTask(BattleMixin, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗"
        self.group_name = "战斗"
        self.description = "自动检测战斗开始和结束，使用说明参见选项"
        self.icon = FluentIcon.ACCEPT
        self._combat_logic = AutoCombatLogic(self)

    def run(self):
        self._combat_logic.run()
