
from src.data.FeatureList import FeatureList as fL


class DailyCreditMixin:
    def collect_credit(self):
        self.info_set("current_task", "collect_credit")
        self.press_key("f5")
        self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_7be4248b, box=self.box.top, time_out=5, recheck_time=1)
        result = self.wait_click_ocr(match=[self.lang.daily_routine_mixin.k_b693e51a, self.lang.daily_routine_mixin.k_f646bcd5],
                                     box=self.box.bottom_left,
                                     time_out=7, recheck_time=1)
        if not result:
            self.log_info("未找到可收取信用或无待领取信用的选项")
            return False
        if "收取信用" in result[0].name:
            self.wait_pop_up()
        self.ensure_main()
        self.back()
        left_exchange_time = 5
        left_help_time = 5
        exchange_time = 0
        help_time = 0
        is_first_time = True
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        exchange_not_found = False
        count = 0
        self.log_info("开始好友拜访阶段")
        while True:
            temp_exchange_time = left_exchange_time
            if count >= 10:
                self.log_info("循环过多次仍未找到交流或助力对象，可能出现异常，结束拜访")
                return False
            if is_first_time:
                self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_e84c3ae9, box=self.box.right, time_out=7, recheck_time=1)
            else:
                if left_exchange_time <= 0 and left_help_time <= 0:
                    if exchange_not_found:
                        self.log_info("未完全找满交流对象，可能存在部分交流次数未完成")
                    self.info_set("exchange_time", exchange_time)
                    self.info_set("help_time", help_time)
                    return True

            result = None
            self.wait_ui_stable(refresh_interval=1)
            start_time = self.active_time()
            scroll_count = 0
            while not result:
                if is_first_time or scroll_count > 0:
                    span_box = self.box_of_screen(3400 / 3840, 301 / 2160, 3692 / 3840, 1883 / 2160)
                else:
                    span_box = self.box_of_screen(3400 / 3840, 615 / 2160, 3692 / 3840, 1883 / 2160)
                if self.active_time() - start_time > 40:
                    self.log_info("找不到可交流或助力的玩家")
                    return False
                if left_exchange_time > 0:
                    result = self.find_feature(
                        feature="can_exchange_info_icon", box=span_box
                    )
                    if scroll_count >= 7:
                        self.back()
                        self.ensure_in_friend_boat()
                        self.press_key('f')
                        self.wait_ui_stable(refresh_interval=1)
                        left_exchange_time = 0
                        exchange_not_found = True
                        continue
                elif left_help_time > 0:
                    result = self.find_feature(
                        feature="can_help_icon", box=span_box
                    )
                if not result:
                    scroll_count += 1
                    self.scroll_relative(0.5, 0.5, -4)
                    self.wait_ui_stable(refresh_interval=1)

            self.click(result)
            self.click_confirm(time_out=5, recheck_time=1)
            if not self.ensure_in_friend_boat():
                self.log_info("未能进入好友帝江号")
                if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_a730d877, box=self.box.top_left, time_out=1):
                    continue
                else:
                    return False
            self.sleep(2)
            actions = []
            if left_exchange_time > 0:
                actions.append("交流")
            if left_help_time > 0:
                actions.append("助力")
            self.log_info(f"已进入好友帝江号，准备进行{''.join(actions)}操作")
            self.press_key("y")
            self.wait_ui_stable(refresh_interval=1)
            if left_exchange_time > 0:
                if not self.wait_click_feature(feature=fL.info_exchange, box=exchange_help_box, time_out=5, click_after_delay=0.5, raise_if_not_found=False, after_sleep=0.5):
                    left_exchange_time = 0
                else:
                    left_exchange_time -= 1
                    exchange_time += 1
            if left_help_time > 0:
                result = self.find_feature(feature=fL.assist_friend, box=exchange_help_box)
                if not result and temp_exchange_time <= 0:
                    self.log_info("未找到可助力的对象")
                    left_help_time = 0
                if result:
                    for res in result:
                        if not self.config.get("尝试仅收培育室"):
                            self.click(res)
                            left_help_time -= 1
                            help_time += 1
                            if left_help_time <= 0:
                                break
                        if res == result[-1]:
                            self.scroll_relative(res.x / self.width, res.y / self.height, count=-8)
                            self.wait_ui_stable(refresh_interval=0.5)
                            if result := self.find_feature(feature=fL.assist_friend, box=exchange_help_box):
                                self.log_info("继续进行助力操作")
                                last_help = result[-1]
                                self.click(last_help)
                                if (last_help.x / self.width > 0.7):
                                    self.wait_pop_up(time_out=5)
                                left_help_time -= 1
                                help_time += 1
            if not self.safe_back(feature=fL.friend_page, time_out=10):
                self.log_info("未能安全返回好友列表")
                return False
            is_first_time = False
            count += 1
