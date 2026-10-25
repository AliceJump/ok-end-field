from typing import ClassVar

from ok import Box

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.image.gray_bar_detector import detect_gray_bars
from src.tasks.mixin.battle_mixin import BattleMixin


class YingTuoTask(BattleMixin):
    # 打完关卡退出副本要加载，实测 22.7 秒，留足余量。
    RETURN_LOADING_TIMEOUT: ClassVar = 90
    # 点击灰条后等关卡详情页出现的时间与重试次数。
    DETAIL_PAGE_TIMEOUT: ClassVar = 5
    CHALLENGE_CLICK_ATTEMPTS: ClassVar = 3
    # 关卡段淡入约 0.55 秒，淡入前也会连续检测为空；空结果连续保持这段时间才采用。
    EMPTY_BARS_SETTLE_TIME: ClassVar = 0.6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "影拓丰碑"
        self.icon = Icons.YingTuo
        self.group_name = "战斗"
        self.description = "自动完成当前所有普通影拓丰碑关卡"

        self.support_schedule_task = True

    def run(self):
        self.ensure_main(time_out=400)
        if not self.enter_yingtuo():
            return
        empty_visits = 0
        while self.find_normal_challenge():
            self.log_info("检测到普通关卡灰条，处理本屏未通关关卡")
            results = (
                self.wait_until(
                    lambda: self.find_feature(
                        feature=fL.yingtuo_not_cleared_icon,
                        box=self.box_of_screen(0.033, 0.133, 0.058, 0.778),
                    ),
                    time_out=10,
                    settle_time=0.5,
                    raise_if_not_found=False,
                )
                or []
            )
            if results:
                empty_visits = 0
            else:
                # 返回后仍会检测到同一组灰条，不设上限就会反复点同一张海报。
                empty_visits += 1
                if empty_visits >= 2:
                    self.log_info("关卡详情页连续两次没有未通关图标，任务结束")
                    return
                self.log_info("关卡详情页没有未通关图标，返回海报页")
            for result in results:
                self.click(result, after_sleep=0.25)
                self.log_info("进入挑战页面，开始挑战")
                if not self.wait_click_feature(
                    feature=fL.to_max_produce_num,
                    box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
                    time_out=10,
                    settle_time=0.5,
                    raise_if_not_found=False,
                ):
                    self.log_info("未能找到挑战开始按钮")
                    raise Exception("未能找到挑战开始按钮")
                if not self.wait_click_feature(
                    feature=fL.give_gift,
                    box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
                    time_out=10,
                    settle_time=0.5,
                    raise_if_not_found=False,
                ):
                    self.log_info("未能进入战斗")
                    raise Exception("未能进入战斗")

                def retry_transition():
                    button = self.find_one(
                        feature=fL.give_gift,
                        box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
                    )
                    if button:
                        self.log_info("挑战入场按钮仍在，重试入场")
                        self.click(button, after_sleep=0.5)

                if not self.battle_and_exit(transition_recovery=retry_transition):
                    self.log_info("战斗过程中发生错误，返回失败")
                    raise Exception("战斗过程中发生错误")
                if not self.wait_feature(
                    feature=fL.to_max_produce_num,
                    box=self.box_of_screen(0.934, 0.881, 0.977, 0.965),
                    time_out=60,
                    settle_time=0.5,
                    raise_if_not_found=False,
                ):
                    self.log_info("未能找到挑战开始按钮，返回失败")
                    raise Exception("未能找到挑战开始按钮")
                self.log_info("挑战完成，继续寻找下一个普通关卡")
            if not self.return_to_monument():
                self.log_info("未能安全返回，任务结束")
                return
        self.log_info("影拓丰碑任务完成", notify=True)

    def return_to_monument(self):
        """从关卡详情页返回海报页，返回是否已回到海报页。

        打完关卡后按返回会退出副本并加载。加载期间再按返回，按键会在加载结束后生效，
        把刚出现的海报页关回行动手册，所以离开详情页后只等待、不再按键。
        """
        for _ in range(3):
            self.back()
            if self.wait_until(lambda: not self._on_detail_page(), time_out=5, raise_if_not_found=False):
                break
        else:
            self.log_info("按返回后仍停在关卡详情页")
            return False
        # 页面须稳定 0.5 秒：加载结束时海报页可能只闪一帧，随后落到行动手册。
        # wait_until 只在条件为假时重新计时，所以页面类型一变就先返回 None。
        last_page = None

        def settled_page():
            nonlocal last_page
            page, previous = self._monument_or_handbook(), last_page
            last_page = page
            return page if page == previous else None

        page = self.wait_until(
            settled_page,
            time_out=self.RETURN_LOADING_TIMEOUT,
            settle_time=0.5,
            raise_if_not_found=False,
        )
        if page == "monument":
            return True
        if (
            page == "handbook"
            and self.wait_click_feature(feature=fL.yingtuo_entrance, time_out=2, raise_if_not_found=False)
            and self._wait_monument(time_out=10)
        ):
            self.log_info("从行动手册重新进入影拓丰碑页面")
            return True
        return False

    def _monument_or_handbook(self):
        """当前在海报页返回 "monument"，在行动手册常驻页返回 "handbook"，都不是返回 None。"""
        if self.find_one(
            feature=fL.yingtuo_monument,
            vertical_variance=0.05,
            horizontal_variance=0.05,
            box=self.box_of_screen(0.002, 0.750, 0.990, 0.783),
        ):
            return "monument"
        if self.find_one(feature=fL.yingtuo_entrance):
            return "handbook"
        return None

    def _wait_monument(self, time_out):
        """等海报页标志连续 0.5 秒可见。"""
        return self.wait_until(
            lambda: self.find_one(
                feature=fL.yingtuo_monument,
                vertical_variance=0.05,
                horizontal_variance=0.05,
                box=self.box_of_screen(0.002, 0.750, 0.990, 0.783),
            ),
            time_out=time_out,
            settle_time=0.5,
            raise_if_not_found=False,
        )

    def _on_detail_page(self):
        """关卡详情页右下角的「挑战影拓丰碑」按钮可见时，说明仍停在详情页。"""
        return self.find_one(feature=fL.to_max_produce_num, box=self.box_of_screen(0.934, 0.881, 0.977, 0.965))

    def enter_yingtuo(self):
        self.log_info("开始影拓丰碑任务", notify=True)
        self.press_key("f8")
        find_yingtuo_entrance = False
        for _ in range(6):
            if self.wait_click_feature(feature=fL.yingtuo_entrance, time_out=2, raise_if_not_found=False):
                self.log_info("找到影拓入口", notify=True)
                find_yingtuo_entrance = True
                break
            self.send_key("e", after_sleep=0)
        if not find_yingtuo_entrance:
            self.log_info("未能找到影拓入口，任务结束", notify=True)
            return False
        if not self.wait_feature(feature=fL.yingtuo_monument, time_out=10, raise_if_not_found=False):
            self.log_info("未能找到影拓丰碑活动页标志，任务结束", notify=True)
            return False
        self.log_info("成功进入影拓丰碑页面")
        return True

    def find_normal_challenge(self):
        """寻找当前可打关卡并进入其详情页；连续四次滚动后无灰条时结束。"""
        empty_scrolls = 0
        attempts = 0
        while empty_scrolls < 4:
            bars, frame = self._detect_stable_bars()
            if not bars:
                empty_scrolls += 1
                self.log_info(f"未检测到普通关卡灰条，继续向下滚动 ({empty_scrolls}/4)")
                self.scroll_relative(0.5, 0.5, -5)
                continue
            attempts += 1
            leftmost_bar = bars[0]
            frame_height, frame_width = frame.shape[:2]
            self.log_info(f"检测到 {len(bars)} 条普通关卡灰条，点击最左侧灰条")
            self.click_relative(
                leftmost_bar.center_x / frame_width,
                leftmost_bar.center_y / frame_height,
                after_sleep=0.25,
            )
            if self.wait_until(self._on_detail_page, time_out=self.DETAIL_PAGE_TIMEOUT, raise_if_not_found=False):
                return True
            # 点满次数就报错，不再检测：此时画面不明，检测为空会走空滚动并误报任务完成。
            if attempts >= self.CHALLENGE_CLICK_ATTEMPTS:
                self.log_info("点击灰条后未进入关卡详情页")
                raise Exception("点击灰条后未进入关卡详情页")
            self.log_info(f"点击灰条后未进入关卡详情页，重新检测 ({attempts}/{self.CHALLENGE_CLICK_ATTEMPTS})")
        self.log_info("连续 4 次滚动未检测到普通关卡灰条")
        return None

    def _detect_stable_bars(self, time_out=3.0):
        """间隔 0.2 秒两次检测结果一致才采用，返回灰条列表与所用帧。

        海报页出现后，下方关卡段约 0.55 秒才淡入完成，期间命中数在 0~5 条之间跳变；
        滚动后的动画同理。结果为空时，须从首次为空起连续为空满淡入时间才采用。
        超时仍未稳定时报错：此时画面状态不明，按最后一次结果点击可能点错，判为空可能误报没有关卡。
        """
        start = self.active_time()
        previous = None
        empty_since = None
        while True:
            frame = self.next_frame()
            bars = self.detect_normal_challenge_bars(frame)
            if bars:
                empty_since = None
            elif empty_since is None:
                empty_since = self.active_time()
            if (
                previous is not None
                and self._same_bars(previous, bars, frame.shape[1])
                and (bars or self.active_time() - empty_since >= self.EMPTY_BARS_SETTLE_TIME)
            ):
                return bars, frame
            if self.active_time() - start >= time_out:
                self.log_info(f"灰条检测 {time_out:g} 秒内未稳定，结束挑战")
                raise Exception(f"灰条检测 {time_out:g} 秒内未稳定")
            previous = bars
            self.sleep(0.2)

    @staticmethod
    def _same_bars(first, second, frame_width):
        """两组灰条数量相同、中心位置相差不超过画面宽度的 1% 时视为一致。"""
        tolerance = frame_width * 0.01
        return len(first) == len(second) and all(
            abs(a.center_x - b.center_x) <= tolerance and abs(a.center_y - b.center_y) <= tolerance
            for a, b in zip(first, second, strict=True)
        )

    def detect_normal_challenge_bars(self, frame):
        """检测普通关卡灰条；启用调试叠加层时按 YOLO 的方式绘制检测框。"""
        bars = detect_gray_bars(frame, min_aspect_ratio=3.5)
        if self._is_debug_overlay_enabled():
            debug_boxes = []
            for index, bar in enumerate(bars, start=1):
                box = Box(bar.x, bar.y, bar.width, bar.height)
                box.name = f"yingtuo_gray_bar_{index}"
                box.confidence = 1.0
                debug_boxes.append(box)
            self.draw_boxes("yingtuo_gray_bars", debug_boxes, color="green", debug=True)
        return bars

    def battle_and_exit(self, transition_recovery=None):
        if not self.wait_battle_space(transition_recovery=transition_recovery):
            return False

        def challenge_state():
            self.next_frame()
            if self.in_combat():
                return "combat"
            return self.ocr(match=self.lang.daily_battle_mixin.k_4cc61900, box=self.box.bottom_right)

        # 入场只表示加载完成；后续关卡可能还要靠近触碰点并交互才会开战。
        state = self.strafe_search(challenge_state, keys=("w",), duration=0.25, passes=None, time_out=60)
        if not state:
            self.log_info("未找到挑战触碰点")
            return False
        if state != "combat":
            self.press_key("f")

            def challenge_started():
                self.next_frame()
                return self.in_combat()

            if not self.wait_until(challenge_started, time_out=10, raise_if_not_found=False):
                self.log_info("触碰后挑战未启动")
                return False
        if not self.auto_battle():
            return False
        if not self.wait_click_feature(
            feature=fL.left_battle,
            vertical_variance=0.1,
            time_out=10,
            settle_time=0.5,
            raise_if_not_found=False,
        ):
            self.log_info("未能退出按钮")
            return False
        return True
