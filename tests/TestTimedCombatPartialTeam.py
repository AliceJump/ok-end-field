import unittest

from src.data.skill_timing import load_skill_timings
from src.patches.timed_team_detection_patch import _call_with_incremental_stability
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic


class _PartialTeamTask:
    def __init__(self, detected_team, member_count=4):
        self.detected_team = list(detected_team)
        self._battle_member_count = member_count
        self._battle_team = None
        self._battle_team_disabled_slots = set()
        self._squad_dead_slots = set()
        self.messages = []

    def detect_team_stable(self, **_kwargs):
        return list(self.detected_team), True

    def log_info(self, message, **_kwargs):
        self.messages.append(message)

    def log_debug(self, message, **_kwargs):
        self.messages.append(message)


class TestTimedCombatPartialTeam(unittest.TestCase):
    def _logic(self, detected_team, member_count=4):
        task = _PartialTeamTask(detected_team, member_count)
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: 0.0)
        return task, logic

    def test_long_identity_failure_never_means_death(self):
        task, logic = self._logic(["佩丽卡", "艾维文娜", "梨诺", "?"], member_count=3)
        logic._detect_team(1)
        task.detected_team = ["?", "艾维文娜", "梨诺", "?"]
        for _ in range(12):
            logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, set())
        self.assertEqual(logic.team, ["佩丽卡", "艾维文娜", "梨诺"])

    def test_all_unknown_identities_still_process_confirmed_deaths(self):
        task, logic = self._logic(["佩丽卡", "狼卫", "陈千语", "管理员"])
        logic._detect_team(1)
        task._squad_dead_slots = {0, 1, 2, 3}
        task.detect_team_stable = lambda **kwargs: (["?"] * 4, False)
        logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, {"1", "2", "3", "4"})
        self.assertEqual(task._battle_member_count, 4)

    def test_initially_unknown_dead_identity_can_be_filled_and_restored(self):
        task, logic = self._logic(["佩丽卡", "?", "梨诺", "?"], member_count=3)
        task._squad_dead_slots = {1}
        logic._detect_team(1)
        self.assertEqual(logic.disabled_slots, {"2"})
        task._squad_dead_slots = set()
        task.detected_team = ["佩丽卡", "艾维文娜", "梨诺", "?"]
        logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, set())
        self.assertTrue(logic._slot_available("2"))

    def test_native_short_teams_use_left_aligned_portraits_and_actual_slot_numbers(self):
        portraits = ["佩丽卡", "艾维文娜", "梨诺", "?"]
        for count in (1, 2, 3):
            with self.subTest(count=count):
                task, logic = self._logic(portraits, member_count=count)
                logic._detect_team(1)

                self.assertEqual(logic.team, portraits[:count])
                self.assertEqual(task._battle_team, portraits[:count])
                self.assertEqual(task._battle_team_disabled_slots, set())
                self.assertTrue(logic.order)
                self.assertEqual(set(logic.ult_order), {str(slot) for slot in range(1, count + 1)})
                self.assertTrue(all(1 <= int(token) <= count for token in logic.order))
                self.assertFalse(logic._slot_available(str(count + 1)))

    def test_unconfirmed_skill_layout_does_not_assume_four_members(self):
        _task, logic = self._logic(["佩丽卡", "艾维文娜", "梨诺", "?"], member_count=0)
        logic._detect_team(1)
        self.assertEqual(logic.team, [])

    def test_portrait_match_outside_native_team_cannot_start_scheduler(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                _task, logic = self._logic(["?", "?", "?", "佩丽卡"], member_count=count)
                logic._detect_team(1)
                self.assertEqual(logic.team, [])

    def test_missing_portrait_inside_native_three_person_team_keeps_its_slot(self):
        task, logic = self._logic(["佩丽卡", "?", "梨诺", "?"], member_count=3)
        logic._detect_team(1)
        self.assertEqual(logic.team, ["佩丽卡", "?", "梨诺"])
        self.assertEqual(task._battle_team_disabled_slots, {1})

        task.detected_team = ["佩丽卡", "艾维文娜", "梨诺", "狼卫"]
        logic._refresh_team_slots(1)
        self.assertEqual(logic.team, ["佩丽卡", "艾维文娜", "梨诺"])
        self.assertTrue(logic._slot_available("2"))
        self.assertFalse(logic._slot_available("4"))

    def test_native_three_person_team_keeps_layout_when_first_member_is_lost(self):
        task, logic = self._logic(["佩丽卡", "艾维文娜", "梨诺", "?"], member_count=3)
        logic._detect_team(1)
        task.detected_team = ["?", "艾维文娜", "梨诺", "?"]
        task._squad_dead_slots = {0}
        task._battle_member_count = 0
        for _ in range(3):
            logic._refresh_team_slots(1)

        self.assertEqual(logic.team, ["佩丽卡", "艾维文娜", "梨诺"])
        self.assertEqual(logic.disabled_slots, {"1"})
        self.assertEqual(task._battle_team_disabled_slots, {0})
        self.assertTrue(logic._slot_available("2"))
        self.assertTrue(logic._slot_available("3"))

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

        for _ in range(5):
            logic._refresh_team_slots(1)

        self.assertNotIn("3", logic.disabled_slots)
        self.assertIn(2, task._battle_team_disabled_slots)

    def test_fill_unknown_while_other_known_slot_is_transiently_missing(self):
        partial = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(partial)
        logic._detect_team(1)
        task.detected_team = ["佩丽卡", "?", "陈千语", "管理员"]

        logic._refresh_team_slots(1)

        self.assertEqual(logic.team, ["佩丽卡", "狼卫", "陈千语", "管理员"])
        self.assertEqual(logic.disabled_slots, set())

    def test_disabled_portrait_recovers_in_original_slot_without_resetting_cooldowns(self):
        original = ["佩丽卡", "狼卫", "陈千语", "管理员"]
        task, logic = self._logic(original)
        logic._detect_team(1)
        logic.cooldowns["sentinel"] = 12.5
        logic.state_until["1"] = 9.0
        logic.normal_attack_sp_gains = dict.fromkeys(original, 10)
        logic.normal_attack_sp_gains["狼卫"] = 100
        logic.cursor = 1
        order = list(logic.order)
        task.detected_team[1] = "?"
        task._squad_dead_slots = {1}
        for _ in range(3):
            logic._refresh_team_slots(1)
        self.assertFalse(logic._slot_available("2"))
        self.assertEqual(task._battle_team_disabled_slots, {1})
        self.assertEqual(logic.assume_success_sp_threshold, 15)

        task.detected_team = original
        task._squad_dead_slots = set()
        logic._refresh_team_slots(1)
        self.assertTrue(logic._slot_available("2"))
        self.assertEqual(logic.disabled_slots, set())
        self.assertEqual(task._battle_team_disabled_slots, set())
        self.assertEqual(logic.team, original)
        self.assertEqual(task._battle_member_count, 4)
        self.assertEqual(logic.order, order)
        self.assertEqual(logic.cursor, 1)
        self.assertEqual(logic.cooldowns["sentinel"], 12.5)
        self.assertEqual(logic.state_until["1"], 9.0)
        self.assertEqual(logic.assume_success_sp_threshold, 105)
        self.assertTrue(any("恢复失效槽位" in message for message in task.messages))

    def test_recovery_waits_for_stability_across_scheduler_ticks(self):
        original = ["佩丽卡", "艾维文娜", "梨诺", "?"]
        task, logic = self._logic(original, member_count=3)
        logic._detect_team(1)
        task.detected_team[0] = "?"
        task._squad_dead_slots = {0}
        for _ in range(3):
            logic._refresh_team_slots(1)
        task.detected_team = original
        task._squad_dead_slots = set()
        task.frame = object()
        task.now = 0.0
        task.active_time = lambda: task.now
        task.detect_team = lambda _frame: list(task.detected_team)

        def refresh():
            _call_with_incremental_stability(logic, "refresh", TimedCombatLogic._refresh_team_slots, 1)

        refresh()
        self.assertEqual(logic.disabled_slots, {"1"})
        task.now = 1.0
        refresh()
        self.assertEqual(logic.disabled_slots, set())
        self.assertEqual(logic.team, original[:3])
        self.assertEqual(task._battle_member_count, 3)
        self.assertEqual(task._battle_team_disabled_slots, set())

    def test_recovery_preserves_unknown_and_still_disabled_slots(self):
        original = ["佩丽卡", "狼卫", "?", "管理员"]
        task, logic = self._logic(original)
        logic._detect_team(1)
        task.detected_team = ["?", "?", "?", "管理员"]
        task._squad_dead_slots = {0, 1}
        for _ in range(3):
            logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, {"1", "2"})
        task.detected_team = ["佩丽卡", "?", "?", "管理员"]
        task._squad_dead_slots = {1}
        logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, {"2"})
        self.assertEqual(task._battle_team_disabled_slots, {1, 2})
        self.assertTrue(logic._slot_available("1"))
        self.assertFalse(logic._slot_available("2"))
        self.assertFalse(logic._slot_available("3"))

    def test_missing_or_wrong_portrait_does_not_restore_disabled_slot(self):
        original = ["佩丽卡", "狼卫", "陈千语", "管理员"]
        task, logic = self._logic(original)
        logic._detect_team(1)
        task.detected_team[1] = "?"
        task._squad_dead_slots = {1}
        for _ in range(3):
            logic._refresh_team_slots(1)
        for current in ("?", "洛茜"):
            task.detected_team[1] = current
            logic._refresh_team_slots(1)
            self.assertEqual(logic.disabled_slots, {"2"})
        task.detected_team = ["?"] * 4
        logic._refresh_team_slots(1)
        self.assertEqual(logic.disabled_slots, {"2"})

    def test_recovered_slot_can_be_disabled_again(self):
        original = ["佩丽卡", "狼卫", "陈千语", "管理员"]
        task, logic = self._logic(original)
        logic._detect_team(1)
        for _ in range(2):
            task.detected_team[1] = "?"
            task._squad_dead_slots = {1}
            for _ in range(3):
                logic._refresh_team_slots(1)
            self.assertEqual(logic.disabled_slots, {"2"})
            task.detected_team = list(original)
            task._squad_dead_slots = set()
            logic._refresh_team_slots(1)
            self.assertEqual(logic.disabled_slots, set())


if __name__ == "__main__":
    unittest.main()
