from src.data.FeatureList import FeatureList as fL


class DailyCraftMixin:
    def make_simply(self):
        self.info_set("current_task", "make_simply")
        if not self.transfer_to_home_point(should_check_out_boat=True):
            self.log_info("未能传送到帝江号")
        self.press_key("b")
        if not self.wait_click_feature(feature=fL.make_simply_entrance, settle_time=1, time_out=5, raise_if_not_found=False):
            self.mark_task_failure("未能找到简易制作入口")
            return False
        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_cdb1d49b, box=self.box.left, time_out=5, log=True):
            self.log_info("未能选定可制作的物品")
        if self.wait_click_feature(feature=fL.to_max_produce_num, box=self.box_of_screen(0.938, 0.902, 0.964, 0.941), time_out=5, raise_if_not_found=False):
            self.wait_pop_up()
        else:
            self.mark_task_failure("未能找到简易制作按钮")
            return False

    def make_weapon(self):
        self.info_set("current_task", "make_weapon")
        self.log_info("开始造装备任务")

        self.back()
        self.log_info("打开终端界面")

        if not self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_1faf3321,
                box=self.box.right,
                time_out=5
        ):
            self.mark_task_failure("未找到装备按钮，任务失败")
            return False
        self.log_info("找到装备按钮并点击")
        self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_557911d7, box=self.box_of_screen(0, 0, 0.5, 80 / 1080), time_out=5,
                            recheck_time=1, after_sleep=1)
        if not self.wait_click_feature(
                feature=fL.select_confirm,
                box=self.box_of_screen(0.938, 0.902, 0.964, 0.941),
                time_out=5,
                raise_if_not_found=False
        ):
            self.mark_task_failure("未找到制作按钮，任务失败")
            return False
        self.log_info("找到制作按钮并点击")
        self.log_info("等待弹窗完成，造装备任务准备完成")
        self.wait_pop_up()

        return True
