from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.tasks.daily.daily_battle_mixin import DailyBattleFeature
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.mixin.battle_mixin import BattleMixin


class BattleTask(Common, MapMixin, ZipLineMixin, BattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "刷体力"
        self.group_name = "战斗"
        self.group_icon = Icons.BATTLE
        self.description = "使用说明参见选项，更多用法参见 ./docs/体力本.md"
        self.icon = Icons.BATTLE
        self.daily_battle = DailyBattleFeature(self)
        self.default_config_group.pop("⭐刷体力", None)
        self.default_config.pop("⭐刷体力", None)
        task_group = {"隐藏": []}

        # 合并两个分组字典
        all_groups = {
            **task_group,
            **self.default_config_group,
            **{"其他配置": ["多账户模式", "发生异常时终止游戏", "仅退出游戏"]},
        }

        self.register_config_groups(all_groups)

    def run(self):
        self.ensure_main(time_out=420)
        try:
            ok = self.daily_battle.battle()
            if ok:
                self.log_info("刷体力结束!", notify=self.get_battle_config("后台结束战斗通知") and self.in_bg())
            else:
                self.log_info("未检测到刷体力正常结束,可能未进入战斗或战斗异常,请检查")
        finally:
            # 显式释放 YOLO/OpenVINO 相关资源，避免长期驻留在进程内存中。
            self.release_yolo_detector()
