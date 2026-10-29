"""演算抽牌：在演武台前反复抽取，直到抽到值得开打的牌面。

抽牌策略
--------
- **原策略（默认）**：整轮抽 5 张只掉一级且最终停在第 10 级才算满足条件，否则放弃重开。
- **最高期望原则**：按选剑演武的期望奖励 MDP 求解器逐步决策（抽牌 / 翻倍 / 开始演算 /
  放弃），不再用固定判定。手牌张数与剩余次数由代码自己记，战力点仍取界面等级条。
"""

from src.core.trial_of_swordmancy_solver import (
    ABANDON,
    CALCULATE,
    DOUBLE,
    DrawState,
    expected_action,
    format_decision,
    format_state,
    get_solver,
    representative_hand,
)
from src.data.FeatureList import FeatureList as fL
from src.icons import Icons
from src.tasks.mixin.common import Common
from src.tasks.mixin.demo_level_mixin import DemoLevelMixin

#: 一轮最多抽 5 张
MAX_DRAWS = 5
#: 等级条上的战斗力点数上限
MAX_POWER = 10


class DemoDrawTask(DemoLevelMixin, Common):
    CFG_MAX_RESTART = "最多重开次数"
    CFG_STRATEGY = "抽牌策略"
    STRATEGY_ORIGINAL = "原策略"
    STRATEGY_EXPECTED = "最高期望原则"
    STRATEGIES = (STRATEGY_ORIGINAL, STRATEGY_EXPECTED)
    CFG_REMAIN_CALC = "剩余演算次数"
    CFG_REMAIN_ABAND = "剩余放弃次数"
    CFG_REMAIN_DOUBLE = "剩余翻倍次数"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "演算抽牌"
        self.icon = Icons.SwordChallenge
        self.group_name = "战斗"
        self.description = "在演武台面前自动进入演算抽牌页，循环抽取直到满足等级变化条件"

        self.default_config.update(
            {
                self.CFG_MAX_RESTART: 30,
                self.CFG_STRATEGY: self.STRATEGY_ORIGINAL,
                self.CFG_REMAIN_CALC: 3,
                self.CFG_REMAIN_ABAND: 3,
                self.CFG_REMAIN_DOUBLE: 2,
            }
        )
        self.config_type[self.CFG_STRATEGY] = {"type": "drop_down", "options": list(self.STRATEGIES)}
        self.config_type[self.CFG_REMAIN_CALC] = {"min": 1, "max": 4}
        self.config_type[self.CFG_REMAIN_ABAND] = {"min": 0, "max": 3}
        self.config_type[self.CFG_REMAIN_DOUBLE] = {"min": 0, "max": 2}
        self.config_description.update(
            {
                self.CFG_MAX_RESTART: "每轮最多抽牌5次；未满足条件时点击左下角『放弃』并重新开始。",
                self.CFG_STRATEGY: (
                    "原策略：整轮只掉一级且最终停在第 10 级才算满足条件，否则放弃重开。\n"
                    "最高期望原则：按期望奖励逐个动作决策（抽牌 / 翻倍 / 开始演算 / 放弃），"
                    "不满足就放弃，达到期望最高的牌面才停。"
                ),
                self.CFG_REMAIN_CALC: "最高期望原则用：本次还剩几次演算机会（1~4，跨日残局为 4）。",
                self.CFG_REMAIN_ABAND: "最高期望原则用：本次还剩几次放弃机会（0~3）。",
                self.CFG_REMAIN_DOUBLE: "最高期望原则用：本次还剩几次翻倍机会（0~2）。",
            }
        )

    def run(self):
        if self.draw_strategy() == self.STRATEGY_EXPECTED:
            return self.run_by_expectation()
        max_restart_times = int(self.config.get(self.CFG_MAX_RESTART, 30))
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

    def draw_strategy(self):
        """读抽牌策略；没登记的值一律按原策略处理，保证默认行为不变。"""
        strategy = self.config.get(self.CFG_STRATEGY, self.STRATEGY_ORIGINAL)
        return strategy if strategy in self.STRATEGIES else self.STRATEGY_ORIGINAL

    def enter_draw_page(self):
        self.ensure_main()
        self.press_key("f")
        self.wait_ui_stable(refresh_interval=1)
        if self.read_level() < 0:
            self.log_warning("按 F 后未进入演算抽牌页面")
            return False
        return True

    def draw_until_target(self, start_count=0):
        down_count = 0
        diff_penalty_sum = 0
        previous_level = self.read_level()
        if previous_level < 0:
            return False
        current_level = previous_level

        for draw_index in range(start_count, MAX_DRAWS):
            if not self.wait_click_feature(
                feature=fL.demo_random_button,
                time_out=10,
                raise_if_not_found=False,
                click_after_delay=0.5,
            ):
                self.log_warning("未找到演算抽牌按钮")
                return False
            current_level = self.wait_level_change(previous_level, time_out=4)
            if current_level is None:
                self.log_warning("抽牌后等级未变化")
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

        return down_count == 1 and current_level == MAX_POWER

    def abandon_draw_page(self):
        if not self.wait_click_ocr(
            match=self.lang.daily_battle_mixin.k_b8a81b7a,
            box=self.box.bottom_left,
            time_out=5,
            raise_if_not_found=False,
        ):
            self.log_warning("未找到左下角『放弃』按钮")
            return False
        return True

    # ---------- 最高期望原则 ----------

    def run_by_expectation(self):
        """最高期望原则：每抽一张都问一次求解器，严格按它给的最优动作执行。"""
        max_restart_times = int(self.config.get(self.CFG_MAX_RESTART, 30))
        draw_state = self.start_draw_state()
        solver = get_solver()
        for restart_index in range(max_restart_times):
            if not draw_state.has_remain_calc():
                self.log_warning("剩余演算次数已用完，停止抽牌", notify=True)
                return False
            if not self.enter_draw_page():
                return False
            action = self.draw_round_by_expectation(draw_state, solver)
            if action is None:
                return False
            if action == CALCULATE:
                self.log_info("抽牌结果满足条件，任务停止", notify=True)
                return True
            self.log_info(f"第 {restart_index + 1} 轮按最高期望原则放弃，重新开始")
            if not self.abandon_draw_page():
                return False
        self.log_warning(f"已达到最多重开次数 {max_restart_times}，任务停止", notify=True)
        return False

    def start_draw_state(self):
        """按配置初始化跨轮局面：剩余演算/放弃/翻倍次数，手牌为空且未翻倍。"""
        return DrawState.from_config(
            remain_calc=self.config.get(self.CFG_REMAIN_CALC, 3),
            remain_aband=self.config.get(self.CFG_REMAIN_ABAND, 3),
            remain_double=self.config.get(self.CFG_REMAIN_DOUBLE, 2),
        )

    def draw_round_by_expectation(self, draw_state, solver):
        """按最高期望抽完本轮，返回本轮结束动作（开始演算 / 放弃），异常返回 None。"""
        power = self.read_level()
        if power < 0:
            return None
        if not 0 <= power <= MAX_POWER:
            self.log_warning(f"读到的战力点 {power} 不在 0~{MAX_POWER} 之间，本轮改用原策略判定")
            return self.finish_round_by_original(draw_state)
        while True:
            action, expected = expected_action(solver, draw_state)
            if action is None:
                self.log_warning(f"求解器判定当前局面不可达（{format_state(draw_state, power)}），本轮改用原策略判定")
                return self.finish_round_by_original(draw_state)
            self.log_info(format_decision(draw_state, power, action, expected))
            if action == DOUBLE and not draw_state.is_doubled:
                if not self.click_double():
                    return None
                draw_state.mark_doubled()
                # 翻倍不消耗抽牌次数，重新决策
                continue
            if action == CALCULATE:
                draw_state.finish_round(CALCULATE)
                return CALCULATE
            if action == ABANDON:
                draw_state.finish_round(ABANDON)
                return ABANDON
            if action == DOUBLE:
                self.log_warning("求解器给出翻倍，但本局已经翻倍，改为继续抽牌")
            next_power = self.draw_one_card(power)
            if next_power is None:
                return None
            self.record_draw(draw_state, power, next_power)
            power = next_power

    def draw_one_card(self, previous_power):
        """点一次抽牌按钮并等战力点变化，返回新的战力点；失败返回 None。"""
        if not self.wait_click_feature(
            feature=fL.demo_random_button,
            time_out=10,
            raise_if_not_found=False,
            click_after_delay=0.5,
        ):
            self.log_warning("未找到演算抽牌按钮")
            return None
        current_power = self.wait_level_change(previous_power, time_out=4)
        if current_power is None:
            self.log_warning("抽牌后等级未变化")
            return None
        return current_power

    def record_draw(self, draw_state, previous_power, current_power):
        """按战力点变化推算这张牌的点数并记账；推算不出来时按张数与战力点估一组手牌。"""
        points = (current_power - previous_power) % 11
        if draw_state.add_card(points):
            self.log_info(f"抽到点数 {points}，当前 {format_state(draw_state, current_power)}")
            return
        self.log_warning(f"战力点 {previous_power} → {current_power} 推算不出合法点数，改用张数与战力点估算手牌")
        hand = representative_hand(draw_state.card_count() + 1, current_power)
        if hand is None:
            self.log_warning(f"也无法按 {draw_state.card_count() + 1} 张 / 战力点 {current_power} 估算手牌")
            return
        draw_state.replace_hand(hand)

    def click_double(self):
        """点『翻倍』按钮；点不到时返回 False，由调用方结束任务。"""
        if not self.wait_click_feature(
            feature=fL.demo_double_open,
            time_out=10,
            raise_if_not_found=False,
            click_after_delay=0.5,
        ):
            self.log_warning("未找到演算翻倍按钮")
            return False
        return True

    def finish_round_by_original(self, draw_state):
        """求解器不可用时的兜底：用原策略的判定跑完本轮剩余抽牌。"""
        if draw_state.card_count() >= MAX_DRAWS:
            self.log_warning("本轮已抽满 5 张，无法再按原策略判定，直接放弃重开")
            draw_state.finish_round(ABANDON)
            return ABANDON
        if self.draw_until_target(start_count=draw_state.card_count()):
            draw_state.finish_round(CALCULATE)
            return CALCULATE
        draw_state.finish_round(ABANDON)
        return ABANDON
