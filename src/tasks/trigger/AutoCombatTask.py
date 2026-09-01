from ok import Logger, TriggerTask

from src.icons import Icons
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic

logger = Logger.get_logger(__name__)


# 自动战斗主逻辑独立类


# 原有任务类调用独立逻辑
class AutoCombatTask(BattleMixin, TriggerTask):
    requires_foreground = True  # 战斗需要前台

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗"
        self.description = "自动检测战斗开始和结束，使用说明参见选项"
        self.icon = Icons.Battle

        self._combat_logic = AutoCombatLogic(self)

    def run(self):
        self.check_resolution()
        self._combat_logic.run()
