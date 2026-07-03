import re
import time

from src.data.world_map import areas_list, outpost_dict, goods_dict
from src.data.world_map_utils import get_area_by_outpost_name, get_goods_by_outpost_name, get_world_map_text
from src.image.hsv_config import HSVRange as hR
from src.data.FeatureList import FeatureList as fL
from src.data.characters_utils import get_contact_list_with_feature_list


class DailyRoutineFeature:
    BOAT_STAGES = ['收集线索', '制造舱', '培养舱']
    ACTIVITY_REWARDS = ['周常奖励', '理智补给']

    def __init__(self, task):
        self._task = task
        task.default_config.update({
            "⭐收邮件": True,
            "⭐据点兑换": True,
            "交易货品优先序列": [],
            "⭐转交运送委托": True,
            "⭐转交委托奖励领取": True,
            "⭐造装备": True,
            "⭐简易制作": True,
            "⭐收信用": True,
            "尝试仅收培育室": True,
            "⭐帝江号收菜": True,
            "帝江号收菜操作": self.BOAT_STAGES,
            "⭐活动奖励": True,
            "活动奖励": self.ACTIVITY_REWARDS,
            "⭐日常奖励": True,
        })
        task.config_type["帝江号收菜操作"] = {
            "type": "multi_selection",
            "options": self.BOAT_STAGES,
        }
        task.config_type["活动奖励"] = {
            "type": "multi_selection",
            "options": self.ACTIVITY_REWARDS,
        }
        # 收集所有可交易的货品作为候选项
        all_goods = []
        for goods_list in goods_dict.values():
            all_goods.extend(goods_list)
        task.config_type["交易货品优先序列"] = {
            "options_available": all_goods,
            "allow_duplication": False,
        }
        task.config_description.update({
            "⭐收邮件": "是否前往「邮箱」领取邮件。",
            "⭐据点兑换": (
                "是否在「地区建设/据点管理」中通过交易获得调度券。"
            ),
            "交易货品优先序列": (
                "默认留空，交易货品顺序随机。\n"
                "更多用法参见 ./docs/日常任务.md > 优先货品交易序列 。"
            ),
            "⭐转交运送委托": (
                "是否在「地区建设/仓储结点」中转交全部运送委托。"
            ),
            "⭐转交委托奖励领取": (
                "是否领取「地区建设/仓储结点/我转交的委托」。\n"
                "超时委托也会被领取。"
            ),
            "⭐造装备": (
                "是否前往「装备制造/套组装备制造」并制作一件列表首位的装备。\n"
                "请确保有足够的装备原件和调度券。"
            ),
            "⭐收信用": (
                "是否前往好友的「帝江号」并在「访客终端」上进行助力获得信用。\n"
                "助力结束后，前往「采购中心/信用交易所」收取全部助力。"
            ),
            "尝试仅收培育室": (
                "若选项开启，则优先尝试仅助力好友「帝江号」上的「培养仓」。\n"
                "如果不能，至少助力一次其它舱室。"
            ),
            "⭐帝江号收菜": (
                "是否前往好友的「帝江号」并在「访客终端」上进行收集线索、制造舱操作"
            ),
            "帝江号收菜操作": (
                "勾选要在帝江号收菜时执行的操作。\n"
                "收集线索：前往「会客室」收集全部线索，若集齐则开启情报交流。\n"
                "制造舱：前往「制造仓」收取培养材料并补足待制造数量。\n"
                "培养舱：前往「培养仓」收取培养材料并直接再次培养。"
            ),
            "⭐活动奖励": (
                "是否领取「活动中心」中的每周事务和理智补给奖励。"
            ),
            "活动奖励": (
                "勾选要在活动中心里领取的奖励。\n"
                "周常奖励：领取「每周事务」中的奖励。\n"
                "理智补给：领取「理智补给」中的奖励。"
            ),
            "⭐日常奖励": (
                "是否领取「行动手册/日常」和「通行证」中的奖励。"
            ),
        })
        task.default_config_group.update({
            "⭐据点兑换": ["交易货品优先序列"],
            "⭐收信用": ["尝试仅收培育室"],
            "⭐帝江号收菜": ["帝江号收菜操作"],
            "⭐活动奖励": ["活动奖励"],
        })

    def __getattr__(self, name):
        return getattr(self._task, name)

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

    def wait_friend_list(self, end_icon_name="friend_chat_icon"):
        start_time = time.time()
        while True:
            if time.time() - start_time > 20:
                self.log_info("加载好友列表超时")
                return False
            if self.find_feature(feature=end_icon_name):
                return True

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
            self.click_confirm(time_out=5, after_sleep=2, recheck_time=1)
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
                if not self.wait_click_feature(feature=fL.info_exchange, box=exchange_help_box, time_out=5,click_after_delay=0.5, raise_if_not_found=False, after_sleep=0.5):
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
                                self.click(result[-1])
                                self.wait_pop_up(time_out=3)
                                left_help_time -= 1
                                help_time += 1
            if not self.safe_back(feature=fL.friend_page, time_out=5):
                self.log_info("未能安全返回好友列表")
                return False
            is_first_time = False
            count += 1

    def claim_mail(self):
        self.info_set("current_task", "claim_delivery_rewards")
        self.log_info("开始收邮件")
        self.press_key("k", after_sleep=2)
        #
        stage_area = self.ocr(match=self.lang.daily_routine_mixin.k_4a2ece6a, box=self.box.top_left)
        if len(stage_area) > 0:
            self.click(x=stage_area[0].x, y=stage_area[0].y + int(self.height * 0.25))
            self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_3fef35d6, box=self.box.center, time_out=5)
            self.wait_pop_up(after_sleep=2)
        #
        if self.wait_click_ocr(
                x=0, y=0.88,
                to_x=0.25, to_y=0.95,
                match=self.lang.daily_routine_mixin.k_ffb5655a,  # 全部收取
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

                    optional_steps = {3} # 特殊可选步骤序号

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
            match=get_world_map_text(self.lang, outpost_name),
            box=self.box.top,
            time_out=5,
            after_sleep=1
        )
        self.wait_ocr(
            match=self.lang.daily_routine_mixin.k_bb6c696b, box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080), time_out=5
        )
        can_exchange_goods = [get_world_map_text(self.lang, good) for good in goods_dict.get(
            get_area_by_outpost_name(outpost_name), []
        )]

        goods_patterns = [
            re.compile(get_world_map_text(self.lang, good)) for good in get_goods_by_outpost_name(outpost_name)
        ]

        max_attempts = 7
        skip_goods = set()
        change_button = None
        confirm_button = None
        for attempt in range(1, max_attempts + 1):
            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            self.log_info(f"尝试第 {attempt}/{max_attempts} 次更换货品")
            if not change_button:
                change_button = self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_bb6c696b,
                                                    box=self.box_of_screen(1700 / 1920, 610 / 1080, 1, 710 / 1080),
                                                    time_out=5)
            else:
                self.click(change_button)
            self.wait_ocr(
                match=self.lang.daily_routine_mixin.k_70b20820,
                box=self.box.top_left,
                time_out=5,
            )
            goods = self.wait_ocr(
                match=goods_patterns,
                time_out=5,
            )

            if not goods:
                self.log_info(f"{outpost_name} 没有可兑换的货物")
                break

            def priority_score(name):
                for i, p in enumerate(priority_list):
                    if re.search(p, name):
                        return i
                return len(priority_list)

            goods.sort(key=lambda g: (priority_score(g.name), -len(g.name)))

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
            self.click(exchange_good, after_sleep=0.1)
            if not confirm_button:
                confirm_button = self.wait_feature(
                    feature=fL.select_confirm,
                    time_out=5,
                    raise_if_not_found=False
                )
                if confirm_button:
                    self.click(confirm_button)
            else:
                self.click(confirm_button)
            self.wait_click_ocr(
                match=get_world_map_text(self.lang, outpost_name),
                box=self.box.top,
                time_out=5
            )
            num = self.read_outpost_ticket_num(outpost_name)
            if num < 1000:
                self.log_info(f"{outpost_name} 据点当前券数量不足 (<1000)，停止兑换")
                break

            if not self.plus_max():
                self.log_info("未找到 '确认' 按钮，跳过本次活动")
                continue

            if self.wait_click_feature(
                feature=fL.to_max_produce_num,
                box=self.box_of_screen(0.945, 0.894, 0.973, 0.944),
                time_out=5,
                raise_if_not_found=False
            ):

                self.wait_pop_up()

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

        priority_list = self.config.get("交易货品优先序列", [])

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

    def _click_ocr_with_info(self, match_str, box, time_out=5, after_sleep=2):
        if not self.wait_click_ocr(
                match=re.compile(match_str),
                box=box,
                time_out=time_out,
                after_sleep=after_sleep,
        ):
            self.mark_task_failure(f"未找到{match_str}按钮，任务失败")
            return False

        self.log_info(f"找到{match_str}按钮并点击")
        return True

    def claim_weekly_rewards(self):
        self.log_info("开始领取每周事务")

        if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_13eea5dd, box=self.box.left, time_out=5, after_sleep=1):
            self.log_info("进入『每周事务』页面")
            if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_39d12e73_1, box=self.box.top_right, time_out=5, after_sleep=1):
                if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_bf856c96, box=self.box.bottom_right, time_out=5,
                                       after_sleep=1):
                    self.wait_pop_up(after_sleep=2)
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

        if not self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_059a808c, box=self.box.left, time_out=5, after_sleep=1):
            self.log_info("未找到『活动中心/理智补给』入口")
            return False

        self.log_info("进入『理智补给』页面")
        if self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_39d12e73_1, box=self.box_of_screen(0.894, 0.648, 0.995, 0.991), time_out=5, after_sleep=1):
            self.wait_pop_up(after_sleep=2)
            self.log_info("已领取『理智补给』奖励")
            return True

        self.log_info("未找到『理智补给/领取』按钮")
        return False

    def claim_activity_rewards(self):
        self.info_set("current_task", "claim_activity_rewards")
        self.log_info("开始领取活动页奖励")

        self.sleep(2)
        self.press_key("f7", after_sleep=2)
        self.log_info("按下 F7 打开活动中心")

        enabled_rewards = set(self.config.get("活动奖励", []))
        weekly_enabled = "周常奖励" in enabled_rewards
        sanity_enabled = "理智补给" in enabled_rewards

        if weekly_enabled:
            self.claim_weekly_rewards()
        else:
            self.log_info("已关闭『周常奖励』，跳过")

        if sanity_enabled:
            self.claim_sanity_supply()
        else:
            self.log_info("已关闭『理智补给』，跳过")

        return True

    def claim_daily_rewards(self):
        self.info_set("current_task", "claim_daily_rewards")
        self.log_info("开始领取日常奖励任务")

        # 行动手册/日常

        self.sleep(2)
        self.press_key("f8", after_sleep=2)
        self.log_info("按下 F8 打开日常奖励界面")

        if not self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_8d0e83fc,
                box=self.box.top,
                time_out=5,
                after_sleep=2,
        ):
            self.mark_task_failure("未找到日常奖励按钮，任务失败")
            return False
        self.log_info("找到日常奖励按钮并点击")

        self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_39d12e73_1,
            box=self.box.right,
            time_out=5,
            after_sleep=2,
        )

        if result := self.find_one(
                feature="claim_gift", box=self.box.left, threshold=0.8
        ):
            self.log_info("发现可领取的额外奖励，点击领取")
            self.click(result, after_sleep=2)
            self.wait_pop_up(after_sleep=2)
            self.log_info("额外奖励领取完成")

        self.log_info("日常奖励领取完成")

        # 通行证

        if not self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_23926d61,
                box=self.box.bottom_right,
                time_out=5,
                after_sleep=2,
        ):
            self.mark_task_failure("未找到通行证奖励入口，任务失败")
            return False

        # 通行证任务
        if self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_d7613f0e,
                box=self.box.top,
                time_out=5,
                after_sleep=2,
        ):
            mission_boxes = self.ocr(
                x=0.12, y=0.33,
                to_x=0.31, to_y=0.80,
                match=self.lang.daily_routine_mixin.k_105cdd5a
            )
            for box in mission_boxes:
                self.click_box(box=box, after_sleep=2)
                self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_3ecdd4bb,  # 一键领取
                    box=self.box.bottom,
                    time_out=5,
                    after_sleep=2,
                )
            self.wait_click_ocr(
                match=self.lang.daily_routine_mixin.k_727d1bec,
                box=self.box.top,
                time_out=5,
                after_sleep=2,
            )

        # 通行证奖励
        self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_39d12e73_1,  # 一键领取
            box=self.box.bottom,
            time_out=5,
            after_sleep=2,
        )
        self.wait_pop_up(after_sleep=2)
        self.send_key("esc", after_sleep=2)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
        if len(self.ocr(match=self.lang.daily_routine_mixin.k_25d2b666, box=self.box.top_right)) > 0:
            # 暂不领取武器补给箱
            self.send_key("esc", after_sleep=2)  # 确认使用send_key：esc为系统通用退出键，非游戏可配置热键
            self.wait_click_ocr(match=self.lang.daily_routine_mixin.k_4d0b4688, time_out=5, after_sleep=2)
            if len(self.ocr(match=self.lang.daily_routine_mixin.k_1c5ad36e, box=self.box.center)) > 0:
                self.click_confirm(time_out=5, after_sleep=2)

        return True

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
