import random
import re
import time

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import stages_cost, higher_order_feature_dict
from src.data.world_map_utils import get_stage_category
from src.tasks.battle_mixin import BattleMixin
from src.tasks.daily.common import LiaisonResult, build_name_patterns
from src.tasks.BaseEfTask import BaseEfTask


class DailyLiaisonMixin(BattleMixin):
    def transfer_to_home_point(self):
        """通过地图界面传送到帝江号指定点"""
        self.ensure_main()
        self.log_info("开始传送到帝江号")
        self.send_key("m", after_sleep=2)
        self.log_info("打开地图界面 (按下 M)")

        target_area = self.wait_ocr(
            match=re.compile("帝江号"), box=self.box.top, time_out=5
        )
        if not target_area:
            self.log_info("未找到帝江号区域，传送失败")
            return False
        self.log_info("找到帝江号区域，点击进入")
        self.click(target_area, after_sleep=2)

        tp_icon = self.find_feature(
            feature_name="transfer_point", box=self.box.left, threshold=0.7
        )
        if not tp_icon:
            self.log_info("未找到传送点图标，传送失败")
            return False
        self.log_info("找到传送点图标，点击传送点")
        self.click(tp_icon, after_sleep=2)

        transfer_btn = self.wait_ocr(
            match="传送", box=self.box.bottom_right, time_out=10, log=True
        )
        if not transfer_btn:
            self.log_info("未找到传送按钮，传送失败")
            return False
        self.log_info("找到传送按钮，点击进行传送")
        self.click(transfer_btn, after_sleep=2)

        self.log_info("等待传送完成，检查舰桥界面")
        if not self.wait_ocr(
                match="舰桥", box=self.box.left, time_out=60, log=True
        ):
            pass

        self.log_info("传送完成，已到达帝江号舰桥")
        return True

    def navigate_to_main_hall(self) -> bool:
        self.log_info("开始前往中央环厅")
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            self.log_info(f"第 {attempt}/{max_attempts} 次尝试移动前进")
            self.move_keys("w", duration=1)
            if self.wait_ocr(
                    match="中央环厅", box=self.box.left, log=True
            ):
                self.log_info("已到达中央环厅")
                return True
        self.log_info("前往中央环厅可能失败，尝试后续操作")
        return True

    def start_tracking_and_align_target(self, target_feature_in_map, target_feature_out_map):
        """在地图中开启追踪并在地图外完成朝向对齐。"""
        result = self.find_one(
            feature_name=target_feature_in_map,
            box=self.box_of_screen(0, 0, 1, 1),
            threshold=0.7,
        )
        if not result:
            self.log_info(f"未找到{target_feature_in_map}图标")
            return False
        self.log_info(f"找到{target_feature_in_map}图标，点击进入")
        self.click(result, after_sleep=2)

        if result := self.wait_ocr(
                match=re.compile("追踪"), box=self.box.bottom_right, time_out=5
        ):
            if (
                    "追踪" in result[0].name
                    and "取" not in result[0].name
                    and "消" not in result[0].name
            ):
                self.log_info("点击追踪按钮")
                self.click(result, after_sleep=2)

        self.send_key("m", after_sleep=2)
        self.log_info("关闭地图界面 (按下 M)")

        self.align_ocr_or_find_target_to_center(
            ocr_match_or_feature_name_list=target_feature_out_map,
            only_x=True,
            threshold=0.7,
            ocr=False,
        )
        self.log_info("已对齐地图目标")
        return True

    def navigate_to_operator_liaison_station(self):
        """前往干员联络站。"""
        self.log_info("开始前往干员联络站")
        self.send_key("m", after_sleep=2)
        self.log_info("打开地图界面 (按下 M)")
        if not self.start_tracking_and_align_target(
                fL.operator_liaison_station, fL.operator_liaison_station_out_map
        ):
            return False

        def special_chat_detect():
            chat_box = self.find_feature("chat_icon_dark") or self.find_feature("chat_icon_2")

            if chat_box:
                self.log_info("发现干员，点击交互图标")
                self.send_key_down("alt")
                self.sleep(0.5)
                self.click(chat_box, after_sleep=0)
                self.send_key_up("alt")
                return LiaisonResult.FIND_CHAT_ICON

            return None

        return self.navigate_until_target(
            target_ocr_pattern=re.compile("联络"),
            nav_feature_name="operator_liaison_station_out_map",
            timeout=60,
            found_special_callback=special_chat_detect,
        )

    def perform_operator_liaison(self):
        """执行干员联络（会客/培养/制造）并尝试完成一次交互。"""
        self.log_info("开始执行干员联络任务")
        target_name = self.config.get("优先送礼对象")
        target_feature_name = self.can_contact_dict[target_name]
        search_char_box = self.box_of_screen(795 / 1920, 248 / 1080, 1687 / 1920, 764 / 1080)
        find_name = ""
        find_name_patterns = []
        for attempt in range(1, 11):
            self.log_info(f"第 {attempt}/10 次尝试打开信任度界面")
            self.press_key('f', after_sleep=2)
            result = {}
            found_target = False

            for _ in range(3):
                self.next_frame()
                found = self.find_one(feature_name=target_feature_name, box=search_char_box, threshold=0.7)
                if found:
                    result[target_feature_name] = found
                    found_target = True
                    break
                self.scroll_relative(0.5, 0.5, -3)
                self.wait_ui_stable(refresh_interval=0.5)

            if not found_target:
                self.back(after_sleep=2)
                self.press_key('f', after_sleep=2)
                self.log_info(f"未找到联络对象 {target_name}，尝试其他目标")

                other_results = {}
                for _, other_feature in self.can_contact_dict.items():
                    if other_feature == target_feature_name:
                        continue

                    self.next_frame()
                    found = self.find_one(feature_name=other_feature, box=search_char_box, threshold=0.7)
                    if found:
                        other_results[other_feature] = found

                if other_results:
                    feature = random.choice(list(other_results.keys()))
                    result[feature] = other_results[feature]

            if not result:
                self.log_info("未找到任何可联络对象")
                return False
            find_feature_name = next(iter(result))

            find_name = next(k for k, v in self.can_contact_dict.items() if v == find_feature_name)
            find_name_patterns = self.contact_name_patterns.get(find_name, build_name_patterns(find_name))
            self.log_info("找到联络对象")
            self.click(list(result.values())[0], after_sleep=2)
            if not self.wait_click_ocr(
                    match=re.compile("确认联络"),
                    box=self.box.bottom_right,
                    time_out=5,
                    log=True,
            ):
                self.log_info("未找到确认联络按钮，任务失败")
                return False
            self.log_info("点击确认联络按钮")
            wait_disappear_count = 0
            while self.ocr(match=re.compile("干员联络"), box=self.box.top_left):
                wait_disappear_count += 1
                if wait_disappear_count >= 200:
                    self.log_info("等待 '干员联络' 文案消失次数超限，任务失败")
                    return False
                self.next_frame()
                self.sleep(0.2)
            self.next_frame()
            if not self.wait_ocr(match=find_name_patterns, box=self.box.top, time_out=2, log=True):
                self.log_info(f"未找到 {find_name} 的名字,重新打开联络界面")
                self.ensure_main()
                continue
            self.next_frame()
            if chat_box := self.ocr(match=find_name_patterns, box=self.box.bottom_right):
                self.log_info("发现干员，点击进行交互")
                self.send_key_down("alt")
                self.sleep(0.5)
                self.click_box(chat_box, after_sleep=1)
                self.next_frame()
                self.wait_click_ocr(match=find_name_patterns, box=self.box.bottom_right, time_out=1)
                self.send_key_up("alt")
                self.log_info("干员联络完成")
                return True
            self.log_info(f"找到 {find_name} 的名字，开始界面对齐")
            find_flag = self.align_ocr_or_find_target_to_center(
                ocr_match_or_feature_name_list=find_name_patterns,
                raise_if_fail=False,
                only_x=True,
                max_time=14,
                max_step=150,
                min_step=20,
                slow_radius=100,
                tolerance=100,
                once_time=0.1
            )
            if find_flag:
                self.log_info("界面对齐完成")
                break

        self.log_info("开始寻找干员进行交互")
        start_time = time.time()
        chat_box = None

        while chat_box is None:
            chat_box = self.wait_ocr(
                match=find_name_patterns,
                box=self.box.bottom_right,
                time_out=1,
            )
            if chat_box:
                self.log_info("发现干员，点击进行交互")
                self.send_key_down("alt")
                self.sleep(0.5)
                self.click_box(chat_box, after_sleep=1)
                self.next_frame()
                self.wait_click_ocr(match=find_name_patterns, box=self.box.bottom_right, time_out=1)
                self.send_key_up("alt")
                self.log_info("干员联络完成")
                return True

            self.move_keys("w", duration=0.5)
            self.log_info("未找到干员，继续前进移动")
            self.sleep(0.5)

            if time.time() - start_time > 30:
                self.log_info("长时间未找到干员，任务超时")
                return False
        return False

    def collect_and_give_gifts(self):
        """执行收礼/送礼完整流程。"""
        self.log_info("开始收取或赠送礼物")
        start_time = time.time()

        while True:
            if time.time() - start_time > 30:
                self.log_info("等待 收下/赠送 超时")
                return False
            self.click(0.5, 0.5, after_sleep=0.5)
            result = self.wait_click_ocr(
                match=[re.compile("收下"), re.compile("赠送")],
                box=self.box.bottom_right,
                time_out=2,
                after_sleep=2,
            )
            if result:
                self.log_info(f"找到按钮: {result[0].name}")
                break

        if result and len(result) > 0 and "收下" in result[0].name:
            self.log_info("开始收下礼物")
            self.skip_dialog(
                end_list=[re.compile("ms")], end_box=self.box.bottom_left
            )
            self.sleep(1)
            self.wait_click_ocr(
                match=re.compile("确认"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
            )
            self.press_key('f', after_sleep=2)

            start_time = time.time()
            while True:
                if time.time() - start_time > 30:
                    self.log_info("等待 收下/赠送 超时")
                    return False
                self.click(0.5, 0.5, after_sleep=0.5)
                result = self.wait_ocr(
                    match=[re.compile("赠送")],
                    box=self.box.bottom_right,
                    time_out=2,
                )
                if result:
                    self.log_info("收下完成，准备赠送礼物")
                    break

        self.wait_click_ocr(
            match=re.compile("赠送"),
            box=self.box.bottom_right,
            time_out=5,
            after_sleep=2,
        )
        self.click(144 / 1920, 855 / 1080, after_sleep=2)
        self.log_info("点击赠送礼物位置")
        self.log_info("本次成功")
        if self.wait_click_ocr(
                match=re.compile("确认赠送"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
        ):
            self.log_info("确认赠送按钮已出现")
            start_time = time.time()
            while True:
                if time.time() - start_time > 30:
                    self.log_info("等待 离开按钮 超时")
                    return False
                self.click(0.5, 0.5, after_sleep=0.5)
                result = self.wait_click_ocr(
                    match=[re.compile("离开")],
                    box=self.box.bottom_right,
                    time_out=2,
                    after_sleep=2,
                )
                if result:
                    self.log_info("赠送完成，点击离开")
                    break
            self.log_info("成功赠送礼物")
            return True
        else:
            self.back(after_sleep=2)
            self.back(after_sleep=2)
            self.wait_click_ocr(match=re.compile("确认"), box=self.box.bottom_right, time_out=5, after_sleep=2)
        self.log_info("赠送礼物失败")
        return False

    def execute_gift_to_liaison(self):
        """传送至帝江号后执行联络与送礼链路。"""
        self.log_info("传送至帝江号指定点")
        if not self.transfer_to_home_point():
            self.log_info("传送失败，无法开始送礼任务")
            return False
        wait_bridge_disappear_count = 0
        while self.ocr(match="舰桥", box=self.box.left):
            wait_bridge_disappear_count += 1
            if wait_bridge_disappear_count >= 120:
                self.log_info("等待 '舰桥' 文案消失次数超限，送礼任务中断")
                return False
            self.next_frame()
            self.sleep(0.5)
        self.log_info("舰桥提示已经消失，等待信赖弹窗并消失")
        start_time = time.time()
        if self.wait_ocr(match=re.compile("信赖"), box=self.box.left, time_out=5):
            while self.ocr(match=re.compile("信赖"), box=self.box.left):
                if time.time() - start_time > 10:
                    self.log_info("等待 '信赖' 弹窗超时，进行下一步")
                self.next_frame()
                self.sleep(0.5)
        self.log_info("前往中央环厅")
        if not self.navigate_to_main_hall():
            self.log_info("未到达中央环厅，送礼任务中断")
            return False

        self.log_info("前往干员联络站")
        result = self.navigate_to_operator_liaison_station()

        if result == LiaisonResult.FIND_CHAT_ICON:
            self.log_info("发现干员聊天图标，开始收取或赠送礼物")
            return self.collect_and_give_gifts()

        elif result:
            self.log_info("成功到达干员联络台，开始干员联络任务")
            if self.perform_operator_liaison():
                self.log_info("干员联络完成，开始收取或赠送礼物")
                return self.collect_and_give_gifts()
            else:
                self.log_info("干员联络任务失败")
                return False

        else:
            self.log_info("前往联络站失败")
            return False

    def execute_gift_task(self):
        """送礼任务入口，支持失败重试。"""
        self.info_set("current_task", "give_gift")
        self.log_info("开始执行送礼任务")

        max_retry = self.config.get("送礼任务最多尝试次数", 1)

        for i in range(max_retry):
            self.log_info(f"送礼任务 - 第 {i + 1}/{max_retry} 次尝试")

            success = self.execute_gift_to_liaison()
            if success:
                self.log_info(f"第 {i + 1} 次送礼任务成功")
                return True

            self.log_info(f"第 {i + 1} 次送礼任务失败")

        self.log_info("送礼任务最终失败")
        return False

    def navigate_until_target(
            self,
            target_ocr_pattern,
            nav_feature_name,
            timeout: int = 60,
            pre_loop_callback=None,
            found_special_callback=None,
    ):
        """通用导航循环：识别目标前持续前进并动态对齐。"""
        start_time = time.time()
        short_distance_flag = False
        fail_count = 0

        while not self.wait_ocr(
                match=target_ocr_pattern,
                box=self.box.bottom_right,
                time_out=1,
        ):
            if time.time() - start_time > timeout:
                self.log_info("导航超时")
                return False

            if found_special_callback:
                special_result = found_special_callback()
                if special_result is not None:
                    return special_result

            if pre_loop_callback:
                pre_loop_callback()

            if not short_distance_flag:
                nav = self.find_feature(
                    nav_feature_name,
                    box=self.box_of_screen(
                        (1920 - 1550) / 1920,
                        150 / 1080,
                        1550 / 1920,
                        (1080 - 150) / 1080,
                    ),
                    threshold=0.7,
                )

                if nav:
                    fail_count = 0
                    self.log_info("找到导航路径，继续对齐并前进")

                    self.align_ocr_or_find_target_to_center(
                        ocr_match_or_feature_name_list=nav_feature_name,
                        only_x=True,
                        threshold=0.7,
                        ocr=False,
                    )

                    self.move_keys("w", duration=1)
                else:
                    fail_count += 1
                    self.log_info(f"未找到导航路径，连续失败次数: {fail_count}")

                    if fail_count >= 3:
                        self.log_info("切换短距离移动")
                        short_distance_flag = True

                    self.move_keys("w", duration=0.5)
            else:
                self.move_keys("w", duration=0.5)

            self.sleep(0.5)

        return True

    def detect_ticket_number(self):
        result = self.wait_ocr(match=re.compile(r'^\d{1,3}/\d{1,3}$'),
                               box=self.box_of_screen(1400 / 1920, 0, 1, 70 / 1080), log=True)
        if result:
            ticket = int(result[0].name.split("/")[0])
            self.log_info(f"ticket:{ticket}")
            return ticket
        else:
            return 200

    def battle(self):
        stage_name = self.config.get("体力本")
        category_name = get_stage_category(stage_name)
        self.ensure_main()
        self.press_key('f8', after_sleep=2)
        self.wait_click_ocr(match=re.compile("索引"), time_out=5, after_sleep=2, box=self.box.top, log=True)
        left_ticket = self.detect_ticket_number()
        if self.max_half_time > 0:
            if left_ticket < stages_cost[category_name] - 40:
                self.log_info("体力不足")
                return True
        else:
            if left_ticket < stages_cost[category_name]:
                self.log_info("体力不足")
                return True
        self.to_stage(stage_name, category_name)
        if category_name != "能量淤积点":
            return self.battle_space(left_ticket, category_name)
        else:
            self.log_info("尚未实现能量淤积点功能", notify=True)
            return True

    def battle_space(self, left_ticket, category_name):
        self.wait_click_ocr(match=re.compile("进入"), time_out=5, after_sleep=2, box=self.box.bottom_right, log=True)
        if self.wait_click_ocr(match=re.compile("取消"),time_out=3):
            self.log_info("体力不足")
            return True
        enter_bool = False
        while left_ticket > 0:
            if enter_bool:
                self.wait_click_ocr(match=re.compile("重新挑战"), box=self.box.bottom_left, log=True)
            else:
                self.wait_click_ocr(match=re.compile("进入"), time_out=5, after_sleep=2, box=self.box.bottom_right,
                                    log=True)
                enter_bool = True
            if not self.to_battle():
                return False
            if not self.to_end():
                return False
            left_ticket = self.get_claim(stages_cost[category_name], left_ticket)
            if left_ticket <= 0:
                self.wait_click_ocr(match=re.compile("离开"), box=self.box.bottom_right, log=True, after_sleep=2)
                break
        return True

    def to_stage(self, stage_name, category_name):
        """
        通用关卡进入方法：
        1. 点击左侧类别。
        2. 定位关卡位置。
        3. 点击对应按钮（“前往”或“查看”）。
        4. 自动支持普通关卡和高阶关卡（危境预演）。
        """
        # 点击左侧关卡类别
        self.wait_click_ocr(
            match=re.compile(category_name),
            box=self.box.left,
            log=True,
            after_sleep=2
        )

        # 默认按钮文本
        to_text = "前往"

        # 判断是否是高阶关卡
        is_higher_order = category_name == "危境预演" and stage_name in higher_order_feature_dict
        location = None
        for _ in range(3):
            if is_higher_order:
                # 高阶关卡，使用 feature_dict 查找位置
                location = self.find_one(feature_name=higher_order_feature_dict[stage_name])
            else:
                # 普通关卡
                location = self.wait_ocr(match=re.compile(stage_name), box=self.box.left, log=True)
                if category_name == "能量淤积点":
                    to_text = "查看"
            if location:
                break
            self.scroll_relative(0.5, 0.5, count=-4)

        # 如果找到位置，则点击按钮
        if location:
            self.wait_click_ocr(
                match=re.compile(to_text),
                box=self.box_of_screen(location[0].x / self.width, location[0].y / self.height, 1, 1),
                after_sleep=2
            )

    def to_battle(self):
        end_time = time.time()
        while not self.wait_ocr(match=re.compile("撤离"), time_out=1, box=self.box.top_left, log=True):
            if time.time() - end_time > 30:
                self.log_info("等待超时，进入协议空间超时")
                return False
        while not self.wait_ocr(match=re.compile("触碰"), time_out=1, box=self.box.bottom_right, log=True):
            self.move_keys('w', duration=0.25)
        self.press_key("f")
        return self.auto_battle()

    def to_end(self):
        search_box = self.box_of_screen((1920 - 1550) / 1920, 0, 1550 / 1920, (1080 - 150) / 1080)
        for _ in range(5):
            if self.yolo_detect(fL.battle_end, box=search_box):
                break
            self.click(key="middle", after_sleep=2)
            self.move_keys('a', duration=0.01)
            self.sleep(2)

        self.align_ocr_or_find_target_to_center(
            fL.battle_end,
            ocr=False,
            use_yolo=True,
            box=search_box,
            max_time=5,
            only_x=True,
            raise_if_fail=False,
            threshold=0.5,
        )
        start_time = time.time()
        while self.align_ocr_or_find_target_to_center(fL.battle_end, ocr=False, use_yolo=True, box=search_box,
                                                      only_x=True, threshold=0.5, tolerance=100):
            if time.time() - start_time > 60:
                return False
            if self.wait_ocr(match=re.compile("领取"), time_out=1, box=self.box.bottom_right):
                self.sleep(0.5)
                self.press_key("f", down_time=0.2)
                break
            else:
                self.move_keys('w', duration=0.25)
        return True

    def get_claim(self, ticket_number, sum_ticket_number):
        """
        执行一次领奖操作，并返回剩余票数。

        逻辑：
        1. 等待界面稳定，并找到“可领取”提示。
        2. 尝试点击“获得奖励”，如果失败则本轮减少消耗票数。
        3. 扣除本轮票数，判断剩余票数是否足够。
        4. 点击“领取”，记录领取状态。
        5. 根据剩余票数和 max_half_time 判断下一轮是否可继续。

        返回：
            int: 扣掉本轮消耗票数后的剩余票数，如果票不足则返回 0。
        """
        self.wait_ui_stable(refresh_interval=1)
        start_time = time.time()

        # 等待界面出现“可领取”
        while not self.wait_ocr(match=re.compile("可领取"), box=self.box.top, time_out=1):
            if time.time() - start_time > 60:
                return 0
            self.press_key("f", down_time=0.2)
            self.wait_ui_stable(refresh_interval=1)

        # 本轮默认消耗票数
        need_ticket_number = ticket_number

        # 尝试点击“获得奖励”，失败则本轮减少消耗票数

        if not self.wait_click_ocr(
                match=re.compile("获得奖励"),
                box=self.box_of_screen(530 / 1920, 330 / 1080, 1400 / 1920, 570 / 1080),
                time_out=2,
                log=True,
                after_sleep=2
        ):
            need_ticket_number = ticket_number - 40  # 减耗票逻辑
            self.max_half_time -= 1  # 减耗次数减少

        # 扣除本轮消耗票数
        sum_ticket_number -= need_ticket_number
        if sum_ticket_number < 0:
            return 0  # 票不足，不能继续

        # 点击“领取”，失败则返回0
        if not self.wait_click_ocr(match=re.compile("领取"), box=self.box.bottom_right, time_out=2, log=True):
            self.log_info("领取失败")
            return 0

        # 如果减耗次数用完，下一轮恢复全额消耗
        if self.max_half_time <= 0:
            need_ticket_number = ticket_number

        # 预测下一轮是否还能继续
        next_sum = sum_ticket_number - need_ticket_number
        # 返回本轮剩余票数，不返回next_sum，因为减耗只用于判断下一轮可否继续
        return sum_ticket_number
