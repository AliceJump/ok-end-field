import re

from src.data.FeatureList import FeatureList as fL


class DailyRewardMixin:
    def _click_ocr_with_info(self, match_str, box, time_out=5, after_sleep=2):
        if not self.wait_click_ocr(
                match=re.compile(match_str),
                box=box,
                time_out=time_out,
                after_sleep=after_sleep,
        ):
            # match_str 是调用方传入的 OCR 匹配文本（运行时参数）不过 tr
            self.mark_task_failure(self.tr("未找到{name}按钮，任务失败").format(name=match_str))
            return False

        self.log_info(self.tr("找到{name}按钮并点击").format(name=match_str))
        return True

    def claim_weekly_rewards(self):
        self.log_info("开始领取每周事务")

        if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_13eea5dd, box=self.box.left, time_out=5):
            self.log_info("进入『每周事务』页面")
            if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_39d12e73_1, box=self.box.top_right, time_out=5):
                if self.wait_click_ocr(
                        match=self.lang.daily_routine_mixin.k_bf856c96,
                        box=self.box.bottom_right,
                        time_out=5,
                ):
                    self.wait_pop_up()
                    self.log_info("已领取『每周事务』奖励")
                else:
                    self.log_info("未找到『每周事务/一键领取』按钮")
            else:
                self.log_info("未找到『每周事务/领取』按钮")
        else:
            self.log_info("未找到『活动中心/每周事务』入口")

        return True

    def claim_sanity_supply(self):
        self.log_info("开始领取理智补给")

        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_059a808c, box=self.box.left, time_out=2):
            self.log_info("未找到『活动中心/理智补给』入口")
            return False

        self.log_info("进入『理智补给』页面")
        if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_39d12e73_1, box=self.box_of_screen(0.894, 0.648, 0.995, 0.991), time_out=5):
            self.wait_pop_up()
            self.log_info("已领取『理智补给』奖励")
            return True

        self.log_info("未找到『理智补给/领取』按钮")
        return False

    def scratch_reward(self):
        start_time = self.active_time()

        while True:
            # 页面判断模板允许横向偏移，纵向不需要放宽（vertical_variance 保持默认 0）
            if self.wait_feature(feature=fL.in_scratch_card_page, horizontal_variance=0.1,
                                 raise_if_not_found=False, time_out=2):
                break

            if self.active_time() - start_time > 20:
                self.log_info("进入刮刮卡页面超时")
                return False

            if not self.wait_click_feature(
                feature=fL.scratch_card_icon,
                box=self.box_of_screen(0.089, 0.130, 0.176, 0.983),
                raise_if_not_found=False,
                time_out=2,
            ):
                return False

        if not self.wait_click_feature(feature=fL.start_scratch_card, raise_if_not_found=False, time_out=5):
            self.log_info("没找到开始刮刮乐的按钮")
            return False
        if not self.wait_feature(feature=fL.scratching_icon, raise_if_not_found=False, time_out=5):
            self.log_info("没点到刮刮乐")
            return False
        self.wait_ui_stable(refresh_interval=1)
        self.log_info("开始刮")
        self.drag_scan_area((0.218, 0.376), (0.787, 0.700))
        self.wait_pop_up()

    def claim_activity_rewards(self):
        self.info_set("current_task", "claim_activity_rewards")
        self.log_info("开始领取活动页奖励")

        self.press_key("f7")
        self.log_info("按下 F7 打开活动中心")

        enabled_rewards = self.config.get("⭐活动奖励", [])
        # 迁移后 ⭐活动奖励 恒为列表；非列表时按未启用处理，不再应用旧布尔开关值。
        if not isinstance(enabled_rewards, list):
            enabled_rewards = []
        enabled_rewards = set(enabled_rewards)
        weekly_enabled = "周常奖励" in enabled_rewards
        sanity_enabled = "理智补给" in enabled_rewards
        scratch_enabled = "刮刮乐" in enabled_rewards


        if weekly_enabled:
            self.claim_weekly_rewards()
        else:
            self.log_info("已关闭『周常奖励』，跳过")
            
        if scratch_enabled:
            self.scratch_reward()
        else:
            self.log_info("已关闭『刮刮乐』，跳过")

        if sanity_enabled:
            self.claim_sanity_supply()
        else:
            self.log_info("已关闭『理智补给』，跳过")

        return True

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

        if result := self.find_one(
                feature=fL.claim_gift, box=self.box.left, threshold=0.8
        ):
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
            mission_boxes = self.wait_ocr(
                x=0.12, y=0.33,
                to_x=0.31, to_y=0.80,
                match=self.lang.daily_routine_mixin.k_105cdd5a,
                time_out=2,
                raise_if_not_found=False,
            ) or []
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
