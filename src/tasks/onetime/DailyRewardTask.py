from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common


class DailyRewardTask(Common):
    """日常奖励子任务：行动手册与通行证奖励，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常奖励"
        self.icon = Icons.Collect
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "领取「行动手册/日常」和「通行证」中的奖励。"

    def claim_daily_rewards(self):
        self.info_set("current_task", "claim_daily_rewards")
        self.log_info("开始领取日常奖励任务")

        self.press_key("f8")
        self.log_info("按下 F8 打开日常奖励界面")

        if not self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_8d0e83fc,
            box=self.box.top,
            time_out=5,
        ):
            self.mark_task_failure("未找到日常奖励按钮，任务失败")
            return False
        self.log_info("找到日常奖励按钮并点击")

        self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_39d12e73_1,
            box=self.box.right,
            time_out=5,
        )

        if result := self.find_one(feature=fL.claim_gift, box=self.box.left, threshold=0.8):
            self.log_info("发现可领取的额外奖励，点击领取")
            self.click(result)
            self.wait_pop_up()
            self.log_info("额外奖励领取完成")

        self.log_info("日常奖励领取完成")

        if not self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_23926d61,
            box=self.box.bottom_right,
            time_out=5,
        ):
            self.mark_task_failure("未找到通行证奖励入口，任务失败")
            return False

        if self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_d7613f0e,
            box=self.box.top,
            time_out=5,
        ):
            mission_boxes = (
                self.wait_ocr(
                    x=0.12,
                    y=0.33,
                    to_x=0.31,
                    to_y=0.80,
                    match=self.lang.daily_routine_mixin.k_105cdd5a,
                    time_out=2,
                    raise_if_not_found=False,
                )
                or []
            )
            for box in mission_boxes:
                self.click_box(box=box)
                self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_3ecdd4bb,
                    box=self.box.bottom,
                    time_out=2,
                )
            self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_727d1bec,
                box=self.box.top,
                time_out=5,
            )

        reward_clicked = self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_39d12e73_1,
            box=self.box.bottom,
            time_out=2,
        )
        if reward_clicked:
            self.wait_pop_up()
        self.send_key("esc")
        pass_page = self.wait_until(
            lambda: self.ocr(match=self.lang.daily_routine_mixin.k_25d2b666, box=self.box.top_right),
            time_out=2,
            raise_if_not_found=False,
        )
        if pass_page:
            self.send_key("esc")
            self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_4d0b4688, time_out=5)
            if len(self.ocr(match=self.lang.daily_routine_mixin.k_1c5ad36e, box=self.box.center)) > 0:
                self.click_confirm(time_out=5)

        return True

    def run(self):
        self.ensure_main(time_out=420)
        return self.claim_daily_rewards()
