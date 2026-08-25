from qfluentwidgets import FluentIcon

from src.data.world_map import stages_list
from src.data.world_map_utils import get_stage_category
from src.tasks.daily.daily_battle_mixin import DailyBattleFeature
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class TestBattleToEnd(Common, MapMixin, ZipLineMixin):
    """独立测试刷体力流程中的奖励点导航。"""

    requires_foreground = True  # 战斗/导航需要前台

    CFG_STAGE_NAME = "关卡名"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "测试"
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.visible = self.debug
        self.default_config = {self.CFG_STAGE_NAME: stages_list[0]}
        self.config_type[self.CFG_STAGE_NAME] = {
            "type": "drop_down",
            "options": stages_list,
        }

        # to_end 只依赖这些 DailyBattleFeature 实例状态，避免注册完整刷体力配置。
        self.daily_battle = DailyBattleFeature.__new__(DailyBattleFeature)
        self.daily_battle._task = self
        self.daily_battle.stages_list = stages_list
        self.daily_battle._reset_battle_state()

    def run(self):
        stage_name = self.config.get(self.CFG_STAGE_NAME, stages_list[0])
        battle_ctx = self.daily_battle.battle_ctx
        battle_ctx.stage_name = stage_name
        battle_ctx.category_name = get_stage_category(stage_name)
        battle_ctx.is_extra_mode = False

        return self.daily_battle.to_end()
