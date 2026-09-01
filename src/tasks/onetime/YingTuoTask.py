from ok import Box

from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.image.gray_bar_detector import detect_gray_bars
from src.tasks.mixin.battle_mixin import BattleMixin


class YingTuoTask(BattleMixin):
    requires_foreground = True  # 战斗需要前台

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
            if not results:
                self.log_info("检测到关卡灰条，但本屏没有未通关图标，继续向下滚动")
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
                if not self.battle_and_exit():
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
            if not self.safe_back(feature=fL.yingtuo_monument, box=self.box_of_screen(0.002, 0.750, 0.990, 0.783)):
                self.log_info("未能安全返回，任务结束")
                return
        self.log_info("影拓丰碑任务完成", notify=True)

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
        """寻找当前可打关卡；连续四次滚动后无灰条时结束。"""
        for empty_scrolls in range(4):
            frame = self.next_frame()
            bars = self.detect_normal_challenge_bars(frame)
            if bars:
                leftmost_bar = bars[0]
                frame_height, frame_width = frame.shape[:2]
                self.log_info(f"检测到 {len(bars)} 条普通关卡灰条，点击最左侧灰条")
                self.click_relative(
                    leftmost_bar.center_x / frame_width,
                    leftmost_bar.center_y / frame_height,
                    after_sleep=0.25,
                )
                return True
            self.log_info(f"未检测到普通关卡灰条，继续向下滚动 ({empty_scrolls + 1}/4)")
            self.scroll_relative(0.5, 0.5, -5)
        self.log_info("连续 4 次滚动未检测到普通关卡灰条")
        return None

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

    def battle_and_exit(self):
        end_time = self.active_time()
        while not self.wait_feature(feature=fL.battle_space_left, time_out=1, raise_if_not_found=False):
            if self.active_time() - end_time > 300:
                self.log_info("等待超时，进入协议空间超时")
                return False
        self.auto_battle()
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
