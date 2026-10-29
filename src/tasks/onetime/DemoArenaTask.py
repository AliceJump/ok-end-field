"""百趣演武：从当前界面直接执行演武的抽牌与战斗步骤。

活动「百趣演武」延续了选剑演武的「抽取 + 战斗」玩法，界面与日常演算基本一致，
差别主要在奖励与风险。因此这里不做任何导航：

1. 先手动把游戏切到活动的演武界面（抽牌页，能看到等级条）；
2. 再运行本任务，它只做「读等级 → 抽牌 → 判断 → 开打 / 放弃重开」。

抽牌策略
--------
- **选剑（默认）**：沿用「演算抽牌」的判定——整轮只掉一级且最终停在第 10 级，
  这类牌面的收益最高；不满足就放弃重开。
- **快速**：沿用日常演算的判定——等级抽到 5 级以上就直接开打。
- **最高期望原则**：按选剑演武的期望奖励 MDP 求解器逐步决策（抽牌 / 翻倍 / 开始演算 /
  放弃），不再用固定判定。手牌张数与剩余次数由代码自己记，战力点仍取界面等级条。

三者都支持「自动战斗」，关闭时只负责抽牌，方便手动接管。
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
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.common import Common
from src.tasks.mixin.demo_level_mixin import DemoLevelMixin

#: 抽牌次数上限（与演算一致：一轮最多抽 5 张）
MAX_DRAWS = 5
#: 总共的等级数，0~10 级
MAX_LEVEL = 10
#: 快速策略的开打等级
FAST_BATTLE_LEVEL = 5


class DemoArenaTask(DemoLevelMixin, BattleMixin, Common):
    """百趣演武：假定已在演武界面，只跑抽牌与战斗。"""

    CFG_MODE = "抽牌策略"
    CFG_BATTLE = "自动战斗"
    CFG_MAX_RESTART = "最多重开次数"
    CFG_REMAIN_CALC = "剩余演算次数"
    CFG_REMAIN_ABAND = "剩余放弃次数"
    CFG_REMAIN_DOUBLE = "剩余翻倍次数"

    MODE_XUANJIAN = "选剑（只降一级且停在第10级）"
    MODE_FAST = "快速（等级过 5 就开打）"
    MODE_EXPECTED = "最高期望原则"
    MODES = (MODE_XUANJIAN, MODE_FAST, MODE_EXPECTED)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "百趣演武"
        self.icon = Icons.SwordChallenge
        self.group_name = "战斗"
        self.description = (
            "活动百趣演武用：先手动进入活动的演武界面，再运行本任务，按所选策略抽牌，"
            "满足条件后开打（或只抽牌交给你手动打）。"
        )
        self.default_config.update(
            {
                self.CFG_MODE: self.MODE_XUANJIAN,
                self.CFG_BATTLE: True,
                self.CFG_MAX_RESTART: 30,
                self.CFG_REMAIN_CALC: 3,
                self.CFG_REMAIN_ABAND: 3,
                self.CFG_REMAIN_DOUBLE: 2,
            }
        )
        self.config_type[self.CFG_MODE] = {"type": "drop_down", "options": list(self.MODES)}
        self.config_type[self.CFG_REMAIN_CALC] = {"min": 1, "max": 4}
        self.config_type[self.CFG_REMAIN_ABAND] = {"min": 0, "max": 3}
        self.config_type[self.CFG_REMAIN_DOUBLE] = {"min": 0, "max": 2}
        self.config_description.update(
            {
                self.CFG_MODE: (
                    "选剑：整轮只掉一级且最终停在第 10 级才开打，收益最高但重开次数多。\n"
                    "快速：等级抽到 5 级以上就开打，省时间。\n"
                    "最高期望原则：按期望奖励逐个动作决策（抽牌 / 翻倍 / 开始演算 / 放弃），"
                    "期望最高的牌面才开打。"
                ),
                self.CFG_BATTLE: "开启后满足条件直接进入战斗；关闭则只抽牌，战斗由你手动接管。",
                self.CFG_MAX_RESTART: "不满足条件时最多放弃重开多少次。",
                self.CFG_REMAIN_CALC: "最高期望原则用：本次还剩几次演算机会（1~4，跨日残局为 4）。",
                self.CFG_REMAIN_ABAND: "最高期望原则用：本次还剩几次放弃机会（0~3）。",
                self.CFG_REMAIN_DOUBLE: "最高期望原则用：本次还剩几次翻倍机会（0~2）。",
            }
        )

    def run(self):
        mode = self.config.get(self.CFG_MODE, self.MODE_XUANJIAN)
        auto_battle = bool(self.config.get(self.CFG_BATTLE, True))
        max_restart = int(self.config.get(self.CFG_MAX_RESTART, 30) or 0)
        if mode not in self.MODES:
            mode = self.MODE_XUANJIAN

        if self.read_level() < 0:
            self.log_error("没有看到演武的等级条，请先手动进入百趣演武的抽牌界面再运行")
            return

        if mode == self.MODE_EXPECTED:
            self.run_by_expectation(auto_battle, max_restart)
            return

        for restart in range(max(1, max_restart)):
            if self.exit_is_set():
                return
            if self.draw_pass(mode):
                self.log_info(f"第 {restart + 1} 轮抽牌满足条件（{mode}）", notify=True)
                if not auto_battle:
                    self.log_info("已关闭自动战斗，抽牌到此为止", notify=True)
                    return
                self.fight_once()
                return
            self.log_info(f"第 {restart + 1} 轮未满足条件，放弃后重开")
            if not self.give_up_and_restart():
                return
        self.log_warning(f"已达到最多重开次数 {max_restart}，停止", notify=True)

    # ---------- 抽牌 ----------

    def draw_pass(self, mode, start_count=0):
        """抽满一轮并按策略判定是否值得开打。"""
        level = self.read_level()
        if level < 0:
            return False
        down_count = 0
        for _ in range(MAX_DRAWS - start_count):
            if not self.wait_click_feature(
                feature=fL.demo_random_button,
                time_out=10,
                raise_if_not_found=False,
                click_after_delay=0.5,
            ):
                self.log_warning("未找到抽牌按钮，可能不在演武抽牌界面")
                return False
            current = self.wait_level_change(level, time_out=4)
            if current is None:
                self.log_warning("抽牌后等级没有变化")
                return False
            if current < level:
                down_count += 1
            level = current
            self.log_info(f"当前等级 {level}，掉级次数 {down_count}")
            if mode == self.MODE_FAST and level > FAST_BATTLE_LEVEL:
                return True
        return self.is_good_draw(mode, down_count, level)

    @staticmethod
    def is_good_draw(mode, down_count, level):
        """选剑：只掉一级且停在第 10 级；快速：等级超过阈值。"""
        if mode == DemoArenaTask.MODE_FAST:
            return level > FAST_BATTLE_LEVEL
        return down_count == 1 and level == MAX_LEVEL

    def give_up_and_restart(self):
        if not self.wait_click_ocr(
            match=self.lang.daily_battle_mixin.k_b8a81b7a,
            box=self.box.bottom_left,
            time_out=5,
            raise_if_not_found=False,
        ):
            self.log_warning("未找到左下角『放弃』按钮")
            return False
        self.wait_ui_stable(refresh_interval=1)
        return True

    # ---------- 最高期望原则 ----------

    def run_by_expectation(self, auto_battle, max_restart):
        """最高期望原则：每抽一张都问一次求解器，严格按它给的最优动作执行。"""
        draw_state = self.start_draw_state()
        solver = get_solver()
        for restart in range(max(1, max_restart)):
            if self.exit_is_set():
                return
            if not draw_state.has_remain_calc():
                self.log_warning("剩余演算次数已用完，停止抽牌", notify=True)
                return
            action = self.draw_round_by_expectation(draw_state, solver)
            if action is None:
                return
            if action == CALCULATE:
                self.log_info(f"第 {restart + 1} 轮抽牌满足条件（{self.MODE_EXPECTED}）", notify=True)
                if not auto_battle:
                    self.log_info("已关闭自动战斗，抽牌到此为止", notify=True)
                    return
                self.fight_once()
                return
            self.log_info(f"第 {restart + 1} 轮按最高期望原则放弃，重开")
            if not self.give_up_and_restart():
                return
        self.log_warning(f"已达到最多重开次数 {max_restart}，停止", notify=True)

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
        if not 0 <= power <= MAX_LEVEL:
            self.log_warning(f"读到的战力点 {power} 不在 0~{MAX_LEVEL} 之间，本轮改用原策略判定")
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
            self.log_warning("未找到抽牌按钮，可能不在演武抽牌界面")
            return None
        current_power = self.wait_level_change(previous_power, time_out=4)
        if current_power is None:
            self.log_warning("抽牌后等级没有变化")
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
            self.log_warning("未找到演武翻倍按钮")
            return False
        return True

    def finish_round_by_original(self, draw_state):
        """求解器不可用时的兜底：用原策略（选剑）的判定跑完本轮剩余抽牌。"""
        if draw_state.card_count() >= MAX_DRAWS:
            self.log_warning("本轮已抽满 5 张，无法再按原策略判定，直接放弃重开")
            draw_state.finish_round(ABANDON)
            return ABANDON
        if self.draw_pass(self.MODE_XUANJIAN, start_count=draw_state.card_count()):
            draw_state.finish_round(CALCULATE)
            return CALCULATE
        draw_state.finish_round(ABANDON)
        return ABANDON

    # ---------- 战斗 ----------

    def fight_once(self):
        if not self.wait_click_feature(
            feature=fL.start_demo, time_out=10, raise_if_not_found=False, click_after_delay=0.5
        ):
            self.log_warning("未找到开始演算按钮")
            return False
        if not self.wait_click_feature(
            feature=fL.give_gift,
            time_out=10,
            raise_if_not_found=False,
            settle_time=1,
            box=self.box_of_screen(0.944, 0.900, 0.969, 0.941),
        ):
            self.log_warning("未找到进入战斗按钮", images=[self.frame])
            return False
        self.ensure_main()
        self.auto_battle()
        self.log_info("战斗结束，百趣演武本次完成", notify=True)
        return True
