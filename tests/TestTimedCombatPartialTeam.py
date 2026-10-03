import unittest

from src.data.skill_timing import load_skill_timings
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class _PartialTeamTask:
    def __init__(self, detected_team):
        self.detected_team = list(detected_team)
        self._battle_team = None
        self._battle_team_disabled_slots = set()
        self.messages = []

    def detect_team_stable(self, **_kwargs):
        return list(self.detected_team), True

    def log_info(self, message, **_kwargs):
        self.messages.append(message)

    def log_debug(self, message, **_kwargs):
        self.messages.append(message)


class TestTimedCombatPartialTeam(unittest.TestCase):
    def _logic(self, detected_team):
        task = _PartialTeamTask(detected_team)
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: 0.0)
        return task, logic

    def test_partial_team_starts_scheduler_with_known_slots(self):
        partial = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(partial)

        logic._detect_team(1)

        self.assertEqual(logic.team, partial)
        self.assertEqual(task._battle_team, partial)
        self.assertTrue(logic.order)
        self.assertTrue(all(logic.team[int(token) - 1] != "?" for token in logic.order))
        self.assertFalse(logic._slot_available("3"))
        self.assertIn(2, task._battle_team_disabled_slots)
        self.assertEqual(logic.disabled_slots, set())
        self.assertTrue(any("后台继续补全" in message for message in task.messages))

    def test_unknown_slot_is_completed_and_composition_data_rebuilt(self):
        partial = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(partial)
        logic._detect_team(1)
        self.assertNotIn("3", logic.state_specs)

        logic.cooldowns["sentinel"] = 12.5
        logic.state_until["1"] = 9.0
        task.detected_team = ["佩丽卡", "狼卫", "陈千语", "管理员"]

        logic._refresh_team_slots(1)

        self.assertEqual(logic.team, task.detected_team)
        self.assertTrue(logic._slot_available("3"))
        self.assertNotIn(2, task._battle_team_disabled_slots)
        self.assertIn("3", logic.state_specs)
        self.assertIn("3", logic.ult_state_specs)
        self.assertIn("陈千语", logic.normal_attack_sp_gains)
        self.assertEqual(logic.cooldowns["sentinel"], 12.5)
        self.assertEqual(logic.state_until["1"], 9.0)
        self.assertTrue(any("补全槽位" in message for message in task.messages))

    def test_unknown_slot_does_not_accumulate_death_evidence(self):
        partial = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(partial)
        logic._detect_team(1)

        for _ in range(logic._DEAD_SLOT_CONFIRM_REFRESHES + 2):
            logic._refresh_team_slots(1)

        self.assertNotIn("3", logic.dead_slot_evidence)
        self.assertNotIn("3", logic.disabled_slots)
        self.assertIn(2, task._battle_team_disabled_slots)

    def test_fill_unknown_while_other_known_slot_is_transiently_missing(self):
        partial = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(partial)
        logic._detect_team(1)
        task.detected_team = ["佩丽卡", "?", "陈千语", "管理员"]

        logic._refresh_team_slots(1)

        self.assertEqual(logic.team, ["佩丽卡", "狼卫", "陈千语", "管理员"])
        self.assertEqual(logic.dead_slot_evidence.get("2"), 1)
        self.assertNotIn("3", logic.dead_slot_evidence)
        self.assertEqual(logic.disabled_slots, set())


if __name__ == "__main__":
    unittest.main()
