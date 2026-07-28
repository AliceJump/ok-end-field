import unittest
from unittest.mock import patch

from src.core.base_mixin.game_flow_mixin import GameFlowMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.tasks.mixin.battle_mixin import BattleMixin


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

    def active_time(self):
        return 1

    def get_battle_config(self, key, default=None):
        return default

    def wait_until(self, condition, **kwargs):
        self.wait_timeouts.append(kwargs["time_out"])
        return condition()

    def find_feature(self, **kwargs):
        return True

    def log_info(self, message):
        self.logs.append(message)

    def sleep(self, timeout):
        raise AssertionError("settlement success must not use a fixed sleep")


class _CombatExitHarness:
    def __init__(self):
        self.exit_check_count = 0

    def _check_single_exit_condition(self):
        raise AssertionError("existing exit condition must be reused")


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
        self.assertEqual(task.wait_timeouts, [15])
        self.assertIn("检测到战斗结算状态，战斗完成", task.logs)

    def test_combat_exit_counter_reuses_existing_detection(self):
        task = _CombatExitHarness()

        self.assertFalse(BattleMixin.is_combat_ended(task, True))
        self.assertTrue(BattleMixin.is_combat_ended(task, True))
        self.assertEqual(task.exit_check_count, 0)


if __name__ == "__main__":
    unittest.main()
