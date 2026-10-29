"""选剑演武最高期望策略：求解器、局面记账与两个任务接线。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.core.trial_of_swordmancy_solver import (
    ABANDON,
    CALCULATE,
    DOUBLE,
    DRAW,
    Config,
    DrawState,
    expected_action,
    format_decision,
    get_solver,
    hand_text,
    power_of,
    representative_hand,
    settle_reward,
    strategy_table,
    total_score,
)
from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.DemoArenaTask import DemoArenaTask
from src.tasks.onetime.DemoDrawTask import DemoDrawTask


class _ScriptedSolver:
    """按脚本返回求解结果，用来验证任务侧把动作翻译成点击/记账。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.states = []

    def decide(self, state):
        self.states.append(state)
        return self._outcomes.pop(0)


class _QuietTask:
    """把日志、点按与等级识别换成桩，保留任务的决策与记账逻辑。"""

    def exit_is_set(self):
        return False

    def log_info(self, message, **kwargs):
        self.log_lines.append(str(message))

    def log_warning(self, message, **kwargs):
        self.log_lines.append(str(message))

    def log_error(self, message, **kwargs):
        self.log_lines.append(str(message))

    def read_level(self):
        return self.levels[0]

    def wait_click_feature(self, feature=None, **kwargs):
        self.clicks.append(feature)
        return True

    def wait_level_change(self, previous_level, time_out=4):
        self.levels.pop(0)
        return self.levels[0] if self.levels else None


def make_task(task_type, levels):
    task = type("HarnessTask", (_QuietTask, task_type), {})(
        SimpleNamespace(scene=None, pause_start=None), SimpleNamespace()
    )
    task._init_topmost_mixin()  # run() 被 TOPMOST 混入包装，测试里补上初始化
    task.config = {}
    task.levels = list(levels)
    task.clicks = []
    task.log_lines = []
    return task


class TestSolverMath(unittest.TestCase):
    def test_total_score_and_power(self):
        self.assertEqual(total_score((1, 1, 0, 0, 0)), 3)
        self.assertEqual(power_of((0, 0, 0, 0, 3)), 4)
        self.assertEqual(power_of((0, 0, 0, 0, 0)), 0)

    def test_settle_reward_follows_overflow_and_double(self):
        hand = (0, 0, 0, 0, 3)  # 总点数 15，战力点 4
        self.assertEqual(settle_reward(Config(overflow="none"), hand, False), 0)
        self.assertEqual(settle_reward(Config(overflow="once"), hand, False), 7500)
        self.assertEqual(settle_reward(Config(overflow="twice"), hand, False), 7500)
        self.assertEqual(settle_reward(Config(overflow="twice"), hand, True), 15000)

    def test_representative_hand_respects_deck_size(self):
        self.assertEqual(representative_hand(0, 0), (0, 0, 0, 0, 0))
        self.assertIsNone(representative_hand(1, 6))
        for count in range(1, 6):
            for power in range(11):
                hand = representative_hand(count, power)
                if hand is None:
                    continue
                with self.subTest(count=count, power=power):
                    self.assertEqual(sum(hand), count)
                    self.assertEqual(power_of(hand), power)
                    for index, copies in enumerate(hand):
                        self.assertLessEqual(copies, Config().deck[index])


class TestDefaultPolicy(unittest.TestCase):
    """默认设定（牌库 4,5,6,6,7、剩余演算 3）下的最优策略表。"""

    @classmethod
    def setUpClass(cls):
        cls.rows = strategy_table(Config(), remain_calc=3, remain_aband=3)

    def actions(self, count):
        return {power: action for row_count, power, action, _expected in self.rows if row_count == count}

    def test_one_card_keeps_drawing(self):
        self.assertEqual(set(self.actions(1).values()), {DRAW})

    def test_two_cards_double(self):
        self.assertEqual(set(self.actions(2).values()), {DOUBLE})

    def test_three_and_four_cards_draw_low_power_and_calculate_high_power(self):
        for count in (3, 4):
            with self.subTest(count=count):
                actions = self.actions(count)
                for power in range(0, 9):
                    self.assertEqual(actions[power], DRAW)
                for power in (9, 10):
                    self.assertEqual(actions[power], CALCULATE)

    def test_five_cards_abandon_low_power_and_calculate_high_power(self):
        actions = self.actions(5)
        for power in range(0, 9):
            self.assertEqual(actions[power], ABANDON)
        for power in (9, 10):
            self.assertEqual(actions[power], CALCULATE)

    def test_decisions_are_sorted_by_expected_value(self):
        solver = get_solver()
        outcomes = solver.decide((3, 3, 2, False, (0, 1, 1, 0, 0)))
        self.assertGreater(len(outcomes), 1)
        expected = [value for _action, value in outcomes]
        self.assertEqual(expected, sorted(expected, reverse=True))


