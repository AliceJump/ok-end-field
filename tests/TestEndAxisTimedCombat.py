import unittest

from src.core.BattleConfig import BATTLE_CONFIG_TYPE, DEFAULT_BATTLE_CONFIG, KEY_TIMING_ROTATION
from src.data.endaxis_timeline import EndAxisAction, EndAxisScenario
from src.data.skill_timing import load_skill_timings
from src.patches.endaxis_timed_combat_patch import (
    KEY_TIMING_ENDAXIS,
    _commit_action,
    _plan_elapsed,
    _sync_due_action,
    install_endaxis_timed_combat_patch,
    install_endaxis_timed_config,
)
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class _ClockLogic:
    def __init__(self, actions):
        self.now = 0.0
        self._clock = lambda: self.now
        self._endaxis_scenario = EndAxisScenario("clock", "Clock", tuple(actions))
        self._endaxis_cursor = 0
        self._endaxis_attempt_index = None
        self._endaxis_frozen = False
        self._endaxis_frozen_elapsed = 0.0
        self._endaxis_frozen_wall = None
        self._endaxis_not_before = 0.0
        self._endaxis_clock_elapsed = 0.0
        self._endaxis_clock_wall = 0.0
        self.task = _RuntimeTask()


class _RuntimeTask:
    def __init__(self):
        self.now = 0.0
        self.sp = 300.0
        self.keys = []
        self.messages = []
        self.mouse = []
        self._battle_team = None
        self._battle_member_count = 1
        self._battle_team_disabled_slots = set()
        self._squad_dead_slots = set()

    def active_time(self):
        return self.now

    def log_info(self, message, **kwargs):
        self.messages.append(message)

    log_warning = log_info
    log_debug = log_info

    def active_and_send_mouse_delta(self, **kwargs):
        pass

    def mouse_down(self, key):
        self.mouse.append(("down", key))

    def mouse_up(self, key):
        self.mouse.append(("up", key))

    def send_key(self, token):
        self.keys.append(token)


class TestEndAxisPlanClock(unittest.TestCase):
    def test_timed_config_is_registered_under_timing_mode(self):
        install_endaxis_timed_config()
        self.assertEqual(DEFAULT_BATTLE_CONFIG[KEY_TIMING_ENDAXIS], "")
        self.assertIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE)
        self.assertIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"][True])
        self.assertNotIn(KEY_TIMING_ENDAXIS, BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"][False])

    def test_due_action_freezes_clock_until_commit(self):
        logic = _ClockLogic(
            [
                EndAxisAction("skill", 1, 360, 0),
                EndAxisAction("ultimate", 2, 480, 0),
            ]
        )

        logic.now = 0.5
        self.assertIsNone(_sync_due_action(logic))
        self.assertAlmostEqual(_plan_elapsed(logic), 0.5)

        logic.now = 1.2
        first = _sync_due_action(logic)
        self.assertEqual((first.kind, first.slot), ("skill", 1))
        self.assertAlmostEqual(_plan_elapsed(logic), 1.0)

        logic.now = 5.0
        self.assertAlmostEqual(_plan_elapsed(logic), 1.0)
        self.assertTrue(_commit_action(logic, 0))

        logic.now = 6.0
        self.assertIsNone(_sync_due_action(logic))
        self.assertAlmostEqual(_plan_elapsed(logic), 2.0)

        logic.now = 7.0
        second = _sync_due_action(logic)
        self.assertEqual((second.kind, second.slot), ("ultimate", 2))
        self.assertAlmostEqual(_plan_elapsed(logic), 3.0)


class TestEndAxisTimedRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_endaxis_timed_combat_patch()

    def _logic(self):
        task = _RuntimeTask()
        logic = TimedCombatLogic(
            task,
            load_skill_timings(),
            clock=lambda: task.now,
            wall_clock=lambda: task.now,
        )
        logic.team = ["佩丽卡"]
        logic.order = ["1"]
        task._battle_team = list(logic.team)
        logic.main_control.update = lambda *args, **kwargs: False
        logic.main_control.record_cast = lambda *args, **kwargs: None
        logic._sample_sp = lambda force=False: task.sp
        logic._endaxis_scenario = EndAxisScenario(
            "runtime",
            "Runtime",
            (EndAxisAction("skill", 1, 300, 30),),
        )
        logic._endaxis_cursor = 0
        logic._endaxis_attempt_index = None
        logic._endaxis_frozen = False
        logic._endaxis_frozen_elapsed = 0.0
        logic._endaxis_frozen_wall = None
        logic._endaxis_not_before = 0.0
        logic._endaxis_clock_elapsed = 0.0
        logic._endaxis_clock_wall = 0.0
        return task, logic

    def test_high_cost_battle_action_commits_only_after_sp_drop_confirmation(self):
        task, logic = self._logic()

        logic.step()
        self.assertEqual(task.keys, ["1"])
        self.assertEqual(logic._endaxis_cursor, 0)
        self.assertEqual(logic._endaxis_attempt_index, 0)
        self.assertFalse(logic._endaxis_frozen)
        self.assertIsNotNone(logic.pending)

        task.now = 0.1
        task.sp = 190.0
        logic.step()

        self.assertEqual(logic._endaxis_cursor, 1)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertFalse(logic._endaxis_frozen)
        self.assertIsNone(logic.pending)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.1)

    def test_cancelled_battle_attempt_keeps_due_action_unconsumed_and_refreezes_clock(self):
        task, logic = self._logic()
        task.now = 0.25
        logic._endaxis_frozen = False
        logic._endaxis_clock_elapsed = 0.0
        logic._endaxis_clock_wall = 0.0
        logic._endaxis_attempt_index = 0
        logic.pending = (300.0, "1", 100.0)
        logic.active_slot = "1"
        logic.active_kind = "battle"

        logic._cancel_unstarted_action("battle", "1", task.now)

        self.assertEqual(logic._endaxis_cursor, 0)
        self.assertIsNone(logic._endaxis_attempt_index)
        self.assertTrue(logic._endaxis_frozen)
        self.assertAlmostEqual(_plan_elapsed(logic), 0.0)
        self.assertIsNone(logic.pending)


if __name__ == "__main__":
    unittest.main()
