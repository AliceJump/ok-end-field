from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.daily.daily_demo_mixin import DailyDemoFeature
from src.tasks.mixin.common import Common


class DemoDrawTask(Common):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "演算抽牌"
        self.group_name = "战斗"
        self.description = "在演武台面前自动进入演算抽牌页，循环抽取直到满足等级变化条件"
        self.icon = Icons.BATTLE
        self.daily_demo = DailyDemoFeature(self)
        self.default_config.update({
            "最多重开次数": 30,
        })
        self.default_config.pop("⭐演算", None)
        self.config_description.update({
            "最多重开次数": "每轮最多抽牌5次；未满足条件时点击左下角『放弃』并重新开始。",
        })

    def run(self):
        max_restart_times = int(self.config.get("最多重开次数", 30))
        for restart_index in range(max_restart_times):
            if not self.enter_draw_page():
                return False
            if self.draw_until_target():
                self.log_info("抽牌结果满足条件，任务停止", notify=True)
                return True
            self.log_info(f"第 {restart_index + 1} 轮未满足条件，放弃后重新开始")
            if not self.abandon_draw_page():
                return False
        self.log_warning(f"已达到最多重开次数 {max_restart_times}，任务停止", notify=True)
        return False

    def enter_draw_page(self):
        self.ensure_main()
        self.press_key("f", after_sleep=1)
        self.wait_ui_stable(refresh_interval=1)
        if self.daily_demo.read_level() < 0:
            self.log_warning("按 F 后未进入演算抽牌页面")
            return False
        return True

    def draw_until_target(self):
        down_count = 0
        diff_penalty_sum = 0
        previous_level = self.daily_demo.read_level()
        if previous_level < 0:
            return False

        for draw_index in range(5):
            if not self.wait_click_feature(
                    feature=fL.demo_random_button,
                    time_out=10,
                    raise_if_not_found=False,
                    click_after_delay=0.5,
                    after_sleep=1,
            ):
                self.log_warning("未找到演算抽牌按钮")
                return False
            current_level = self.daily_demo.read_level()
            if current_level < 0:
                return False
            level_diff = current_level - previous_level
            if -level_diff > 0:
                down_count += 1
            if level_diff < 0:
                level_diff += 11
            diff_penalty_sum += 5 - level_diff
            self.log_info(
                f"第 {draw_index + 1} 次抽牌后等级: {current_level}, "
                f"差值: {level_diff}, 差值统计: {diff_penalty_sum}, 等级下降计数: {down_count}"
            )
            if diff_penalty_sum > 4:
                self.log_info("差值统计超过4，放弃本轮")
                return False
            previous_level = current_level

        return down_count == 1 and current_level == 10

    def abandon_draw_page(self):
        if not self.wait_click_ocr(
                match=self.lang.daily_battle_mixin.k_b8a81b7a,
                box=self.box.bottom_left,
                time_out=5,
                raise_if_not_found=False,
                after_sleep=1,
        ):
            self.log_warning("未找到左下角『放弃』按钮")
            return False
        return True