class TestUnreachableStates(unittest.TestCase):
    def test_absorbed_state_has_no_decision(self):
        self.assertEqual(get_solver().decide((0, 3, 2, False, (0, 0, 0, 0, 0))), [])
        self.assertEqual(get_solver().decide(None), [])

    def test_hand_beyond_deck_has_no_decision(self):
        # 牌库里只有 4 张点数 1，五张 1 不可能出现
        self.assertEqual(get_solver().decide((3, 3, 2, False, (5, 0, 0, 0, 0))), [])

    def test_doubled_without_double_chance_has_no_decision(self):
        self.assertEqual(get_solver().decide((3, 0, 0, True, (0, 1, 1, 0, 0))), [])

    def test_expected_action_reports_unreachable(self):
        self.assertEqual(expected_action(get_solver(), DrawState(0, 3, 2)), (None, 0.0))


class TestDrawState(unittest.TestCase):
    def test_from_config_clamps_values(self):
        state = DrawState.from_config(remain_calc=9, remain_aband=-1, remain_double=5)
        self.assertEqual((state.remain_calc, state.remain_aband, state.remain_double), (4, 0, 2))
        self.assertEqual(DrawState.from_config().solver_state(), (3, 3, 2, False, (0, 0, 0, 0, 0)))

    def test_add_card_counts_points(self):
        state = DrawState(3, 3, 2)
        self.assertTrue(state.add_card(3))
        self.assertTrue(state.add_card(3))
        self.assertTrue(state.add_card(5))
        self.assertEqual(state.hand, (0, 0, 2, 0, 1))
        self.assertEqual(state.card_count(), 3)

    def test_add_card_rejects_points_outside_one_to_five(self):
        for points in (0, 6, 11):
            with self.subTest(points=points):
                state = DrawState(3, 3, 2)
                self.assertFalse(state.add_card(points))
                self.assertEqual(state.card_count(), 0)

    def test_calculate_consumes_calc_and_double_chance(self):
        state = DrawState(3, 3, 2, is_doubled=True, hand=(0, 1, 1, 0, 0))
        state.finish_round(CALCULATE)
        self.assertEqual((state.remain_calc, state.remain_aband, state.remain_double), (2, 3, 1))
        self.assertFalse(state.is_doubled)
        self.assertEqual(state.card_count(), 0)

    def test_abandon_consumes_abandon_chance_first(self):
        state = DrawState(3, 3, 2, hand=(0, 1, 0, 0, 0))
        state.finish_round(ABANDON)
        self.assertEqual((state.remain_calc, state.remain_aband), (3, 2))

    def test_abandon_consumes_calc_when_abandon_chances_run_out(self):
        state = DrawState(2, 0, 2)
        state.finish_round(ABANDON)
        self.assertEqual((state.remain_calc, state.remain_aband), (1, 0))

    def test_abandon_on_cross_day_extra_round_consumes_calc(self):
        # 剩余演算 = 4 是跨日残局的额外一局，放弃也扣演算次数（照 MaaEnd）
        state = DrawState(4, 2, 2)
        state.finish_round(ABANDON)
        self.assertEqual((state.remain_calc, state.remain_aband), (3, 2))

    def test_unknown_round_end_action_raises(self):
        with self.assertRaises(ValueError):
            DrawState(3, 3, 2).finish_round(DRAW)


class TestExpectedAction(unittest.TestCase):
    def test_two_cards_double(self):
        state = DrawState(3, 3, 2, hand=(0, 1, 1, 0, 0))
        action, expected = expected_action(get_solver(), state)
        self.assertEqual(action, DOUBLE)
        self.assertGreater(expected, 0)

    def test_already_doubled_never_picks_double(self):
        state = DrawState(3, 3, 2, is_doubled=True, hand=(0, 1, 1, 0, 0))
        action, _expected = expected_action(get_solver(), state)
        self.assertNotEqual(action, DOUBLE)


class TestDecisionLog(unittest.TestCase):
    def test_decision_log_contains_count_power_action_and_expected(self):
        state = DrawState(3, 3, 2, is_doubled=True, hand=(0, 1, 1, 0, 0))
        line = format_decision(state, 5, CALCULATE, 123456.0)
        self.assertIn("手牌 2 张", line)
        self.assertIn("战力点 5", line)
        self.assertIn(CALCULATE, line)
        self.assertIn("123456", line)
        self.assertIn("已翻倍", line)

    def test_hand_text(self):
        self.assertEqual(hand_text((0, 0, 0, 0, 0)), "空")
        self.assertEqual(hand_text((2, 0, 0, 1, 0)), "1×2、4×1")


