from ok import Logger, TriggerTask

from src.icons import Icons
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic

logger = Logger.get_logger(__name__)


# 自动战斗主逻辑独立类


# 原有任务类调用独立逻辑
class AutoCombatTask(BattleMixin, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动战斗"
        self.description = "自动检测战斗开始和结束，使用说明参见选项"
        self.icon = Icons.Battle

        self.default_config.update(
            {
                KEY_SAVE_ENEMY_PRESENCE_FRAMES: False,
            }
        )
        self.config_description.update(
            {
                KEY_SAVE_ENEMY_PRESENCE_FRAMES: (
                    "保存每一次敌人存在检测使用的当前帧，并在图片上绘制本次实际扫描区域、"
                    "命中的血条像素框和最终判定状态。每张 PNG 会生成一个完全同 stem 的"
                    " .inform.json，记录每个扫描区域的 hit/miss 和精确像素坐标；"
                    "文件保存在 screenshots/enemy_presence。用于排查误检，开启后会快速产生大量文件。"
                ),
            }
        )

        self._combat_logic = AutoCombatLogic(self)

    def run(self):
        self.check_resolution()
        self._combat_logic.run()
