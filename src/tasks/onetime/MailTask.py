from qfluentwidgets import FluentIcon

from src.icons import Icons
from src.tasks.mixin.common import Common


class MailTask(Common):
    """收邮件子任务：领取邮箱全部附件，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "收邮件"
        self.icon = Icons.Collect
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往「邮箱」领取全部邮件附件。"

    def claim_mail(self):
        self.info_set("current_task", "claim_delivery_rewards")
        self.log_info("开始收邮件")
        self.press_key("k")
        if not self.wait_click_ocr(
            x=0,
            y=0.88,
            to_x=0.25,
            to_y=0.95,
            match=self.lang.daily_routine_mixin.k_ffb5655a,
            time_out=5,
        ):
            self.log_info("未识别到领取按钮，直接退出收邮件")
            self.press_key("esc")
            return True
        self.wait_pop_up()
        stage_area = (
            self.wait_ocr(
                match=self.lang.daily_routine_mixin.k_4a2ece6a,
                box=self.box.top_left,
                time_out=4,
                raise_if_not_found=False,
            )
            or []
        )
        if len(stage_area) > 0:
            self.click(x=stage_area[0].x, y=stage_area[0].y + int(self.height * 0.25))
            self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_3fef35d6, box=self.box.center, time_out=5)
            self.wait_pop_up()
        self.press_key("esc")
        return True

    def run(self):
        self.ensure_main(time_out=420)
        self.run_mail()

    def run_mail(self):
        """收邮件流程，独立运行与被日常执行（DailyFeature）共用。"""
        return self.claim_mail()