class TestTaskConfig(unittest.TestCase):
    def build(self, task_type):
        return task_type(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())

    def test_draw_task_defaults_to_original_strategy(self):
        task = self.build(DemoDrawTask)
        self.assertEqual(task.default_config[DemoDrawTask.CFG_STRATEGY], DemoDrawTask.STRATEGY_ORIGINAL)
        self.assertEqual(
            task.config_type[DemoDrawTask.CFG_STRATEGY],
            {"type": "drop_down", "options": ["原策略", "最高期望原则"]},
        )
        self.assertEqual(
            (task.default_config[DemoDrawTask.CFG_REMAIN_CALC], task.config_type[DemoDrawTask.CFG_REMAIN_CALC]),
            (3, {"min": 1, "max": 4}),
        )

    def test_arena_task_keeps_original_modes_and_adds_expected_strategy(self):
        task = self.build(DemoArenaTask)
        self.assertEqual(task.default_config[DemoArenaTask.CFG_MODE], DemoArenaTask.MODE_XUANJIAN)
        options = task.config_type[DemoArenaTask.CFG_MODE]["options"]
        self.assertEqual(options, [DemoArenaTask.MODE_XUANJIAN, DemoArenaTask.MODE_FAST, DemoArenaTask.MODE_EXPECTED])
        self.assertEqual(task.default_config[DemoArenaTask.CFG_REMAIN_DOUBLE], 2)

    def test_unknown_strategy_falls_back_to_original(self):
        task = self.build(DemoDrawTask)
        task.config = {DemoDrawTask.CFG_STRATEGY: "不存在的策略"}
        self.assertEqual(task.draw_strategy(), DemoDrawTask.STRATEGY_ORIGINAL)


class TestOriginalStrategyPath(unittest.TestCase):
    def test_draw_task_keeps_original_round_logic(self):
        task = make_task(DemoDrawTask, [0])
        task.enter_draw_page = Mock(return_value=True)
        task.draw_until_target = Mock(return_value=True)
        task.abandon_draw_page = Mock(return_value=True)
        task.run_by_expectation = Mock()
        with patch("src.tasks.onetime.DemoDrawTask.get_solver") as solver_factory:
            self.assertTrue(task.run())
        task.draw_until_target.assert_called_once_with()
        task.run_by_expectation.assert_not_called()
        solver_factory.assert_not_called()

    def test_arena_task_keeps_original_round_logic(self):
        task = make_task(DemoArenaTask, [3])
        task.draw_pass = Mock(return_value=True)
        task.fight_once = Mock(return_value=True)
        with patch("src.tasks.onetime.DemoArenaTask.get_solver") as solver_factory:
            task.run()
        task.draw_pass.assert_called_once_with(DemoArenaTask.MODE_XUANJIAN)
        task.fight_once.assert_called_once()
        solver_factory.assert_not_called()


