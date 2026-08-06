import unittest
from unittest.mock import patch

from src.core.base_mixin.game_flow_mixin import GameFlowMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.map_mixin import MapMixin


class _EnsureMainHarness(GameFlowMixin):
    def __init__(self, wait_results):
        self.wait_results = iter(wait_results)
        self.esc_checks = []
        self.sleeps = []

    def check_resolution(self):
        pass

    def info_set(self, *args):
        pass

    def active_time(self):
        return 1

    def is_main(self, esc=False, need_active=True):
        self.esc_checks.append(esc)
        return False

    def wait_until(self, condition, **kwargs):
        condition()
        return next(self.wait_results)

    def sleep(self, timeout):
        self.sleeps.append(timeout)


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

    def to_near_transfer_point(self, after_track, need_location_list=None):
        return True


class TestStateDrivenWaits(unittest.TestCase):
    def test_ensure_main_observes_before_enabling_recovery(self):
        task = _EnsureMainHarness([None, True])

        task.ensure_main()

        self.assertEqual(task.esc_checks, [False, True])
        self.assertEqual(task.sleeps, [])

    def test_ensure_main_natural_success_never_enables_recovery(self):
        task = _EnsureMainHarness([True])

        task.ensure_main()

        self.assertEqual(task.esc_checks, [False])
        self.assertEqual(task.sleeps, [])

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

        result = MapMixin.task_to_transfer_point(task)

        self.assertTrue(result)
        self.assertEqual(task.keys, [("j", {})])
        self.assertEqual(task.click_kwargs, [{}])
        self.assertEqual(task.stable_waits, 1)


if __name__ == "__main__":
    unittest.main()
