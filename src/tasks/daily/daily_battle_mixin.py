import math
import re
import time

import win32gui

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import stages_cost, higher_order_feature_dict
from src.data.world_map import stages_dict, stages_list
from src.data.world_map_utils import get_stage_category
from src.tasks.BaseEfTask import back_window
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.map_mixin import MapMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin

gather_list = stages_dict["能量淤积点"]


class DailyBattleMixin(MapMixin, ZipLineMixin, BattleMixin, Common):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gather_near_transfer_point_dict = dict()
        self.stages_list = stages_list
        # 下列代码在 AutoCombatTask.py 中有部分重复。如有更新，请两边一起修改。
        # 不要试图归并，否则会影响『日常任务』中的选项顺序。
        self.default_config.update({
            "⭐刷体力": True,
            "消耗限时体力药": False,
            "体力本": "干员经验",
            "仅站桩": False,
            **{key: "" for key in gather_list},
            "技能释放": "123",
            "启动技能点数": 2,
            "后台结束战斗通知": True,
            "无数字操作间隔": 6,
            "进入战斗后的初始等待时间": 3,
            "启用排轴": False,
            "排轴序列": "ult_2,1,e,ult_3,sleep_8",
        })
        self.config_description.update({
            "⭐刷体力": "是否消耗所有「理智」刷取培养材料。",
            "消耗限时体力药": "如果勾选，那么对于随机某项 m 个限时 n 天的体力药，使用其中的 2*m/n 个（向上取整）。",
            "体力本": "刷取哪个副本。所选副本必须领完所有等级的首通奖励。",
            "仅站桩": "若启用，则开始挑战后角色原地不动（不输出），仅对「重度能量淤积点」生效。可以用于建好防御塔情形，避免角色离开副本区域。",
            **{key: "需要设好「预刻写属性」。默认留空表示直线前往，更多用法参见 ./docs/体力本.md > 能量淤积点 。" for key in
               gather_list},
        })
        self.config_type["体力本"] = {"type": "drop_down", "options": self.stages_list}

    def battle(self):
        stage_name = self.config.get("体力本")
        category_name = get_stage_category(stage_name)
        self.ensure_main()
        self.press_key("f8")
        self.wait_click_ocr(match=re.compile("索引"), time_out=7, after_sleep=2, box=self.box.top, log=True)
        if self.config.get("消耗限时体力药", False):
            self.click(3530/3840, 80/2160, after_sleep=2)  # 右上角加号
            box_list = self.ocr(x=0.28, y=0.45, to_x=0.88, to_y=0.66, match=re.compile(r"(\d+)天"))
            if len(box_list) <= 0:
                self.log_error("未找到应急理智加强剂，剩余天数未识别")
            else:
                box = box_list[0]
                validity  = float(re.findall(r'(\d+)', box.name)[0])
                box_list=self.ocr(x=box.x/self.width+0.04, y=box.y/self.height+0.14, to_x=box.x/self.width+0.08, to_y=box.y/self.height+0.18, match=re.compile(r"(\d+)"))
                if len(box_list) <= 0:
                    self.log_info("数量未识别，按照1个处理")
                    count = 1
                else:
                    count = float(re.findall(r'(\d+)', box_list[0].name)[0])
                consume = min(max(1, math.ceil(2 * count / validity)), count)
                self.log_error(f"找到 {int(count)} 个限时 {int(validity)} 天的 应急理智加强剂，本次预计使用 {consume} 个")
                #
                for i in range(consume):
                    self.click(box)
                if not self.wait_click_ocr(match=re.compile("确认"), box=self.box.bottom_right, after_sleep=2):
                    self.log_error("无法使用 应急理智加强剂")
                else:
                    self.log_error(f"已使用 {consume} 个 应急理智加强剂")
                    self.wait_pop_up()
            if not self.safe_back(re.compile("干员"), box=self.box.top_left, time_out=10, ocr_time_out=2):
                return False
        left_ticket = self.detect_ticket_number()
        self.log_info(f"当前体力: {left_ticket}")
        if left_ticket < stages_cost[category_name]:
            self.log_info("体力不足")
            return True
        if not self.to_stage(stage_name, category_name):
            return False
        if category_name != "能量淤积点":
            return self.battle_space(left_ticket, category_name)
        else:
            return self.battle_gather(left_ticket, stage_name, category_name,
                                      no_battle=self.config.get("仅站桩", False))

    def battle_gather(self, left_ticket, stage_name, category_name, no_battle=False):
        self.gather_near_transfer_point_dict["枢纽区"] = self.box.top
        self.gather_near_transfer_point_dict["源石研究园"] = self.box.top
        self.gather_near_transfer_point_dict["矿脉源区"] = self.box.right
        self.gather_near_transfer_point_dict["供能高地"] = self.box.bottom_right
        self.gather_near_transfer_point_dict["武陵城"] = self.box.top
        self.gather_near_transfer_point_dict["清波寨"] = self.box.top
        if result := self.wait_ocr(match=re.compile("追踪"), box=self.box.bottom_right, time_out=5):
            if "追踪" in result[0].name and "取" not in result[0].name and "消" not in result[0].name:
                self.log_info("点击追踪按钮")
                self.click(result, after_sleep=2)
        self.to_near_transfer_point(self.gather_near_transfer_point_dict[stage_name])
        self.ensure_main()
        zip_line_str = self.config.get(stage_name)
        if zip_line_str:
            self.press_key("f", after_sleep=2)
            zip_line_list = [int(i) for i in zip_line_str.split(",")]
            self.zip_line_list_go(zip_line_list)
        self.navigate_until_target(target_ocr_pattern=re.compile("激发"), nav_feature_name=fL.gather_icon_out_map,
                                   time_out=60)
        result = self.wait_ocr(match=re.compile("激发"), box=self.box.bottom_right, time_out=5)
        if not result:
            self.log_info("没有找到激发按钮")
            return False
        else:
            self.sleep(1)
            result = self.wait_ocr(match=re.compile("激发"), box=self.box.bottom_right, time_out=5)
        self.click_with_alt(result)
        return self.battle_recycle(left_ticket, category_name, "挑战", no_battle=no_battle, challenge_check=True)

    def battle_space(self, left_ticket, category_name):
        self.wait_click_ocr(match=re.compile("进入"), time_out=5, after_sleep=2, box=self.box.bottom_right, log=True)
        if self.wait_click_ocr(match=re.compile("取消"), time_out=5, box=self.box.bottom_left, log=True):
            self.log_info("没有进入战斗，可能是因为已经没理智了")
            return True
        return self.battle_recycle(left_ticket, category_name, "进入")

    def battle_recycle(self, left_ticket, category_name, enter_str, no_battle=False, challenge_check=False):
        enter_bool = False
        while left_ticket > 0:
            if enter_bool:
                self.wait_click_ocr(match=re.compile("重新挑战"), box=self.box.bottom_left, log=True, time_out=5,
                                    after_sleep=2, recheck_time=1)
            else:
                self.wait_click_ocr(match=re.compile(enter_str), time_out=10, after_sleep=2, box=self.box.bottom_right,
                                    log=True, recheck_time=1)
                enter_bool = True
            if not self.to_battle(no_battle=no_battle, challenge_check=challenge_check):
                return False
            if not self.to_end(challenge=challenge_check):
                return False
            left_ticket = self.get_claim(stages_cost[category_name], left_ticket)
            self.sleep(2)
            if left_ticket <= 0:
                self.wait_click_ocr(match=re.compile("离开"), box=self.box.bottom_right, log=True, recheck_time=1)
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
            after_sleep=2,
            time_out=6
        )

        # 默认按钮文本
        to_text = "前往"
        if category_name == "能量淤积点":
            to_text = "查看"
        # 判断是否是高阶关卡
        is_higher_order = category_name == "危境预演"
        for _ in range(5):
            if is_higher_order:
                # 高阶关卡，使用 feature_dict 查找位置
                location = self.find_feature(feature_name=higher_order_feature_dict[stage_name])
            else:
                # 普通关卡
                location = self.wait_ocr(match=re.compile(stage_name if stage_name != '源石研究园' else '源石研究'),
                                         box=self.box.left, log=True, time_out=5)
                # 「重度能量淤积点·源石研究园」会被居中指针挡住 “园”

            if location:
                enter_bool = self.wait_click_ocr(
                    match=re.compile(to_text),
                    box=self.box_of_screen(location[0].x / self.width, location[0].y / self.height, 1, 1),
                    after_sleep=2,
                    time_out=6,
                )
                if enter_bool:
                    return True
            self.scroll_relative(650 / 1920, 0.5, count=-2)
            self.wait_ui_stable(refresh_interval=0.5)
        return False
        # 如果找到位置，则点击按钮

    def to_battle(self, no_battle: bool = False, challenge_check=False):
        if not challenge_check:
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=re.compile("撤离"), time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 300:
                    self.log_info("等待超时，进入协议空间超时")
                    return False
            prev = win32gui.GetForegroundWindow()
            while not self.wait_ocr(match=re.compile("触碰"), time_out=1, box=self.box.bottom_right, log=True):
                self.move_keys('w', duration=0.25)
            back_window(prev)
            self.press_key("f")
        else:
            self.wait_pop_up(time_out=4)
            end_time = time.time()
            while not self.wait_ocr(match=re.compile("挑战"), time_out=1, box=self.box.top_left, log=True):
                if time.time() - end_time > 30:
                    self.log_info("等待超时，进入挑战超时")
                    return False
        return self.auto_battle(no_battle=no_battle)

    def to_end(self, challenge=False):
        prev = win32gui.GetForegroundWindow()
        if challenge:
            end_feature_name = fL.gather_icon_out_map2
            use_yolo = False
            search_box = None
        else:
            end_feature_name = "battle_end"
            use_yolo = True
            search_box = self.box_of_screen((1920 - 1550) / 1920, 0, 1550 / 1920, (1080 - 150) / 1080)
        for _ in range(9):
            if challenge:
                if self.find_feature(
                        end_feature_name,
                        box=self.box_of_screen((1920 - 1550) / 1920, 150 / 1080, 1550 / 1920, (1080 - 150) / 1080),
                ):
                    break
            else:
                if self.yolo_detect(end_feature_name, box=search_box):
                    break
            self.click(key="middle", after_sleep=2)
            self.move_keys("aw", duration=0.1)
            self.sleep(1)

        self.align_ocr_or_find_target_to_center(
            end_feature_name,
            ocr=False,
            use_yolo=use_yolo,
            box=search_box,
            max_time=5,
            only_x=True,
            raise_if_fail=False,
            threshold=0.5,
        )
        start_time = time.time()
        while self.align_ocr_or_find_target_to_center(end_feature_name, ocr=False, use_yolo=use_yolo, box=search_box,
                                                      only_x=True, threshold=0.5, tolerance=100):
            if time.time() - start_time > 60:
                return False
            if self.wait_ocr(match=re.compile("领取"), time_out=1, box=self.box.bottom_right):
                self.sleep(0.5)
                self.press_key("f", down_time=0.2)
                break
            else:
                self.move_keys('w', duration=0.25)
        back_window(prev)
        return True

    def get_claim(self, ticket_number, sum_ticket_number):
        """
        执行一次领奖操作，并返回剩余理智。

        逻辑：
        1. 等待界面稳定，并找到“可领取”提示。
        2. 尝试点击“获得奖励”，如果失败则本轮任务失败。
        3. 扣除本轮理智，判断剩余理智是否足够。
        4. 点击“领取”，记录领取状态。

        返回：
            int: 扣掉本轮消耗理智后的剩余理智，如果理智不足则返回 0。
        """
        self.log_info("领取奖励,当前理智: {}, 本轮消耗理智: {}".format(sum_ticket_number, ticket_number))
        self.wait_ui_stable(refresh_interval=1)
        start_time = time.time()

        # 等待界面出现“可领取”
        while not self.wait_ocr(match=re.compile("可领取"), box=self.box.top, time_out=1):
            if time.time() - start_time > 60:
                return 0
            self.press_key("f", down_time=0.2)
            self.wait_ui_stable(refresh_interval=1)

        # 本轮默认消耗理智
        need_ticket_number = ticket_number

        # 尝试点击“获得奖励”，失败则本轮减少消耗理智
        if not self.wait_click_ocr(
                match=re.compile("获得奖励"),
                box=self.box_of_screen(530 / 1920, 330 / 1080, 1400 / 1920, 570 / 1080),
                time_out=2,
                after_sleep=1,
                log=True
        ):
            self.log_info("未找到 '获得奖励' 按钮, 任务失败")
            return 0

        # 扣除本轮消耗理智
        sum_ticket_number -= need_ticket_number
        self.log_info("扣除本轮消耗理智: {}, 剩余理智: {}".format(need_ticket_number, sum_ticket_number))
        if sum_ticket_number < 0:
            return 0  # 理智不足，不能继续

        # 点击“领取”，失败则返回0
        self.next_frame()
        if not self.wait_click_ocr(match=re.compile("领取"), box=self.box.bottom_right, time_out=2, log=True):
            self.log_info("领取失败")
            return 0
        # 预测下一轮是否还能继续
        next_sum = sum_ticket_number - need_ticket_number
        self.log_info("预测下一轮消耗理智: {}, 预测下一轮剩余理智: {}".format(need_ticket_number, next_sum))

        if next_sum < 0:
            self.log_info("下一轮理智不足，无法继续")
            return 0
        else:
            # 返回本轮剩余理智，不返回next_sum，因为减耗只用于判断下一轮可否继续
            return sum_ticket_number
