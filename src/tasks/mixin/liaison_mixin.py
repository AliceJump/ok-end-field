"""
LiaisonMixin

负责干员联络自动化流程，包括：

主要功能：
1. 传送到帝江号
2. 导航至中央环厅
3. 导航至干员联络站
4. 执行干员联络
5. 收取礼物
6. 赠送礼物

依赖模块：
- NavigationMixin: 提供路径导航能力
- OCR识别
- Feature识别
"""

import random
import re
import time

from src.data.FeatureList import FeatureList as fL
from src.data.characters_utils import get_contact_list_with_feature_list
from src.tasks.BaseEfTask import BaseEfTask
from src.tasks.mixin.common import LiaisonResult, build_name_patterns
from src.tasks.mixin.navigation_mixin import NavigationMixin


class LiaisonMixin(NavigationMixin):
    """
    干员联络任务 Mixin。

    提供完整的干员联络自动化流程，包括：
    - 帝江号传送
    - 联络站导航
    - 干员联络
    - 礼物交互

    属性:
        can_contact_dict (dict):
            可联络干员字典
            {角色名: feature_name}

        contact_name_patterns (dict):
            OCR匹配角色名字的正则表达式
    """

    def __init__(self, *args, **kwargs):
        """初始化联络系统"""
        super().__init__(*args, **kwargs)

        # 获取所有可联络干员
        self.can_contact_dict = get_contact_list_with_feature_list()

        # 为每个干员构建 OCR 名称匹配规则
        self.contact_name_patterns = {
            name: build_name_patterns(name)
            for name in self.can_contact_dict.keys()
        }

    def transfer_to_home_point(self, should_check_out_boat=False):
        """
        通过地图传送到帝江号指定点。

        流程:
        1. 打开地图
        2. 找到帝江号
        3. 进入帝江号区域
        4. 点击传送点
        5. 点击传送按钮

        Returns:
            bool: 是否成功传送
        """
        self.ensure_main()

        self.log_info("开始传送到帝江号")

        # 打开地图
        self.ensure_map()
        self.log_info("打开地图界面 (按下 M)")

        # 查找帝江号区域
        target_area = self.wait_ocr(
            match=re.compile("帝江号"),
            box=self.box.top,
            time_out=5
        )
        if should_check_out_boat:
            if target_area[0].x < self.width / 2:
                self.log_info("已在帝江号区域内，无需传送")
                self.ensure_main()
                return True
        if not target_area:
            self.log_info("未找到帝江号区域，传送失败")
            return False
        self.log_info("找到帝江号区域，点击进入")
        self.click(target_area, after_sleep=2)

        # 查找传送点
        tp_icon = self.find_feature(
            feature_name="transfer_point",
            box=self.box.left,
            threshold=0.7
        )

        if not tp_icon:
            self.log_info("未找到传送点图标，传送失败")
            return False

        self.log_info("找到传送点图标，点击传送点")
        self.click(tp_icon)

        # 查找传送按钮
        transfer_btn = self.wait_ocr(
            match="传送",
            box=self.box.bottom_right,
            time_out=10,
            log=True
        )

        if not transfer_btn:
            self.log_info("未找到传送按钮，传送失败")
            return False

        self.log_info("找到传送按钮，点击进行传送")
        self.click(transfer_btn)

        # 等待传送完成
        self.log_info("等待传送完成，检查舰桥界面")

        if not self.wait_ocr(
                match="舰桥",
                box=self.box.left,
                time_out=60,
                log=True
        ):
            pass

        self.log_info("传送完成，已到达帝江号舰桥")
        return True

    def navigate_to_main_hall(self) -> bool:
        """
        前往中央环厅。

        通过短距离前进移动触发区域更新，
        OCR检测当前位置是否为中央环厅。

        Returns:
            bool: 是否成功到达
        """
        self.log_info("开始前往中央环厅")

        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            self.log_info(f"第 {attempt}/{max_attempts} 次尝试移动前进")

            self.move_keys("w", duration=1)

            if self.wait_ocr(
                    match="中央环厅",
                    box=self.box.left,
                    log=True
            ):
                self.log_info("已到达中央环厅")
                return True

        self.log_info("前往中央环厅可能失败，尝试后续操作")
        return True
    def navigate_to_operator_liaison_station(self):
        """
        自动导航到干员联络站。

        Returns:
            LiaisonResult | bool
        """
        self.log_info("开始前往干员联络站")
        self.ensure_map()
        self.log_info("打开地图界面")

        if not self.start_tracking_and_align_target(
                fL.operator_liaison_station,
                fL.operator_liaison_station_out_map
        ):
            return False

        def special_chat_detect():
            """
            在导航过程中检测是否出现干员交互图标
            """

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
            time_out=60,
            found_special_callback=special_chat_detect,
        )

    def perform_operator_liaison(self):
        """
        执行一次干员联络流程。

        Returns:
            bool: 是否成功完成联络
        """
        self.log_info("开始执行干员联络任务")

        target_name = self.config.get("优先送礼对象")
        target_feature_name = self.can_contact_dict[target_name]

        search_char_box = self.box_of_screen(
            795 / 1920,
            248 / 1080,
            1687 / 1920,
            764 / 1080
        )

        find_name_patterns = []

        for attempt in range(1, 11):

            self.log_info(f"第 {attempt}/10 次尝试打开信任度界面")

            self.press_key('f')
            self.wait_ocr(match=re.compile("干员"),box=self.box.top_left, time_out=5)
            result = {}
            found_target = False

            for _ in range(3):

                self.next_frame()

                found = self.find_one(
                    feature_name=target_feature_name,
                    box=search_char_box,
                    threshold=0.7
                )

                if found:
                    result[target_feature_name] = found
                    found_target = True
                    break

                self.scroll_relative(0.5, 0.5, -3)
                self.wait_ui_stable(refresh_interval=0.5)

            if not found_target:

                self.ensure_main()
                self.press_key('f')
                self.wait_ocr(match=re.compile("干员"), box=self.box.top_left, time_out=5)
                self.log_info(f"未找到联络对象 {target_name}，尝试其他目标")

                other_results = {}

                for _, other_feature in self.can_contact_dict.items():

                    if other_feature == target_feature_name:
                        continue

                    self.next_frame()

                    found = self.find_one(
                        feature_name=other_feature,
                        box=search_char_box,
                        threshold=0.7
                    )

                    if found:
                        other_results[other_feature] = found

                if other_results:
                    feature = random.choice(list(other_results.keys()))
                    result[feature] = other_results[feature]

            if not result:
                self.log_info("未找到任何可联络对象")
                return False

            find_feature_name = next(iter(result))

            find_name = next(
                k for k, v in self.can_contact_dict.items()
                if v == find_feature_name
            )

            find_name_patterns = self.contact_name_patterns.get(
                find_name,
                build_name_patterns(find_name)
            )

            self.log_info("找到联络对象")

            self.click(list(result.values())[0], after_sleep=0.5)

            if not self.wait_click_ocr(
                    match=re.compile("确认联络"),
                    box=self.box.bottom_right,
                    time_out=5,
                    log=True,
                    after_sleep=2,
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

            if not self.wait_ocr(
                    match=find_name_patterns,
                    box=self.box.top,
                    time_out=2,
                    log=True
            ):
                self.log_info(f"未找到 {find_name} 的名字,重新打开联络界面")
                self.ensure_main()
                continue

            self.next_frame()

            if chat_box := self.ocr(
                    match=find_name_patterns,
                    box=self.box.bottom_right
            ):
                return self.click_chat_box(find_name_patterns, chat_box)

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
                return self.click_chat_box(find_name_patterns, chat_box)

            self.move_keys("w", duration=0.5)

            self.log_info("未找到干员，继续前进移动")

            if time.time() - start_time > 5:
                self.log_info("长时间未找到干员，任务超时")
                return False

        return False

    def click_chat_box(self, find_name_patterns, chat_box):
        """点击干员聊天交互框"""
        self.log_info("发现干员，点击进行交互")

        self.send_key_down("alt")
        self.sleep(0.5)

        self.click_box(chat_box, after_sleep=0.5)

        self.next_frame()

        self.wait_click_ocr(
            match=find_name_patterns,
            box=self.box.bottom_right,
            time_out=1
        )

        self.send_key_up("alt")

        self.log_info("干员联络完成")

        return True

    def collect_and_give_gifts(self):
        """
        自动执行收礼与送礼流程。

        Returns:
            bool: 是否成功完成
        """
        self.log_info("开始收取或赠送礼物")

        start_time = time.time()

        while True:

            if time.time() - start_time > 10:
                self.log_info("等待 收下/赠送 超时")
                return False

            self.click(0.5, 0.5, after_sleep=0.5)

            result = self.wait_click_ocr(
                match=[re.compile("收下"), re.compile("赠送")],
                box=self.box.bottom_right,
                time_out=1,
                after_sleep=0.5,
            )

            if result:
                self.log_info(f"找到按钮: {result[0].name}")
                break

        if result and len(result) > 0 and "收下" in result[0].name:

            self.log_info("开始收下礼物")

            self.skip_dialog(
                end_list=[re.compile("ms")],
                end_box=self.box.bottom_left
            )

            self.wait_click_ocr(
                match=re.compile("确认"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=0.5,
            )

            self.press_key('f', after_sleep=0.5)

            start_time = time.time()

            while True:

                if time.time() - start_time > 10:
                    self.log_info("等待 收下/赠送 超时")
                    return False

                self.click(0.5, 0.5, after_sleep=0.5)

                result = self.wait_click_ocr(
                    match=[re.compile("赠送")],
                    box=self.box.bottom_right,
                    time_out=2,
                )

                if result:
                    self.log_info("收下完成，准备赠送礼物")
                    break
        self.wait_ocr(match=re.compile("默认"), box=self.box.bottom_left, time_out=5)
        self.click(144 / 1920, 855 / 1080)
        self.log_info("点击赠送礼物位置")
        self.log_info("本次成功")

        if self.wait_click_ocr(
                match=re.compile("确认赠送"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=0.5,
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
                )

                if result:
                    self.log_info("赠送完成，点击离开")
                    break

            self.log_info("成功赠送礼物")

            return True

        else:

            self.back(after_sleep=2)
            self.back(after_sleep=2)

            self.wait_click_ocr(
                match=re.compile("确认"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2
            )

        self.log_info("赠送礼物失败")

        return False
