import re

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
        return self._collect_friend_credit()

    def _collect_friend_credit(self):
        """首次从列表进入，随后优先快速拜访；仅以界面次数减少计入完成数。"""
        remaining = (None, None)
        totals = [0, 0]
        no_progress = 0
        quick_enabled = True
        self.log_info("开始好友拜访阶段")
        try:
            for visit_index in range(15):
                used_quick = False
                if visit_index and quick_enabled and no_progress < 2:
                    used_quick = self._quick_credit_visit()
                if not used_quick:
                    if visit_index:
                        # 快速入口不可用或连续无收益后，本轮改用列表，避免兜底重复选人。
                        quick_enabled = False
                    list_result = self._visit_credit_friend_from_list(remaining, first=visit_index == 0)
                    if list_result == "complete":
                        self.log_info("今日情报交流和生产助力剩余次数均为零，结束拜访")
                        return True
                    if list_result != "visited":
                        self.log_info("未找到可继续交流或助力的好友，本次拜访未确认全部完成")
                        return False

                result = self._collect_current_friend_credit()
                if result is None:
                    return False
                remaining, gains = result
                totals = [total + gain for total, gain in zip(totals, gains, strict=True)]
                if remaining == (0, 0):
                    self.log_info("今日情报交流和生产助力剩余次数均为零，结束拜访")
                    return True
                no_progress = 0 if any(gains) else no_progress + 1
                if not used_quick and no_progress >= 2:
                    self.log_info("列表拜访后仍未确认次数减少，停止重复拜访")
                    return False
            self.log_info("好友拜访达到安全次数上限，停止重复拜访")
            return False
        finally:
            self.info_set("exchange_time", totals[0])
            self.info_set("help_time", totals[1])

    @staticmethod
    def _parse_credit_remaining(boxes, expected_count=2):
        """终端两行依次为交流、助力；分开的 OCR 文本按行拼接，拒绝缺项和异常数值。"""
        rows = []
        for box in sorted(boxes or [], key=lambda item: item.y + item.height / 2):
            center = box.y + box.height / 2
            if rows and abs(center - rows[-1][0]) < min(box.height, rows[-1][1]) * 0.6:
                rows[-1][2].append(box)
            else:
                rows.append((center, box.height, [box]))
        counts = []
        for _, _, row in rows:
            text = "".join(box.name for box in sorted(row, key=lambda item: item.x))
            for match in re.finditer(r"(?<!\d)(\d+)\s*[/／]\s*(\d+)(?!\d)", text):
                remaining, limit = map(int, match.groups())
                if not 0 <= remaining <= limit or limit <= 0:
                    return None
                counts.append(remaining)
        return tuple(counts) if len(counts) == expected_count else None

    def _read_credit_remaining(self):
        # 位置取自仓库访客终端原图 51.png，区域只覆盖两行每日次数，不含信用余额和舱室。
        return self._parse_credit_remaining(self.ocr(box=self.box_of_screen(0.12, 0.555, 0.45, 0.625)))

    def _wait_credit_remaining(self, previous=None):
        def changed():
            counts = self._read_credit_remaining()
            if counts is None:
                return None
            if previous is None:
                return counts
            # 同步期或 OCR 误读导致的回升不当作完成，也不作为新的点击依据。
            if all(now <= old for now, old in zip(counts, previous, strict=True)) and counts != previous:
                return counts
            return None

        return self.wait_until(changed, time_out=5, settle_time=0.5, raise_if_not_found=False)

    def _collect_current_friend_credit(self):
        self.press_key("y")
        self.wait_ui_stable(refresh_interval=0.5)
        before = self._wait_credit_remaining()
        if before is None:
            self.log_info("未能识别访客终端每日剩余次数，停止拜访")
            return None
        remaining = before
        action_box = self.box_of_screen(0.1, 561 / 861, 0.9, 0.9)
        if remaining[0] > 0 and self.wait_click_feature(
            feature=fL.info_exchange,
            box=action_box,
            time_out=3,
            click_after_delay=0.5,
            raise_if_not_found=False,
            after_sleep=0.5,
        ):
            remaining = self._wait_credit_remaining(remaining) or remaining

        if remaining[1] > 0:
            prefer_grow = self.config.get("尝试仅收培育室")
            if prefer_grow:
                # 沿用原流程：右滚至培养舱所在末端，优先最右可助力项，无则使用其他舱室。
                self.scroll_relative(0.8, 0.8, count=-8)
                self.wait_ui_stable(refresh_interval=0.5)
            for _ in range(1 if prefer_grow else min(remaining[1], 5)):
                buttons = self.find_feature(feature=fL.assist_friend, box=action_box)
                if not buttons and prefer_grow:
                    self.scroll_relative(0.8, 0.8, 40)
                    self.wait_ui_stable(refresh_interval=0.5)
                    buttons = self.find_feature(feature=fL.assist_friend, box=action_box)
                if not buttons:
                    break
                target = max(buttons, key=lambda box: box.x) if prefer_grow else min(buttons, key=lambda box: box.x)
                self.click(target, after_sleep=0.5)
                if target.x / self.width > 0.7:
                    self.wait_pop_up(time_out=5)
                updated = self._wait_credit_remaining(remaining)
                if updated is None:
                    break
                remaining = updated
                if remaining[1] == 0:
                    break

        # 先关闭访客终端回到 HUD，快速拜访只在主 HUD 接受输入。
        self.back()
        if not self.ensure_in_friend_boat():
            self.log_info("未能返回好友帝江号界面")
            return None
        return remaining, tuple(old - new for old, new in zip(before, remaining, strict=True))

    def _quick_credit_visit(self):
        matchers = self.lang.credit_collect
        result = self.wait_ocr(
            match=[matchers.quick_visit, matchers.no_next_visit],
            box=self.box.top,
            time_out=10,
            raise_if_not_found=False,
        )
        if not result or not any(matchers.quick_visit.search(box.name) for box in result):
            self.log_info("快速拜访暂无目标或入口不可用，返回好友列表补查")
            return False
        self.press_key("tab")
        # 旧船的离开按钮也满足到达判定，必须先观察离开旧 HUD，避免立即把旧船当作新船。
        departed = self.wait_until(
            lambda: not self.find_one(feature=fL.left_button, vertical_variance=0.1, horizontal_variance=0.1),
            time_out=8,
            settle_time=0,
            raise_if_not_found=False,
        )
        if not departed or not self.ensure_in_friend_boat():
            self.log_info("快速拜访未确认完成切换，返回列表恢复")
            return False
        return True

    def _visit_credit_friend_from_list(self, remaining, first=False):
        if first and not self.wait_click_ocr(
            match=self.lang.daily_routine_mixin.k_e84c3ae9, box=self.box.right, time_out=7, recheck_time=1
        ):
            return "failed"
        if not self.safe_back(feature=fL.friend_page, time_out=10):
            self.log_info("未能安全返回好友列表")
            return "failed"
        # 好友列表右上角也显示同一组剩余次数，可识别当日已经做完的首次运行。
        self.wait_ui_stable(refresh_interval=0.5)
        # 分区读取两个相邻数字，避免 OCR 把“4/5 | 5/5”合成“4/55/5”。
        listed_counts = [
            self._parse_credit_remaining(self.ocr(box=self.box_of_screen(left, 0.095, right, 0.135)), expected_count=1)
            for left, right in ((0.91, 0.935), (0.954, 0.978))
        ]
        listed_remaining = tuple(count[0] for count in listed_counts) if all(listed_counts) else None
        if listed_remaining == (0, 0):
            return "complete"
        remaining = listed_remaining or remaining
        for left, feature in zip(remaining, (fL.can_exchange_info_icon, fL.can_help_icon), strict=True):
            if left == 0:
                continue
            self.scroll_relative(0.5, 0.5, 40)
            self.wait_ui_stable(refresh_interval=0.5)
            for scroll_index in range(8):
                # 拜访期间当前好友置顶；首页跳过该行，滚动后恢复完整列表扫描区域。
                top = 301 / 2160 if first or scroll_index else 615 / 2160
                area = self.box_of_screen(3400 / 3840, top, 3692 / 3840, 1883 / 2160)
                targets = self.find_feature(feature=feature, box=area)
                if targets:
                    self.click(targets[0])
                    self.click_confirm(time_out=5, recheck_time=1)
                    return "visited" if self.ensure_in_friend_boat() else "failed"
                self.scroll_relative(0.5, 0.5, -4)
                self.wait_ui_stable(refresh_interval=0.5)
        return "failed"

    def run(self):
        self.ensure_main(time_out=420)
        self.run_credit_collect()

    def run_credit_collect(self):
        """收信用流程，独立运行与被日常执行（DailyFeature）共用。"""
        return self.collect_credit()
