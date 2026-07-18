from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list


class DailyLogisticsMixin:
    def claim_mail(self):
        self.info_set("current_task", "claim_delivery_rewards")
        self.log_info("开始收邮件")
        self.press_key("k", after_sleep=2)
        stage_area = self.ocr(match=self.lang.daily_routine_mixin.k_4a2ece6a, box=self.box.top_left)
        if len(stage_area) > 0:
            self.click(x=stage_area[0].x, y=stage_area[0].y + int(self.height * 0.25))
            self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_3fef35d6, box=self.box.center, time_out=5)
            self.wait_pop_up(after_sleep=2)
        if self.wait_click_ocr(
                x=0, y=0.88,
                to_x=0.25, to_y=0.95,
                match=self.lang.daily_routine_mixin.k_ffb5655a,
                time_out=5,
                after_sleep=2,
        ):
            self.wait_pop_up(after_sleep=2)
        self.press_key("esc", after_sleep=2)
        return True

    def claim_delivery_rewards(self):
        self.info_set("current_task", "claim_delivery_rewards")
        self.log_info("开始领取转交委托奖励")

        area = areas_list[0]
        self.to_model_area(area, "仓储节点")

        if not self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_41a9fd98,
                box=self.box.top_left,
                time_out=5
        ):
            self.log_info(f"'未找到我转交的委托'节点，返回主界面")
            self.ensure_main()

        results = self.wait_ocr(
            match=self.lang.daily_routine_mixin.k_bf856c96,
            box=self.box.bottom_right,
            time_out=5,
        )

        if not results:
            self.log_info(f"当前没有可领取的转交委托奖励，返回主界面")
            self.ensure_main()

        if results:
            self.click(results, after_sleep=2)
            if not self.wait_pop_up():
                self.log_info("未找到 '确认' 按钮，可能未成功领取奖励")
                self.ensure_main()
            self.sleep(2)

        self.log_info("转交委托奖励领取完成，返回主界面")
        self.ensure_main()
        self.log_info("转交委托奖励领取任务完成")

    def delivery_send_others(self):
        self.info_set("current_task", "delivery_send_others")

        send_count = 0

        for area in areas_list:
            activity_num = 0
            count = 0
            self.log_info(f"开始处理区域: {area}")

            while True:
                if 0 < activity_num <= count:
                    self.log_info(
                        f"{area}仓储节点已完成{activity_num}次，停止继续"
                    )
                    break

                self.to_model_area(area, "仓储节点")

                if not self.wait_click_ocr(
                        match=self.lang.daily_routine_mixin.k_298d3284,
                        box=self.box.top_left,
                        time_out=5
                ):
                    self.log_info(f"{area}未找到本地仓储节点，返回主界面")
                    self.ensure_main()
                    break

                results = self.wait_ocr(
                    match=[self.lang.daily_routine_mixin.k_573c7c18, self.lang.daily_routine_mixin.k_8f2058a8],
                    box=self.box.bottom,
                    time_out=5,
                )

                if not results:
                    self.log_info(
                        f"{area} 当前没有货物装箱可操作，返回主界面"
                    )
                    self.ensure_main()
                    break

                if activity_num == 0:
                    activity_num = len(results)
                    self.log_info(
                        f"{area}共有{activity_num}次可进行转交运送委托的活动",
                        notify=True,
                    )

                self.click(results[0], after_sleep=2)
                start_index = 0 if not (self.lang.daily_routine_mixin.k_view_quote in results[0].name) else 2
                steps = [
                    (fL.give_gift, self.box_of_screen(0.945, 0.904, 0.965, 0.937), 0),
                    (fL.fill_max, None, 0),
                    (fL.give_gift, self.box_of_screen(0.945, 0.904, 0.965, 0.937), 1),
                    (fL.give_gift, self.box_of_screen(0.945, 0.904, 0.965, 0.937), 0),
                    (fL.get_exchange_ticket, None, 0)
                ]

                for i in range(start_index, len(steps)):
                    step = steps[i]
                    feature = step[0]
                    box = step[1]
                    after_sleep = step[2]
                    time_out = 12 if i > 2 else 5
                    res = self.wait_click_feature(feature=feature, click_after_delay=0.5, box=box, time_out=time_out, raise_if_not_found=False, after_sleep=after_sleep)

                    optional_steps = {3}

                    if not res:
                        if i in optional_steps:
                            continue

                        self.log_info(
                            f"步骤 {feature} 未找到，跳过本次活动"
                        )
                        break
                self.ensure_main()
                self.press_key("v", after_sleep=1)
                self.press_key("j", after_sleep=1)

                if not self.wait_click_ocr(
                        match=self.lang.daily_routine_mixin.k_1dd73947,
                        box=self.box.bottom_left,
                        time_out=5
                ):
                    self.log_info(
                        "未找到 '转交运送委托' 按钮，跳过本次活动"
                    )
                    self.ensure_main()
                    break
                if not self.click_confirm():
                    self.log_info("未找到 '确认' 按钮，跳过本次活动")
                    self.ensure_main()
                    break
                count += 1
                send_count += 1
                self.log_info(f"{area} 已完成 {count}/{activity_num} 次转交")
        if send_count == 0:
            self.mark_task_failure("未完成任何转交运送委托操作")
            return False
        return True
