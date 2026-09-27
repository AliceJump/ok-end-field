from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common


class CraftWeaponTask(Common):
    """造装备子任务：制作列表首位装备，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "造装备"
        self.icon = Icons.SwordChallenge
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往「装备制造/套组装备制造」并制作一件列表首位的装备。"

    def make_weapon(self):
        self.info_set("current_task", "make_weapon")
        self.log_info("开始造装备任务")

        self.back()
        self.log_info("打开终端界面")

        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_1faf3321, box=self.box.right, time_out=5):
            self.mark_task_failure("未找到装备按钮，任务失败")
            return False
        self.log_info("找到装备按钮并点击")
        self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_557911d7,
            box=self.box_of_screen(0, 0, 0.5, 80 / 1080),
            time_out=5,
            recheck_time=1,
            after_sleep=1,
        )
        if not self.wait_click_feature(
            feature=fL.select_confirm,
            box=self.box_of_screen(0.938, 0.902, 0.964, 0.941),
            time_out=5,
            raise_if_not_found=False,
        ):
            self.mark_task_failure("未找到制作按钮，任务失败")
            return False
        self.log_info("找到制作按钮并点击")
        self.log_info("等待弹窗完成，造装备任务准备完成")
        self.wait_pop_up()

        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.make_weapon()
