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
        self.description = "自动检测战斗开始和结束，使用说明参见选项"
        self.icon = FluentIcon.ACCEPT
        # 下列代码在 daily_battle_mixin.py 中有部分重复。如有更新，请两边一起修改。
        # 不要试图归并，否则会影响『日常任务』中的选项顺序。
        self.default_config.update({
            "技能释放": ["1", "2", "3"],
            "启动技能点数": 2,
            "后台结束战斗通知": True,
            "无数字操作间隔": 6,
            "进入战斗后的初始等待时间": 3,
            "启用排轴": False,
            "排轴序列": "ult_2,1,e,ult_3,sleep_8",
        })
        self.config_description.update({
            "启动技能点数": (
                "当「技力条」达到该数值时，\n"
                "开始执行技能序列。取值范围1-3。"
            ),
            "无数字操作间隔": (
                "战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n"
                "取值不小于6。"
            ),
        })
        self._combat_logic = AutoCombatLogic(self)

    def run(self):
        self._combat_logic.run()
