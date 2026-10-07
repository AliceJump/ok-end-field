import unittest
from unittest.mock import patch

from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_TYPE,
    BATTLE_ROOT_CONFIGS,
    DEFAULT_BATTLE_CONFIG,
    KEY_BATTLE_INITIAL_WAIT,
    KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY,
)
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
from src.tasks.onetime.ImmediateTimedCombatLogic import ImmediateTimedCombatLogic
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestConditionalRotation import _FakeTask


class TestCombatStartupWait(unittest.TestCase):
    def test_wait_settings_are_no_longer_registered(self):
        for key in (KEY_BATTLE_INITIAL_WAIT, KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY):
            with self.subTest(key=key):
                self.assertNotIn(key, DEFAULT_BATTLE_CONFIG)
                self.assertNotIn(key, BATTLE_ROOT_CONFIGS)
                self.assertNotIn(key, BATTLE_CONFIG_TYPE)
                self.assertNotIn(key, BATTLE_CONFIG_DESCRIPTION)

    def test_legacy_combat_does_not_block_on_start_sleep(self):
        def elapsed(start_sleep):
            task = _FakeTask({"技能释放": ["1"], "启动技能点数": 1}, skill=3)
            self.assertTrue(AutoCombatLogic(task).run(start_sleep=start_sleep))
            self.assertTrue(task.actions)
            return task.active_time()

        self.assertAlmostEqual(elapsed(3), elapsed(0), places=6)

    @patch.object(TimedCombatLogic, "run", return_value=True)
    def test_timing_combat_forces_zero_startup_delay(self, base_run):
        logic = ImmediateTimedCombatLogic(object())

        self.assertTrue(logic.run(start_sleep=3, no_battle=False, deadline=10))
        base_run.assert_called_once_with(start_sleep=0, no_battle=False, deadline=10)


if __name__ == "__main__":
    unittest.main()
