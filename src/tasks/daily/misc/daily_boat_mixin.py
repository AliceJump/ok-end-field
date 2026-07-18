from src.data.FeatureList import FeatureList as fL
from src.data.characters_utils import get_contact_list_with_feature_list


class DailyBoatMixin:
    def boat_claim_rewards(self):
        self.enter_home_room_list()
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        ok_bool_clue = True
        ok_up_room = True
        ok_culture_room = True
        if not self.up_make_room_num(exchange_help_box):
            self.mark_task_failure("制造舱任务失败")
            ok_up_room = False
        if not self.culture_room(exchange_help_box):
            self.mark_task_failure("培养舱任务失败")
            ok_culture_room = False
        if not self.safe_back(feature=fL.operation_report_icon):
            self.log_info("无法返回到运转界面")
            return False
        self.use_help()
        if not self.safe_back(feature=fL.operation_report_icon):
            self.log_info("无法返回到运转界面")
            return False
        if not self.collect_clue(exchange_help_box):
            self.mark_task_failure("收集线索任务失败")
            ok_bool_clue = False
        if ok_bool_clue and ok_up_room and ok_culture_room:
            return True
        return False

    def collect_clue(self, exchange_help_box):
        if "收集线索" not in self.config.get("帝江号收菜操作", []):
            self.log_info("收集线索任务未启用，跳过")
            return True

        if not self._enter_exchange_room(exchange_help_box):
            return False

        self._collect_clue()
        self._receive_clue()
        self._give_clue()

        self.log_info("收集线索任务完成")
        return True

    def _enter_exchange_room(self, exchange_help_box):
        result = self.wait_feature(
            feature=fL.make_room,
            time_out=4,
            box=exchange_help_box,
            raise_if_not_found=False,
        )

        if not result:
            self.mark_task_failure("未找到会客室，任务失败")
            return False

        self.scroll_relative(
            result.x / self.width,
            result.y / self.height,
            count=8,
        )

        self.wait_ui_stable(refresh_interval=0.5)

        if not self.wait_click_feature(
            feature=fL.exchange_room,
            time_out=6,
            box=exchange_help_box,
            raise_if_not_found=False,
        ):
            self.log_info("未找到会客室，无法收集线索")
            return False

        self.log_info("进入会客室,准备处理收集线索")
        return True

    def _collect_clue(self):
        self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.550, 0.885, 0.573, 0.920),
            time_out=5,
            raise_if_not_found=False,
        )

        if not self.clue_safe(
            self.wait_click_feature,
            feature=fL.collect_clue_enter,
            time_out=4,
            after_sleep=1,
            raise_if_not_found=False,
        ):
            self.log_info("未找到收集线索按钮")
            return

        self.log_info("点击收集线索")

        self.clue_safe(
            self.wait_click_feature,
            feature=fL.give_gift,
            time_out=4,
            box=self.box_of_screen(0.938, 0.641, 0.957, 0.669),
            after_sleep=1,
            raise_if_not_found=False,
        )

        self.back(after_sleep=1)

    def _receive_clue(self):
        if not self.clue_safe(
            self.wait_click_feature,
            feature=fL.receive_clue_enter,
            time_out=4,
            box=self.box.right,
            after_sleep=1,
            raise_if_not_found=False,
        ):
            self.log_info("未找到接收按钮")
            return

        self.clue_safe(
            self.wait_click_feature,
            feature=fL.all_receive,
            time_out=4,
            after_sleep=1,
            raise_if_not_found=False,
        )

        self.back(after_sleep=1)

    def _give_clue(self):
        results = self._find_clue_icons()

        for result in results:
            self.clue_guard()

            self.log_info("点击线索框")

            self.click(result)

            self.clue_safe(
                self.wait_click_ocr,
                match=self.lang.daily_routine_mixin.k_401d58fa,
                time_out=4,
                box=self.box.top_right,
                after_sleep=1,
            )
            self.back(after_sleep=1)

        self.clue_guard()

        if self.clue_safe(
            self.wait_click_ocr,
            match=self.lang.daily_routine_mixin.k_0503d6d6,
            time_out=4,
            box=self.box.bottom,
            after_sleep=1,
        ):
            self.wait_pop_up()

    def _find_clue_icons(self):
        search_box = self.box_of_screen(
            x=1390 / 3840,
            y=450 / 2160,
            to_x=3360 / 3840,
            to_y=1330 / 2160,
        )

        results = []

        for i in range(1, 8):
            self.next_frame()
            self.clue_guard()

            result = self.find_one(
                feature=f"clue_{i}_icon",
                box=search_box,
            )

            if result:
                results.append(result)

        return results

    def clue_guard(self):
        """
        会客室线索任务专用弹窗处理
        """
        if self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.550, 0.885, 0.573, 0.920),
            time_out=1,
            raise_if_not_found=False,
        ):
            self.log_info("检测到线索弹窗并已处理")
            return True

        return False

    def clue_safe(self, func, *args, **kwargs):
        self.clue_guard()

        result = func(*args, **kwargs)

        self.clue_guard()

        return result

    def up_make_room_num(self, exchange_help_box):
        if "制造舱" not in self.config.get("帝江号收菜操作", []):
            self.log_info("制造舱助力任务未启用，跳过")
            return True
        self.wait_ui_stable()
        results = self.find_feature(feature=fL.make_room, box=exchange_help_box)
        if not results:
            self.mark_task_failure("未找到制造舱，任务失败")
            return False
        for result in results:
            self.sleep(0.5)
            self.click(result, after_sleep=2)
            self.log_info("点击制造室")
            if icon := self.find_one(feature=fL.max_icon, horizontal_variance=0.01, vertical_variance=0.01):
                self.click(icon)
                self.wait_click_feature(feature=fL.to_max_produce_num, time_out=2, box=self.box.bottom_right, raise_if_not_found=False)

                if self.wait_click_feature(
                        feature=fL.skip_dialog_confirm, time_out=3, box=self.box.bottom_right, raise_if_not_found=False
                ):
                    self.wait_pop_up(after_sleep=1)
            self.use_help(char=False)
            if not self.safe_back(feature=fL.operation_report_icon):
                self.log_info("无法返回到运转界面")
                return False
        self.log_info("制造舱助力任务完成")
        return True

    def culture_room(self, exchange_help_box):
        if "培养舱" not in self.config.get("帝江号收菜操作", []):
            self.log_info("培养舱任务未启用，跳过")
            return True
        result = self.wait_feature(feature=fL.make_room, time_out=4, box=exchange_help_box, raise_if_not_found=False)
        if not result:
            self.mark_task_failure("未找到制造舱，任务失败")
            return False
        self.scroll_relative(result.x / self.width, result.y / self.height, count=-8)
        self.wait_ui_stable(refresh_interval=0.5)
        result = self.wait_feature(feature=fL.cultivation_room, time_out=4, box=exchange_help_box, raise_if_not_found=False)
        if not result:
            self.mark_task_failure("未找到培养舱，任务失败")
            return False
        self.click(result)
        self.log_info("点击培育室")
        results = self.wait_click_ocr(match=[self.lang.daily_routine_mixin.k_ffb5655a, self.lang.daily_routine_mixin.k_31cceca8], time_out=3, box=exchange_help_box,
                                      recheck_time=1)
        if not results:
            self.mark_task_failure("未找到全部收取或培养中字样，任务失败")
            return False
        if not (self.lang.daily_routine_mixin.k_ffb5655a.search(results[0].name)):
            self.log_info("正在培养，任务结束")
            return True
        self.log_info("找到收取按钮")
        self.wait_pop_up(after_sleep=1)
        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_a4cd21cc, time_out=3, box=self.box.bottom, after_sleep=1):
            self.mark_task_failure("未找到再次培养按钮，再次培养失败")
            return False
        self.click_confirm(time_out=3)
        self.log_info("再次培养成功")
        return True

    def use_help(self, char=True):
        if not self.wait_click_feature(feature=fL.can_use_help, time_out=2, box=self.box_of_screen(0.890, 0.011, 0.941, 0.074), after_sleep=1, raise_if_not_found=False):
            return
        if not self.wait_click_feature(feature=fL.skip_dialog_confirm, time_out=2, box=self.box_of_screen(0.818, 0.787, 0.865, 0.861), after_sleep=1, raise_if_not_found=False):
            return
        if char:
            char_list = list(get_contact_list_with_feature_list().values())
            count = 0
            for char in char_list:
                if result := self.find_one(feature=char, box=self.box_of_screen(0.3, 0, 1, 1)):
                    self.click(result)
                    count += 1
                if count >= 2:
                    break
        else:
            self.wait_click_feature(feature=fL.max_icon, time_out=2, box=self.box_of_screen(0.699, 0.654, 0.732, 0.719), raise_if_not_found=False)
        self.wait_click_feature(feature=fL.skip_dialog_confirm, time_out=2, box=self.box_of_screen(0.818, 0.787, 0.865, 0.861), after_sleep=1, raise_if_not_found=False)
        return
