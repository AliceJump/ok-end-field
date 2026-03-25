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
        self.default_config = {'_enabled': False}
        self.name = "自动战斗"
        self.description = "自动战斗(进入战斗后自动战斗直到结束)"
        self.icon = FluentIcon.ACCEPT
        self.default_config.update({
            "技能释放": "123",
            "启动技能点数": 2,
            "后台结束战斗通知": True,
            "无数字操作间隔": 6,
            "进入战斗后的初始等待时间": 2,
        })
        self.config_description.update({
            "技能释放": "满技能时, 开始释放技能, 如123, 建议只放3个技能",
            "启动技能点数": "当技能点达到该数值时，开始执行技能序列, 1-3",
            "无数字操作间隔": "战斗中周期触发锁敌+向前闪避的最小间隔(秒，最少6秒)",
        })
        self._combat_logic = AutoCombatLogic(self)

    def run(self):
        self._combat_logic.run()