class TestExpectedStrategyWiring(unittest.TestCase):
    def test_original_round_rule_can_continue_from_drawn_count(self):
        # start_count=3 时只剩 2 次抽牌：10 → 3（掉级）→ 10，仍满足原策略判定
        task = make_task(DemoDrawTask, [10, 3, 10])
        self.assertTrue(task.draw_until_target(start_count=3))
        self.assertEqual(len(task.clicks), 2)

    def test_original_round_rule_with_full_hand_returns_false(self):
        task = make_task(DemoDrawTask, [10])
        self.assertFalse(task.draw_until_target(start_count=5))
        self.assertEqual(task.clicks, [])

    def test_double_then_draw_then_calculate(self):
        task = make_task(DemoDrawTask, [0, 3])
        draw_state = DrawState.from_config(3, 3, 2)
        solver = _ScriptedSolver([[(DOUBLE, 100.0)], [(DRAW, 200.0)], [(CALCULATE, 300.0)]])

        self.assertEqual(task.draw_round_by_expectation(draw_state, solver), CALCULATE)

        self.assertEqual(task.clicks, [fL.demo_double_open, fL.demo_random_button])
        self.assertEqual((draw_state.remain_calc, draw_state.remain_aband, draw_state.remain_double), (2, 3, 1))
        self.assertFalse(draw_state.is_doubled)
        self.assertEqual(draw_state.card_count(), 0)
        decisions = [line for line in task.log_lines if line.startswith("最高期望原则")]
        self.assertEqual(len(decisions), 3)
        self.assertIn("最优动作 翻倍", decisions[0])

    def test_draw_then_abandon(self):
        task = make_task(DemoDrawTask, [0, 4])
        draw_state = DrawState.from_config(3, 3, 2)
        solver = _ScriptedSolver([[(DRAW, 10.0)], [(ABANDON, 20.0)]])

        self.assertEqual(task.draw_round_by_expectation(draw_state, solver), ABANDON)

        self.assertEqual((draw_state.remain_calc, draw_state.remain_aband), (3, 2))
        self.assertEqual(draw_state.card_count(), 0)

    def test_unreachable_state_falls_back_to_original_round(self):
        task = make_task(DemoDrawTask, [0])
        task.draw_until_target = Mock(return_value=True)
        draw_state = DrawState.from_config(3, 3, 2)
        solver = _ScriptedSolver([[]])

        self.assertEqual(task.draw_round_by_expectation(draw_state, solver), CALCULATE)

        task.draw_until_target.assert_called_once_with(start_count=0)
        self.assertTrue(any("不可达" in line for line in task.log_lines))
        self.assertEqual(draw_state.remain_calc, 2)

    def test_double_on_already_doubled_round_falls_back_to_draw(self):
        task = make_task(DemoDrawTask, [5, 8])
        draw_state = DrawState.from_config(3, 3, 2)
        draw_state.mark_doubled()
        solver = _ScriptedSolver([[(DOUBLE, 1.0)], [(ABANDON, 2.0)]])

        self.assertEqual(task.draw_round_by_expectation(draw_state, solver), ABANDON)

        self.assertNotIn(fL.demo_double_open, task.clicks)
        self.assertTrue(any("已经翻倍" in line for line in task.log_lines))

    def test_record_draw_derives_card_points_from_level_delta(self):
        task = make_task(DemoDrawTask, [0])
        draw_state = DrawState.from_config(3, 3, 2)
        task.record_draw(draw_state, 0, 3)
        self.assertEqual(draw_state.hand, (0, 0, 1, 0, 0))
        # 战力点从 8 变到 2 说明抽到点数 5（8+5=13，13 % 11 = 2）
        task.record_draw(draw_state, 8, 2)
        self.assertEqual(draw_state.hand, (0, 0, 1, 0, 1))

    def test_record_draw_with_impossible_delta_warns(self):
        task = make_task(DemoDrawTask, [0])
        draw_state = DrawState.from_config(3, 3, 2)
        task.record_draw(draw_state, 0, 7)
        self.assertEqual(draw_state.card_count(), 0)
        self.assertTrue(any("推算不出合法点数" in line for line in task.log_lines))

    def test_arena_runs_fight_once_after_calculate(self):
        task = make_task(DemoArenaTask, [7])
        task.fight_once = Mock(return_value=True)
        task.give_up_and_restart = Mock(return_value=True)
        solver = _ScriptedSolver([[(CALCULATE, 5000.0)]])

        with patch("src.tasks.onetime.DemoArenaTask.get_solver", return_value=solver):
            task.run_by_expectation(auto_battle=True, max_restart=3)

        task.fight_once.assert_called_once()
        task.give_up_and_restart.assert_not_called()

    def test_arena_without_auto_battle_stops_after_drawing(self):
        task = make_task(DemoArenaTask, [7])
        task.fight_once = Mock(return_value=True)
        solver = _ScriptedSolver([[(CALCULATE, 5000.0)]])

        with patch("src.tasks.onetime.DemoArenaTask.get_solver", return_value=solver):
            task.run_by_expectation(auto_battle=False, max_restart=3)

        task.fight_once.assert_not_called()

    def test_arena_stops_when_calc_chances_are_used_up(self):
        task = make_task(DemoArenaTask, [7])
        task.start_draw_state = Mock(return_value=DrawState(1, 0, 2))
        task.give_up_and_restart = Mock(return_value=True)
        solver = _ScriptedSolver([[(ABANDON, 1.0)]])

        with patch("src.tasks.onetime.DemoArenaTask.get_solver", return_value=solver):
            task.run_by_expectation(auto_battle=True, max_restart=10)

        # 剩余放弃为 0 时放弃扣演算次数：一次重开后就该停下来，不再空转
        task.give_up_and_restart.assert_called_once()
        self.assertTrue(any("剩余演算次数已用完" in line for line in task.log_lines))


if __name__ == "__main__":
    unittest.main()
