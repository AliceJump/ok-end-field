from qfluentwidgets import FluentIcon

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common


class CreditCollectTask(Common):
    """收信用子任务：好友帝江号交流助力并收取信用，日常任务经 DailyFeature 接入。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "收信用"
        self.icon = Icons.Interact
        self.group_name = "日常任务"
        self.group_icon = FluentIcon.CALENDAR
        self.description = "前往好友的「帝江号」进行交流与助力，并前往「采购中心/信用交易所」收取信用。"
        # 账号配置页收集标记：本任务参数可按账号覆盖（多账户独立配置）
        self.support_multi_account = True
        self.default_config.update(
            {
                "尝试仅收培育室": True,
            }
        )
        self.config_description.update(
            {
                "尝试仅收培育室": (
                    "若选项开启，则优先尝试仅助力好友「帝江号」上的「培养仓」。\n如果不能，至少助力一次其它舱室。"
                ),
            }
        )

    def collect_credit(self):
        self.info_set("current_task", "collect_credit")
        self.press_key("f5")
        self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_7be4248b, box=self.box.top, time_out=5, recheck_time=1
        )
        result = self.wait_click_ocr(
            match=[self.lang.daily_routine_mixin.k_b693e51a, self.lang.daily_routine_mixin.k_f646bcd5],
            box=self.box.bottom_left,
            time_out=7,
            recheck_time=1,
        )
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
        count = 0
        quick_arrival = False
        self.log_info("开始好友拜访阶段")
        while True:
            temp_exchange_time = left_exchange_time
            if is_first_time:
                self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_e84c3ae9, box=self.box.right, time_out=7, recheck_time=1
                )
            if count >= 15:
                self.log_info("循环过多次仍未找到交流或助力对象，可能出现异常，结束拜访")
                return False
            count += 1

            result = None
            self.wait_ui_stable(refresh_interval=1)
            start_time = self.active_time()
            scroll_count = 0
            search_exchange = left_exchange_time > 0
            while not quick_arrival and not result:
                if is_first_time or scroll_count > 0:
                    span_box = self.box_of_screen(3400 / 3840, 301 / 2160, 3692 / 3840, 1883 / 2160)
                else:
                    span_box = self.box_of_screen(3400 / 3840, 615 / 2160, 3692 / 3840, 1883 / 2160)
                if self.active_time() - start_time > 40:
                    self.log_info("找不到可交流或助力的玩家")
                    return False
                if search_exchange:
                    result = self.find_feature(feature=fL.can_exchange_info_icon, box=span_box)
                    if not result and scroll_count >= 7:
                        if left_help_time <= 0:
                            self.log_info("列表补查未找到可交流的好友，本次未确认完成全部次数")
                            return False
                        search_exchange = False
                        scroll_count = 0
                        self.scroll_relative(0.5, 0.5, 40)
                        self.wait_ui_stable(refresh_interval=1)
                        continue
                elif left_help_time > 0:
                    result = self.find_feature(feature=fL.can_help_icon, box=span_box)
                    if not result and scroll_count >= 7:
                        self.log_info("列表补查未找到可助力的好友，本次未确认完成全部次数")
                        return False
                if not result:
                    scroll_count += 1
                    self.scroll_relative(0.5, 0.5, -4)
                    self.wait_ui_stable(refresh_interval=1)

            if not quick_arrival:
                self.click(result)
                self.click_confirm(time_out=5, recheck_time=1)
            if not quick_arrival and not self.ensure_in_friend_boat():
                self.log_info("未能进入好友帝江号")
                if self.wait_click_ocr(
                    match=self.lang.daily_routine_mixin.k_a730d877, box=self.box.top_left, time_out=1
                ):
                    continue
                else:
                    return False
            self.sleep(2)
            actions = []
            if left_exchange_time > 0:
                actions.append("交流")
            if left_help_time > 0:
                actions.append("助力")
            # 「交流」「助力」为静态文案，内层逐个过 tr 后拼接
            self.log_info(
                self.tr("已进入好友帝江号，准备进行{actions}操作").format(actions="".join(self.tr(a) for a in actions))
            )
            self.press_key("y")
            self.wait_ui_stable(refresh_interval=1)
            if left_exchange_time > 0 and self.wait_click_feature(
                feature=fL.info_exchange,
                box=exchange_help_box,
                time_out=5,
                click_after_delay=0.5,
                raise_if_not_found=False,
                after_sleep=0.5,
            ):
                left_exchange_time -= 1
                exchange_time += 1
            if left_help_time > 0:
                result = self.find_feature(feature=fL.assist_friend, box=exchange_help_box)
                if not result and temp_exchange_time <= 0:
                    self.log_info("未找到可助力的对象")
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
                                if last_help.x / self.width > 0.7:
                                    self.wait_pop_up(time_out=5)
                                left_help_time -= 1
                                help_time += 1
            self.info_set("exchange_time", exchange_time)
            self.info_set("help_time", help_time)
            if left_exchange_time <= 0 and left_help_time <= 0:
                self.info_set("visit_end_reason", "action_counts_completed")
                self.log_info("本轮交流、助力操作各执行 5 次，正常结束好友拜访")
                if not self.safe_back(feature=fL.friend_page, time_out=10):
                    self.log_info("操作次数已完成，但未能安全返回好友列表")
                    return False
                return True

            quick_arrival = False
            if count < 15:
                quick_result = self.try_quick_visit()
                if quick_result is None:
                    # 已确认处于好友船 HUD，但没有下位候选；不要重复搜索列表。
                    self.info_set("visit_end_reason", "no_next_person")
                    self.log_info("快速拜访无下位候选：可能次数已用完或没有合格好友，结束本轮拜访")
                    if not self.safe_back(feature=fL.friend_page, time_out=10):
                        self.log_info("拜访阶段结束，但未能安全返回好友列表")
                        return False
                    return True
                quick_arrival = quick_result
            if not quick_arrival and not self.safe_back(feature=fL.friend_page, time_out=10):
                self.log_info("未能安全返回好友列表")
                return False
            is_first_time = False

    def _friend_hud_visible(self):
        """检查好友帝江号 HUD 是否仍在，不将单帧命中当作稳定状态。"""
        return bool(self.find_one(feature=fL.left_button, vertical_variance=0.1, horizontal_variance=0.1))

    def try_quick_visit(self):
        """返回 True 表示已切换，False 表示切换异常需恢复，None 表示稳定 HUD 下无候选。"""
        if not self.safe_back(feature=fL.left_button, time_out=10):
            return False
        # safe_back 只保证一次命中，先确认 HUD 连续存在再检查下位按钮。
        if not self.wait_until(self._friend_hud_visible, time_out=3, settle_time=0.5):
            self.log_info("好友船 HUD 未能稳定确认，返回好友列表补查")
            return False
        if not self.wait_feature(feature=fL.next_person, time_out=5, raise_if_not_found=False):
            # 按钮缺失本身不是 Empty 证明；仅在 HUD 仍稳定时视作本轮无候选。
            if self.wait_until(self._friend_hud_visible, time_out=2, settle_time=0.5):
                self.log_info("稳定好友船 HUD 下未检测到快速拜访按钮，推定无剩余次数或无合格好友")
                return None
            self.log_info("快速拜访按钮未命中且好友船 HUD 不稳定，返回好友列表补查")
            return False
        self.press_key("tab")
        # 要求旧 HUD 连续消失，防止单帧漏检被当作传送开始。
        if not self.wait_until(
            lambda: not self._friend_hud_visible(),
            time_out=8,
            settle_time=0.5,
        ):
            self.log_info("快速拜访未稳定观察到离开旧船，返回好友列表补查")
            return False
        return bool(self.wait_until(self.in_friend_boat, time_out=60, settle_time=0.5))

    def run(self):
        self.ensure_main(time_out=420)
        result = self.run_credit_collect()
        if result is False:
            # 一次性任务执行器忽略 run() 的返回值，必须通过异常报告独立执行失败。
            raise RuntimeError("收信用任务未完成，请检查日志中的具体原因")
        return result

    def run_credit_collect(self):
        """收信用流程，独立运行与被日常执行（DailyFeature）共用。"""
        return self.collect_credit()
