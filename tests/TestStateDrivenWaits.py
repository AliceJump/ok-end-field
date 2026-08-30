import unittest
from unittest.mock import patch

from ok import Box
from src.core.base_mixin.game_flow_mixin import GameFlowMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.map_mixin import MapMixin


class _EnsureMainHarness(GameFlowMixin):
    def __init__(self, wait_results, simulated_time=None):
        self.wait_results = iter(wait_results)
        self.esc_checks = []
        self.sleeps = []
        self.logs = []
        self.simulated_time = simulated_time or [1]
        self.time_index = 0
        self.wait_calls = []

    def tr(self, message, **kwargs):
        return message

    def check_resolution(self):
        pass

    def info_set(self, *args):
        pass

    def active_time(self):
        if self.time_index < len(self.simulated_time):
            return self.simulated_time[self.time_index]
        return self.simulated_time[-1]

    def is_main(self, esc=False, need_active=True):
        self.esc_checks.append(esc)
        return False

    def wait_until(self, condition, **kwargs):
        self.wait_calls.append(kwargs)
        condition()
        if self.time_index + 1 < len(self.simulated_time):
            self.time_index += 1
        return next(self.wait_results)

    def sleep(self, timeout):
        self.sleeps.append(timeout)

    def log_info(self, message):
        self.logs.append(message)


class _EnsureMapHarness(GameFlowMixin):
    def __init__(self, in_map):
        self.in_map = in_map
        self.keys = []

    def active_time(self):
        return 0

    def box_of_screen(self, *args):
        return object()

    def find_one(self, feature, **kwargs):
        return self.in_map

    def wait_until(self, condition, **kwargs):
        return condition()

    def press_key(self, key):
        self.keys.append(key)
        self.in_map = True

    def log_info(self, message):
        pass


class _SafeBackHarness(RuntimeMixin):
    def __init__(self):
        self.target_visible = False
        self.back_count = 0

    def active_time(self):
        return 0

    def wait_until(self, condition, **kwargs):
        return condition()

    def find_one(self, *args, **kwargs):
        return self.target_visible

    def back(self):
        self.back_count += 1
        self.target_visible = True

    def log_info(self, message):
        pass

    def log_warning(self, message):
        pass


class _BattleHarness:
    def __init__(self):
        self.wait_timeouts = []
        self.logs = []
        self.frames = 0
        self.combat = False
        self.settlement_results = [True]

    def active_time(self):
        return 1

    def get_battle_config(self, key, default=None):
        return default

    def next_frame(self):
        self.frames += 1

    def in_combat(self, required_yellow=0):
        return self.combat

    def find_feature(self, **kwargs):
        return True

    def is_battle_settlement(self):
        return self.settlement_results.pop(0)

    def log_info(self, message):
        self.logs.append(message)

    def sleep(self, timeout):
        raise AssertionError("settlement success must not use a fixed sleep")


class _CombatExitHarness:
    def __init__(self):
        self.exit_check_count = 0

    def _check_single_exit_condition(self):
        raise AssertionError("existing exit condition must be reused")


class _ConfirmHarness(GameFlowMixin):
    def __init__(self):
        self.confirm_visible = True
        self.wait_timeouts = []
        self.clicks = []
        self.sleeps = []

    def next_frame(self):
        pass

    def active_time(self):
        return 0

    def find_confirm(self):
        return object() if self.confirm_visible else None

    def click(self, target, **kwargs):
        self.clicks.append(kwargs)
        self.confirm_visible = False

    def wait_until(self, condition, **kwargs):
        self.wait_timeouts.append(kwargs["time_out"])
        return condition()

    def sleep(self, timeout):
        self.sleeps.append(timeout)


class _RepeatingConfirmHarness(_ConfirmHarness):
    def sleep(self, timeout):
        self.sleeps.append(timeout)
        if timeout == 1:
            self.confirm_visible = True


class _TaskMapHarness:
    def __init__(self):
        self.box = type("Box", (), {"bottom_right": object(), "top": object()})()
        self.keys = []
        self.click_kwargs = []
        self.stable_waits = 0

    def ensure_main(self):
        pass

    def press_key(self, key, **kwargs):
        self.keys.append((key, kwargs))

    def wait_feature(self, **kwargs):
        return object()

    def wait_until(self, condition, **kwargs):
        return condition()

    def find_one(self, *args, **kwargs):
        return object()

    def box_of_screen(self, *args):
        return object()

    def click(self, target, **kwargs):
        self.click_kwargs.append(kwargs)

    def wait_ui_stable(self, **kwargs):
        self.stable_waits += 1

    def to_near_transfer_point(self, need_track, need_location_list=None, need_reserve_icon_name=None):
        return True


