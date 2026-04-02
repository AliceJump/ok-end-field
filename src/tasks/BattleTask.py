from qfluentwidgets import FluentIcon

from src.tasks.daily.daily_battle_mixin import DailyBattleMixin


class BattleTask(DailyBattleMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "刷体力"
        self.description = "使用说明参见选项，更多用法参见 ./docs/日常任务.md"
        self.icon = FluentIcon.BRIGHTNESS

    def run(self):
        self.ensure_main(time_out=420)
        if self.battle():
            self.log_info("刷体力结束!", notify=self.config.get("后台结束战斗通知") and self.in_bg())
        else:
            self.log_info("未检测到刷体力正常结束,可能未进入战斗或战斗异常,请检查")
