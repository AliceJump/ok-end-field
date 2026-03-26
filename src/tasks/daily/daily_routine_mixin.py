import re
import time

from src.data.world_map import areas_list, outpost_dict, goods_dict
from src.data.world_map_utils import get_area_by_outpost_name, get_goods_by_outpost_name
from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.data.FeatureList import FeatureList as fL
from src.data.characters_utils import get_contact_list_with_feature_list


class DailyRoutineMixin(LiaisonMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "⭐据点兑换": True,
            "交易货品优先序列": "",
            "⭐转交运送委托": True,
            "⭐转交委托奖励领取": True,
            "⭐造装备": True,
            "⭐收信用": True,
            "尝试仅收培育室": True,
            "⭐收集线索": True,
            "⭐制造舱": True,
            "⭐周常奖励": True,
            "⭐日常奖励": True,
        })
        self.config_description.update({
            "⭐据点兑换": "是否在「地区建设/据点管理」中通过交易获得调度券。",
            "交易货品优先序列": "默认留空，交易货品顺序随机。更多用法参见 ./docs/日常任务.md > 优先货品交易序列 。",
            "⭐转交运送委托": "是否在「地区建设/仓储结点」中转交全部运送委托。",
            "⭐转交委托奖励领取": "是否领取「地区建设/仓储结点/我转交的委托」。超时委托也会被领取。",
            "⭐造装备": "是否前往「装备制造/套组装备制造」并制作一件列表首位的装备。请确保有足够的装备原件和调度券。",
            "⭐收信用": "是否前往好友的「帝江号」并在「访客终端」上进行助力获得信用。助力结束后，前往「采购中心/信用交易所」收取全部助力。",
            "尝试仅收培育室": "若选项开启，则优先尝试仅助力好友「帝江号」上的「培养仓」。如果不能，至少助力一次其它舱室。",
            "⭐收集线索": "是否前往「帝江号/会客室」收集全部线索。若集齐线索，则开启情报交流。",
            "⭐制造舱": "是否前往「帝江号/制造仓」收取培养材料。收取后会补足待制造数量。",
            "⭐周常奖励": "是否领取「活动中心/每周事物」中的奖励。",
            "⭐日常奖励": "是否领取「行动手册/日常」中的奖励。",
        })
       
    def wait_friend_list(self, end_icon_name="friend_chat_icon"):
        start_time = time.time()
        while True:
            if time.time() - start_time > 20:
                self.log_info("加载好友列表超时")
                return False
            if self.find_feature(feature_name=end_icon_name):
                return True

    def collect_credit(self):
        self.info_set("current_task", "collect_credit")
        self.press_key("f5", after_sleep=2)
        self.wait_click_ocr(match=re.compile("信用交易所"), box=self.box.top, time_out=5, after_sleep=2)
        result = self.wait_click_ocr(match=[re.compile("收取信用"), re.compile("无待领取信用")],
                                     box=self.box.bottom_left,
                                     time_out=5, after_sleep=2)
        if not result:
            self.log_info("未找到可收取信用或无待领取信用的选项")
            return False
        if "收取信用" in result[0].name:
            self.wait_pop_up(after_sleep=2)
        self.ensure_main()
        self.back(after_sleep=2)
        left_exchange_time = 5
        left_help_time = 5
        exchange_time = 0
        help_time = 0
        is_first_time = True
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        exchange_not_found = False
        count = 0
        while True:
            if count >= 10:
                self.log_info("循环过多次仍未找到交流或助力对象，可能出现异常，结束拜访")
                return False
            if is_first_time:
                self.wait_click_ocr(match=re.compile("好友"), box=self.box.right, time_out=5, after_sleep=2)
            else:
                if left_exchange_time <= 0 and left_help_time <= 0:
                    self.wait_click_ocr(match=re.compile("结束拜访"), box=self.box.bottom_right, time_out=5,
                                        after_sleep=2)
                    self.log_info("交流和助力次数已完成，结束拜访")
                    self.wait_click_ocr(match=re.compile("确认"), box=self.box.bottom_right, time_out=5, after_sleep=2)
                    if exchange_not_found:
                        self.log_info("未完全找满交流对象，可能存在部分交流次数未完成")
                    self.info_set("exchange_time", exchange_time)
                    self.info_set("help_time", help_time)
                    return True

            result = None
            self.wait_ui_stable(refresh_interval=0.5)
            start_time = time.time()
            scroll_count = 0
            while not result:
                if is_first_time or scroll_count > 0:
                    span_box = self.box_of_screen(3400 / 3840, 301 / 2160, 3692 / 3840, 1883 / 2160)
                else:
                    span_box = self.box_of_screen(3400 / 3840, 615 / 2160, 3692 / 3840, 1883 / 2160)
                if time.time() - start_time > 40:
                    self.log_info("找不到可交流或助力的玩家")
                    return False
                if left_exchange_time > 0:
                    result = self.find_feature(
                        feature_name="can_exchange_info_icon", box=span_box
                    )
                    if scroll_count >= 7:
                        self.back(after_sleep=2)
                        self.ensure_in_friend_boat()
                        self.press_key('f', after_sleep=2)
                        self.wait_ui_stable(refresh_interval=0.5)
                        left_exchange_time = 0
                        exchange_not_found = True
                        continue
                elif left_help_time > 0:
                    result = self.find_feature(
                        feature_name="can_help_icon", box=span_box
                    )
                if not result:
                    scroll_count += 1
                    self.scroll_relative(0.5, 0.5, -4)
                    self.wait_ui_stable(refresh_interval=1)

            self.click(result, after_sleep=2)
            self.wait_click_ocr(match=re.compile("确定"), box=self.box.bottom_right, time_out=5, after_sleep=2)
            if not self.ensure_in_friend_boat():
                self.log_info("未能进入好友帝江号")
                return False
            self.sleep(2)
            actions = []
            if left_exchange_time > 0:
                actions.append("交流")
            if left_help_time > 0:
                actions.append("助力")
            self.log_info(f"已进入好友帝江号，准备进行{''.join(actions)}操作")
            self.press_key("y", after_sleep=2)
            if left_exchange_time > 0:
                self.wait_click_ocr(match=re.compile("情报交流"), box=exchange_help_box, time_out=5, after_sleep=2)
                left_exchange_time -= 1
                exchange_time += 1
            if left_help_time > 0:
                result = self.wait_ocr(match=re.compile("生产助力"), box=exchange_help_box, time_out=5)
                if result:
                    for res in result:
                        if not self.config.get("尝试仅收培育室"):
                            self.click(res, after_sleep=2)
                            left_help_time -= 1
                            help_time += 1
                            if left_help_time <= 0:
                                break
                        if res == result[-1]:
                            self.scroll_relative(res.x / self.width, res.y / self.height, count=-8)
                            self.wait_ui_stable(refresh_interval=0.5)
                            if result := self.wait_ocr(match=re.compile("生产助力"), box=exchange_help_box, time_out=5):
                                self.log_info("继续进行助力操作")
                                self.click(result[-1], after_sleep=2)
                                if self.find_reward_ok():
                                    self.wait_pop_up(after_sleep=2)
                                left_help_time -= 1
                                help_time += 1
            select_visit_deadline = time.time() + 30
            while not self.wait_click_ocr(match=re.compile("选择拜访"), box=self.box.top_left, time_out=1):
                if time.time() > select_visit_deadline:
                    self.log_info("等待 '选择拜访' 超时，结束本轮好友流程")
                    return False
                self.back(after_sleep=2)
            is_first_time = False
            count += 1

    def claim_delivery_rewards(self):
        self.info_set("current_task", "claim_delivery_rewards")
        self.log_info("开始领取转交委托奖励")

        area = areas_list[0]
        self.to_model_area(area, "仓储节点")

        if not self.wait_click_ocr(
                match=re.compile("我转交的委托"),
                box=self.box.top_left,
                time_out=5,
                after_sleep=2,
        ):
            self.log_info(f"'未找到我转交的委托'节点，返回主界面")
            self.ensure_main()

        results = self.wait_ocr(
            match=re.compile("一键领取"),
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
                        match=re.compile("本地仓储节点"),
                        box=self.box.top_left,
                        time_out=5,
                        after_sleep=2,
                ):
                    self.log_info(f"{area}未找到本地仓储节点，返回主界面")
                    self.ensure_main()
                    break

                results = self.wait_ocr(
                    match=[re.compile("货物装箱"), re.compile("查看报价")],
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
                start_index = 0 if not ("查看报价" in results[0].name) else 2
                steps = [
                    ("下一步", self.box.bottom_right),
                    ("填充至满", self.box.top_right),
                    ("下一步", self.box.bottom_right),
                    ("开始运送", self.box_of_screen(1548 / 1920, 951 / 1080, 1, 1)),
                    ("获得调度券", self.box.bottom_right)
                ]

                for i in range(start_index, len(steps)):
                    step = steps[i]
                    match = step[0]
                    box = step[1]
                    timeout = 12 if i > 2 else 5
                    res = None
                    for time_index in range(2):
                        self.next_frame()
                        if time_index == 0:
                            res = self.wait_ocr(match=re.compile(match), box=box, time_out=timeout, log=True)
                        else:
                            res = self.ocr(match=re.compile(match), box=box,
                                           frame_processor=self.make_hsv_isolator(hR.DARK_GRAY_TEXT), log=True)
                            if not res:
                                res = self.ocr(match=re.compile(match), box=box,
                                               frame_processor=self.make_hsv_isolator(hR.WHITE), log=True)
                        if res:
                            self.log_info(f"找到步骤 {match}，继续下一步")
                            self.sleep(1)
                            self.click(res[0], after_sleep=2)
                            break

                    if not res and i != 2:
                        self.log_info(f"步骤 {match} 未找到，跳过本次活动")
                        break
                self.ensure_main()
                self.press_key("v", after_sleep=1)
                self.press_key("j", after_sleep=1)

                if not self.wait_click_ocr(
                        match=re.compile("转交运送委托"),
                        box=self.box.bottom_left,
                        time_out=5,
                        after_sleep=2,
                ):
                    self.log_info(
                        "未找到 '转交运送委托' 按钮，跳过本次活动"
                    )
                    self.ensure_main()
                    break
                if not self.wait_click_ocr(
                        match=re.compile("确认"),
                        box=self.box.bottom_right,
                        time_out=5,
                        after_sleep=2,
                ):
                    self.log_info("未找到 '确认' 按钮，跳过本次活动")
                    self.ensure_main()
                    break

                self.sleep(2)
                count += 1
                self.log_info(f"{area} 已完成 {count}/{activity_num} 次转交")

    def read_outpost_ticket_num(self, outpost_name):
        num_str = self.wait_ocr(
            match=re.compile(r"\d+"),
            box=self.box_of_screen(
                1224 / 1920,
                235 / 1080,
                1551 / 1920,
                356 / 1080,
            ),
            time_out=5,
        )

        num = 0
        if num_str and hasattr(num_str[0], "name"):
            try:
                num = int(num_str[0].name)
            except ValueError:
                num = 0

        self.log_info(f"{outpost_name} 据点当前券数量: {num}")
        return num

    def perform_outpost_exchange(self, outpost_name, priority_list=[]):
        """据点内循环尝试更换货品并兑换。"""
        self.log_info(f"开始处理据点: {outpost_name}")

        self.wait_click_ocr(
            match=outpost_name,
            box=self.box.top,
            time_out=5,
            after_sleep=2,
        )

        can_exchange_goods = goods_dict.get(
            get_area_by_outpost_name(outpost_name), []
        )

        goods_patterns = [
            re.compile(i) for i in get_goods_by_outpost_name(outpost_name)
        ]

        max_attempts = 7
        skip_goods = set()

        for attempt in range(1, max_attempts + 1):
            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            self.log_info(f"尝试第 {attempt}/{max_attempts} 次更换货品")
            self.wait_click_ocr(match="更换货品", after_sleep=2)

            goods = self.wait_ocr(
                match=goods_patterns,
                time_out=5,
            )

            if not goods:
                self.log_info(f"{outpost_name} 没有可兑换的货物")
                break

            if len(priority_list) > 0:
                goods_name = list(map(lambda g: g.name, goods))
                for p in reversed(priority_list):
                    if p in goods_name:
                        goods.insert(0, goods.pop(goods_name.index(p)))

            exchange_good = None
            for good in goods:
                standard_name = next(
                    (
                        kw for kw in sorted(can_exchange_goods, key=len, reverse=True)
                        if (kw in good.name or good.name in kw)
                           and len(good.name) >= max(2, len(kw) - 1)
                    ),
                    None
                )

                if not standard_name:
                    self.log_info(f"未知货物: {good.name}，跳过")
                    continue

                if good.name != standard_name:
                    self.log_info(
                        f"修正 OCR 识别结果: '{good.name}' -> '{standard_name}'"
                    )
                    good.name = standard_name

                if standard_name in skip_goods:
                    self.log_info(f"跳过已处理货物: {standard_name}")
                    continue

                skip_goods.add(standard_name)
                exchange_good = good
                self.log_info(f"成功锁定兑换目标: {standard_name}")
                break

            if not exchange_good:
                self.log_info(f"{outpost_name} 本轮没有可兑换目标")
                break

            self.log_info(f"选择货物进行兑换: {exchange_good.name}")
            self.click(exchange_good, after_sleep=2)

            self.wait_click_ocr(
                match=re.compile("确认"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
            )

            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            plus_button = self.find_one(
                feature_name="plus_button",
                box=self.box.bottom_right,
                threshold=0.8,
            )

            if not plus_button:
                continue

            self.log_info("找到加号按钮，执行点击")
            self.click(plus_button, down_time=12, after_sleep=0)

            self.wait_click_ocr(
                match="交易",
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
            )

            self.wait_pop_up(after_sleep=2)

        self.log_info(f"{outpost_name} 兑换操作完成")

    def test_ocr_full(self):
        self.next_frame()
        self.ocr(log=True)

    def test_ocr(self):
        box1 = self.box_of_screen(1749 / 1920, 107 / 1080, 1789 / 1920, 134 / 1080)
        box2 = self.box_of_screen(
            (1749 + (1832 - 1750)) / 1920, 107 / 1080, (1789 + (1832 - 1750)) / 1920, 134 / 1080
        )
        self.wait_click_ocr(
            match=re.compile(r"^\d+/5$"),
            after_sleep=2,
            time_out=2,
            box=box1,
            log=True,
        )
        self.wait_click_ocr(
            match=re.compile(r"^\d+/5$"),
            after_sleep=2,
            time_out=2,
            box=box2,
            log=True,
        )

    def exchange_outpost_goods(self):
        self.info_set("current_task", "exchange_outpost_goods")
        self.log_info("开始据点兑换任务")

        priority_list = str.split(self.config.get("交易货品优先序列", ""), ",")

        for area in areas_list:
            self.log_info(f"进入区域: {area}")
            self.to_model_area(area, "据点管理")

            outposts = outpost_dict.get(area, [])
            if not outposts:
                self.log_info(f"{area} 没有据点可兑换")
                continue

            for outpost_name in outposts:
                self.log_info(f"开始兑换据点: {outpost_name}")
                self.perform_outpost_exchange(outpost_name, priority_list)
                self.log_info(f"完成兑换据点: {outpost_name}")

            self.log_info(f"{area} 区域据点兑换完成，返回主界面")
            self.ensure_main()

        self.log_info("据点兑换任务完成")

    def make_weapon(self):
        self.info_set("current_task", "make_weapon")
        self.log_info("开始造装备任务")

        self.back(after_sleep=2)
        self.log_info("打开终端界面")

        if not self.wait_click_ocr(
                match=re.compile("装备"),
                box=self.box.right,
                time_out=5,
                after_sleep=2,
        ):
            self.log_info("未找到装备按钮，任务失败")
            return False
        self.log_info("找到装备按钮并点击")

        if not self.wait_click_ocr(
                match=re.compile("制作"),
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
        ):
            self.log_info("未找到制作按钮，任务失败")
            return False
        self.log_info("找到制作按钮并点击")
        self.log_info("等待弹窗完成，造装备任务准备完成")
        self.wait_pop_up()

        return True

    def _click_ocr_with_info(self, match_str, box, time_out=5, after_sleep=2):
        if not self.wait_click_ocr(
                match=re.compile(match_str),
                box=box,
                time_out=time_out,
                after_sleep=after_sleep,
        ):
            self.log_info(f"未找到{match_str}按钮，任务失败")
            return False

        self.log_info(f"找到{match_str}按钮并点击")
        return True

    def claim_weekly_rewards(self):
        self.info_set("current_task", "claim_daily_rewards")
        self.log_info("开始领取每周事务")

        self.sleep(2)
        self.press_key("f7", after_sleep=2)
        self.log_info("按下 F7 打开活动中心")

        if not self._click_ocr_with_info("每周事务", self.box.left):
            return False

        if not self._click_ocr_with_info("领取", self.box.top_right):
            return False

        if not self._click_ocr_with_info("一键领取", self.box.bottom_right):
            return False

        self.wait_pop_up(after_sleep=2)
        self.log_info(f"每周事务领取完成")
        return True

    def claim_daily_rewards(self):
        self.info_set("current_task", "claim_daily_rewards")
        self.log_info("开始领取日常奖励任务")

        self.sleep(2)
        self.press_key("f8", after_sleep=2)
        self.log_info("按下 F8 打开日常奖励界面")

        if not self.wait_click_ocr(
                match=re.compile("日常"),
                box=self.box.top,
                time_out=5,
                after_sleep=2,
        ):
            self.log_info("未找到日常奖励按钮，任务失败")
            return False
        self.log_info("找到日常奖励按钮并点击")

        self.wait_click_ocr(
            match=re.compile("领取"),
            box=self.box.right,
            time_out=5,
            after_sleep=2,
        )

        if result := self.find_one(
                feature_name="claim_gift", box=self.box.left, threshold=0.8
        ):
            self.log_info("发现可领取的额外奖励，点击领取")
            self.click(result, after_sleep=2)
            self.wait_pop_up()
            self.log_info("额外奖励领取完成")
            return True

        self.log_info("日常奖励领取完成")
        return True

    def collect_clue(self):
        self.info_set("current_task", "collect_clue")
        self.log_info("开始收集线索任务")
        self.transfer_to_home_point(should_check_out_boat=True)
        self.press_key("i", after_sleep=2)
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        if self.wait_click_ocr(match=re.compile("会客室"), time_out=4, box=exchange_help_box, after_sleep=2):
            self.logger.info("进入会客室,准备处理收集线索")
            self.wait_click_ocr(match=re.compile("确认"), time_out=4, box=self.box.bottom, after_sleep=2)
            if self.wait_click_ocr(match=re.compile("收集"), time_out=4, box=self.box.right, after_sleep=2):
                self.logger.info("点击收集线索")
                self.wait_click_ocr(match="领取", time_out=4, box=self.box.bottom_right, after_sleep=2)
                self.back(after_sleep=1)
            else:
                self.logger.info("未找到收集线索按钮")

            if self.wait_click_ocr(match=re.compile("接收"), time_out=4, box=self.box.right, after_sleep=2):
                self.wait_click_ocr(match=re.compile("全部接收"), time_out=4, box=self.box.right, after_sleep=2)
                self.back(after_sleep=2)
            else:
                self.logger.info("未找到接收按钮")
            results = []

            search_box = self.box_of_screen(
                x=1390 / 3840, y=450 / 2160, to_x=3360 / 3840, to_y=1330 / 2160
            )

            for i in range(1, 8):
                self.next_frame()
                result = self.find_one(
                    feature_name=f"clue_{i}_icon",
                    box=search_box,
                )
                if result:
                    results.append(result)
                    self.sleep(0.5)

            for result in results:
                self.logger.info("点击线索框")
                self.click(result, after_sleep=1)
                self.wait_click_ocr(match=re.compile("的线索"), time_out=4, box=self.box.top_right, after_sleep=1)
                if not self.wait_ocr(match=[re.compile(i) for i in ["设施", "等级"]], box=self.box.left, time_out=1):
                    self.back(after_sleep=2)
            if self.wait_click_ocr(match=re.compile("开展交流"), time_out=4, box=self.box.bottom, after_sleep=1):
                self.wait_pop_up()
            self.log_info("收集线索任务完成")
            return True
        else:
            self.logger.info("未找到会客室，无法收集线索")
            return False

    def up_make_room_num(self):
        self.info_set("current_task", "up_make_room_num")
        self.log_info("开始制造室任务")
        self.transfer_to_home_point(should_check_out_boat=True)
        self.press_key("i", after_sleep=2)
        exchange_help_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        results = self.wait_ocr(match=re.compile("制造"), time_out=4, box=exchange_help_box)
        if not results:
            self.log_info("未找到制造舱，任务失败")
            return False
        for result in results:
            self.click(result, after_sleep=2)
            self.logger.info("点击制造室")
            for i in range(2):
                if i == 0:
                    if not self.wait_click_ocr(match=re.compile("收取"), time_out=2, box=self.box.bottom,
                                               after_sleep=2):
                        continue
                    self.wait_pop_up(after_sleep=2)
                if i == 1:
                    if not self.wait_click_ocr(match=re.compile("助力"), time_out=2, box=self.box.top_right,
                                               after_sleep=2):
                        continue
                    if not self.wait_click_ocr(match=re.compile("使用"), time_out=2, box=self.box.bottom_right,
                                               after_sleep=2):
                        continue
                if icon := self.find_one(feature_name=fL.max_icon, horizontal_variance=0.1, vertical_variance=0.1):
                    self.click(icon, after_sleep=2)
                self.wait_click_ocr(match=re.compile("确认"), time_out=2, box=self.box.bottom, after_sleep=2)
            self.safe_back(match=re.compile("运转"), box=self.box.top_left)
        self.wait_click_ocr(match=re.compile("助力"), time_out=2, box=self.box.top_right, after_sleep=2)
        self.wait_click_ocr(match=re.compile("使用"), time_out=2, box=self.box.bottom_right, after_sleep=2)
        char_list = list(get_contact_list_with_feature_list().values())
        count = 0
        for char in char_list:
            if result := self.find_one(feature_name=char, box=self.box_of_screen(0.3, 0, 1, 1)):
                self.click(result)
                count += 1
            if count >= 2:
                break
        self.wait_click_ocr(match=re.compile("确认"), settle_time=1, time_out=2, box=self.box.bottom)
        return True
