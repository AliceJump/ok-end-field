from src.data.FeatureList import FeatureList as fL
from src.data.characters_utils import get_contact_list_with_feature_list


class DailyBoatMixin:
    def _boat_stages(self):
        stages = self.config.get("⭐帝江号收菜", [])
        # 迁移后 ⭐帝江号收菜 恒为列表；非列表时按未启用处理，不再应用旧布尔开关值。
        if not isinstance(stages, list):
            return []
        return stages

    def boat_claim_rewards(self):
        self.enter_home_room_list()
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        ok_bool_clue = True
        ok_up_room = True
        self._one_click_collect()
        if "使用制造舱助力" in self._boat_stages():
            if not self.up_make_room_num(exchange_help_box):
                self.mark_task_failure("制造舱任务失败")
                ok_up_room = False
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
        if ok_bool_clue and ok_up_room:
            return True
        return False

    def _one_click_collect(self):
        stages = self._boat_stages()
        clue_box = self.box_of_screen(1627 / 1920, 178 / 1080, (1627 + 76) / 1920, (178 + 154) / 1080)
        if "收集线索" in stages:
            self.wait_click_feature(feature=fL.clue_collect_icon, box=clue_box)
        result = self.wait_click_feature(feature=fL.products_collect_icon, box=clue_box)
        if result:
            self.wait_pop_up(time_out=5)
        return True

    def collect_clue(self, exchange_help_box):
        if "收集线索" not in self._boat_stages():
            self.log_info("收集线索任务未启用，跳过")
            return True

        if not self._enter_exchange_room(exchange_help_box):
            return False

        self._receive_clue()
        self._give_clue()

        self.log_info("收集线索任务完成")
        return True

    def _enter_exchange_room(self, exchange_help_box):
        if not self.wait_click_feature(
            feature=fL.exchange_room,
            time_out=6,
            box=exchange_help_box,
            raise_if_not_found=False,
        ):
            self.log_info("未找到会客室，无法收集线索")
            return False

        self.wait_click_feature(
            feature=fL.to_max_produce_num,
            box=self.box_of_screen(0.550, 0.885, 0.573, 0.920),
            time_out=5,
            raise_if_not_found=False,
        )
        self.log_info("进入会客室,准备处理收集线索")
        return True

    def _receive_clue(self):
        self.receive_bool = False
        if not self.wait_click_feature(
            feature=fL.receive_clue_enter,
            time_out=4,
            box=self.box.right,
            raise_if_not_found=False,
            after_sleep=1
        ):
            self.log_info("未找到接收按钮")
            return

        if self.wait_click_feature(
            feature=fL.all_receive,
            time_out=4,
            raise_if_not_found=False,
        ):
            self.receive_bool = True


    def _give_clue(self):
        start_index = 1
        search_box = self._clue_search_box(shift_x=0.558 - 0.258)
        found=None
        if self.receive_bool:
            self.log_info("接收线索成功，开始放置线索")
            while start_index <= 7:
                found = self._find_first_clue_icon(start_index, search_box)
                if not found:
                    break

                clue_index, result = found
                self.log_info("点击线索框")

                self.click(result, after_sleep=0.5)
                self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_401d58fa,
                    time_out=2,
                    box=self.box.top_right,
                )

                start_index = clue_index + 1
        start_end_x_offset=0.3
        if self.wait_click_feature(
            feature=fL.skip_dialog_confirm,
            box=self.box_of_screen(0.371+start_end_x_offset, 0.770, 0.397+start_end_x_offset, 0.811),
            time_out=1,
            raise_if_not_found=False,
        ):
            self.wait_pop_up()

    def _clue_search_box(self, shift_x=0):
        return self.box_of_screen(
            x=1390 / 3840 - shift_x,
            y=450 / 2160,
            to_x=3360 / 3840 - shift_x,
            to_y=1330 / 2160,
        )

    def _find_first_clue_icon(self, start_index, search_box):
        for i in range(start_index, 8):
            self.next_frame()
            result = self.find_one(
                feature=f"clue_{i}_icon",
                box=search_box,
            )
            if result:
                return i, result
        return None

    def up_make_room_num(self, exchange_help_box):
        self.wait_ui_stable()
        results = self.find_feature(feature=fL.make_room, box=exchange_help_box)
        if not results:
            self.mark_task_failure("未找到制造舱，任务失败")
            return False
        for result in results:
            self.click(result)
            self.log_info("点击制造室")
            self.use_help(char=False)
            if not self.safe_back(feature=fL.operation_report_icon):
                self.log_info("无法返回到运转界面")
                return False
        self.log_info("制造舱助力任务完成")
        return True

    def use_help(self, char=True):
        if not self.wait_click_feature(feature=fL.can_use_help, time_out=2, box=self.box_of_screen(0.890, 0.011, 0.941, 0.074), raise_if_not_found=False):
            return
        if not self.wait_click_feature(feature=fL.skip_dialog_confirm, time_out=2, box=self.box_of_screen(0.818, 0.787, 0.865, 0.861), raise_if_not_found=False):
            return
        if char:
            self.wait_ui_stable()
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
        self.wait_click_feature(feature=fL.skip_dialog_confirm, time_out=2, box=self.box_of_screen(0.818, 0.787, 0.865, 0.861), raise_if_not_found=False)
        return