class TestStateDrivenWaits(unittest.TestCase):
    def test_ensure_main_observes_before_enabling_recovery(self):
        task = _EnsureMainHarness([None, True, True])

        task.ensure_main()

        # 第一段观察未过 → 恢复阶段疑似确认（按 ESC）+ 第二段 2 秒稳定（只观察不按键）
        self.assertEqual(task.esc_checks, [False, True, False])
        self.assertEqual(task.sleeps, [])

    def test_ensure_main_natural_success_requires_two_stages(self):
        task = _EnsureMainHarness([True, True])

        task.ensure_main()

        # 第一段疑似通过后仍须经过第二段稳定检查才最终确认，未进入恢复阶段
        self.assertEqual(task.esc_checks, [False, False])
        self.assertEqual(task.sleeps, [])

    def test_ensure_main_stage_two_failure_loops_recovery(self):
        task = _EnsureMainHarness([True, None, True, True])

        task.ensure_main()

        # 观察阶段第一段疑似通过但第二段 2 秒稳定失败 → 进入恢复阶段重新两段确认（循环），
        # 恢复阶段的第二段观察仍不按键（esc=False）
        self.assertEqual(task.esc_checks, [False, False, True, False])
        self.assertEqual(task.sleeps, [])

    def test_ensure_main_short_timeout_caps_phase_b(self):
        # 测试短超时场景（time_out=1.0）：相位B的超时时间应受限于剩余时间
        # 模拟时间：start=0, 相位A后=0.5, 相位B调用时剩余0.5秒
        task = _EnsureMainHarness([True, True], simulated_time=[0, 0.5, 0.5])

        task.ensure_main(time_out=1.0)

        # 验证第二个 wait_until (相位B) 使用了受限的超时时间
        self.assertEqual(len(task.wait_calls), 2)
        # 相位A: time_out=min(2.0, 1.0)=1.0
        self.assertEqual(task.wait_calls[0]["time_out"], 1.0)
        # 相位B: time_out=min(3.0, 0.5)=0.5, settle_time=min(2.0, 0.5)=0.5
        self.assertLessEqual(task.wait_calls[1]["time_out"], 0.5)
        self.assertLessEqual(task.wait_calls[1]["settle_time"], 0.5)
        self.assertEqual(task.esc_checks, [False, False])

    def test_ensure_main_near_deadline_skips_phase_b_when_no_time_remains(self):
        # 测试接近截止时间场景：相位A消耗大部分时间，相位B无剩余时间则返回False
        # 模拟时间：start=0, 相位A后=0.99, 剩余0.01秒
        # 当相位A返回False时，应尝试恢复路径
        task = _EnsureMainHarness([None, True, None], simulated_time=[0, 0.99, 1.0, 1.0])

        with self.assertRaises(Exception) as context:
            task.ensure_main(time_out=1.0)

        # 验证抛出正确的异常
        self.assertEqual(str(context.exception), "Please start in game world and in team!")
        # 相位A失败，进入恢复路径的相位A，相位B因剩余时间<=0而返回False
        self.assertEqual(len(task.wait_calls), 2)
        # 第一次相位A
        self.assertEqual(task.wait_calls[0]["time_out"], 1.0)
        # 恢复路径相位A，剩余时间接近0
        self.assertAlmostEqual(task.wait_calls[1]["time_out"], 0.01, places=2)

    def test_ensure_main_very_short_timeout_does_not_hang(self):
        # 测试极短超时场景（time_out=0.5）：确保不会挂起或超出预算
        # 模拟时间：start=0, 相位A后=0.3, 相位B有0.2秒剩余
        task = _EnsureMainHarness([True, True], simulated_time=[0, 0.3, 0.3])

        task.ensure_main(time_out=0.5)

        # 验证相位B的超时时间被正确限制
        self.assertEqual(len(task.wait_calls), 2)
        self.assertEqual(task.wait_calls[0]["time_out"], 0.5)
        # 相位B: 剩余0.2秒，time_out和settle_time都应<=0.2
        self.assertLessEqual(task.wait_calls[1]["time_out"], 0.2)
        self.assertLessEqual(task.wait_calls[1]["settle_time"], 0.2)
        self.assertEqual(task.esc_checks, [False, False])

    def test_ensure_main_recovery_stable_failure_keeps_looping(self):
        task = _EnsureMainHarness([None, True, None, True, True])

        task.ensure_main()

        # 恢复阶段第二段再次失败后继续循环：疑似(ESC) → 稳定(不按键) → 成功
        self.assertEqual(task.esc_checks, [False, True, False, True, False])
        self.assertEqual(task.sleeps, [])
        self.assertEqual(len([m for m in task.logs if "第二段稳定检查未通过" in m]), 1)

    def test_ensure_map_does_not_toggle_an_open_map(self):
        task = _EnsureMapHarness(in_map=True)

        task.ensure_map()

        self.assertEqual(task.keys, [])

    def test_ensure_map_waits_for_state_after_key(self):
        task = _EnsureMapHarness(in_map=False)

        task.ensure_map()

        self.assertEqual(task.keys, ["m"])

    def test_safe_back_observes_before_single_recovery_action(self):
        task = _SafeBackHarness()

        result = task.safe_back(feature="target", time_out=5, once_time_out=1)

        self.assertTrue(result)
        self.assertEqual(task.back_count, 1)

    @patch("src.tasks.mixin.battle_mixin.AutoCombatLogic")
    def test_battle_settlement_returns_as_soon_as_state_appears(self, combat_logic):
        combat_logic.return_value.run.return_value = True
        task = _BattleHarness()

        result = BattleMixin.auto_battle(task)

        self.assertTrue(result)
        self.assertEqual(task.frames, 1)
        self.assertIn("检测到战斗结算状态，战斗完成", task.logs)

    @patch("src.tasks.mixin.battle_mixin.AutoCombatLogic")
    def test_battle_restarts_when_combat_returns_during_settlement_wait(self, combat_logic):
        combat_logic.return_value.run.side_effect = [True, True]
        task = _BattleHarness()
        task.combat = True
        task.settlement_results = [False, True]

        result = BattleMixin.auto_battle(task)

        self.assertTrue(result)
        self.assertEqual(combat_logic.return_value.run.call_count, 2)
        self.assertEqual(task.frames, 2)

    def test_combat_exit_counter_reuses_existing_detection(self):
        task = _CombatExitHarness()

        self.assertFalse(BattleMixin.is_combat_ended(task, True))
        self.assertTrue(BattleMixin.is_combat_ended(task, True))
        self.assertEqual(task.exit_check_count, 0)

    def test_click_confirm_waits_for_disappearance_without_fixed_sleep(self):
        task = _ConfirmHarness()

        result = task.click_confirm(disappear_time_out=2)

        self.assertTrue(result)
        self.assertEqual(task.clicks, [{}])
        self.assertEqual(task.wait_timeouts, [2])
        self.assertEqual(task.sleeps, [])

    def test_click_confirm_keeps_explicit_after_sleep_contract(self):
        task = _ConfirmHarness()

        result = task.click_confirm(after_sleep=0.5, disappear_time_out=0)

        self.assertTrue(result)
        self.assertEqual(task.wait_timeouts, [])
        self.assertEqual(task.sleeps, [0.5])

    def test_click_confirm_keeps_delay_after_rechecked_confirmation(self):
        task = _RepeatingConfirmHarness()

        result = task.click_confirm(after_sleep=0.5, recheck_time=1)

        self.assertTrue(result)
        self.assertEqual(len(task.clicks), 2)
        self.assertEqual(task.sleeps, [0.5, 1, 0.5])

    def test_task_map_transition_uses_feature_and_stability_waits(self):
        task = _TaskMapHarness()

        result = MapMixin.task_to_transfer_point(task, need_location_list=[])

        self.assertTrue(result)
        self.assertEqual(task.keys, [("j", {})])
        self.assertEqual(task.click_kwargs, [{}])
        self.assertEqual(task.stable_waits, 1)

    def test_in_team_falls_back_to_later_skill_template(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 4)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                matches = {
                    ("skill_1", boxes[0].x): Box(101, 11, 10, 10, confidence=0.99),
                    ("skill_2", boxes[1].x): None,
                    ("skill_3", boxes[2].x): Box(301, 11, 10, 10, confidence=0.98),
                }
                return matches.get((feature, box.x))

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 3)
        self.assertIn("skill_3->框3", task.last_log)

    def test_in_team_requires_two_skill_matches(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 3)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                if feature == "skill_1" and box.x == boxes[0].x:
                    return Box(101, 11, 10, 10, confidence=0.99)
                return None

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertFalse(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 0)

    def test_in_team_accepts_skill_one_in_last_slot(self):
        boxes = [Box(index * 100, 10, 20, 20) for index in range(1, 5)]

        class StubTask:
            _battle_member_count = 0

            def _battle_feature_boxes(self, prefix):
                return boxes

            def find_one(self, feature, box):
                if feature == "skill_1" and box.x == boxes[-1].x:
                    return Box(401, 11, 10, 10, confidence=0.99)
                return None

            def log_debug(self, message):
                self.last_log = message

        task = StubTask()
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 1)


if __name__ == "__main__":
    unittest.main()
