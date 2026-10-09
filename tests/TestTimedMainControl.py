import unittest

from src.data.skill_timing import load_skill_timings
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask


class _ControlTask(FakeTask):
    def __init__(self):
        super().__init__()
        self.current = 0
        self.switches = []
        self.arrow_reads = 0
        self.auto_switch_ults = set()
        self.sp = 0.0

    def detect_current_char_index(self):
        self.arrow_reads += 1
        return self.current

    def switch_main_control(self, slot):
        self.switches.append(slot)
        return BattleMixin.switch_main_control(self, slot)

    def use_ult(self, ult_sequence=None, wait_for_team_recovery=True):
        result = super().use_ult(ult_sequence, wait_for_team_recovery)
        if result and ult_sequence in self.auto_switch_ults:
            self.current = int(ult_sequence) - 1
        return result


def _control_logic(team):
    task = _ControlTask()
    logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
    logic._configure_team([*team, *(["?"] * (4 - len(team)))], reset_runtime=True)
    logic.team = list(team)
    task._battle_team = list(team)
    task._battle_member_count = len(team)
    logic.order = []
    logic.ult_order = []
    logic._hold(True)
    return task, logic


def _accept_cast(logic, token, kind="battle"):
    profiles = logic.store.profiles(logic.team[int(token) - 1], kind)
    logic._begin(profiles, logic.task.now, slot=token, kind=kind)
    if kind == "battle":
        logic._accept_battle_skill(token, advance_cursor=False)
    else:
        logic._activate_state(token, logic.ult_state_specs.get(token), "终结技")
        logic.main_control.record_cast(token, kind, profiles, logic.started)
        logic._clear_active()


class TestTimedMainControl(unittest.TestCase):
    def test_gilberta_cast_hands_main_control_to_an_available_teammate_after_commit(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        _accept_cast(logic, "1")
        task.now = 0.7
        logic.step()
        self.assertEqual(task.keys, [])
        task.now = 0.95
        logic.step()
        self.assertEqual(task.keys, ["f2"])
        self.assertEqual(logic.team, ["洁尔佩塔", "佩丽卡"])
        task.current = 1
        task.now += 0.05
        logic.step()
        self.assertEqual(task.keys, ["f2"])
        self.assertEqual(task.mouse[-1], "down")

    def test_gilberta_cast_in_background_does_not_move_an_unoccupied_main_character(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        task.current = 1
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_liino_stance_is_not_selected_as_a_replacement_for_gilberta(self):
        task, logic = _control_logic(["洁尔佩塔", "梨诺", "佩丽卡"])
        _accept_cast(logic, "2")
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, ["f3"])

    def test_liino_battle_and_ultimate_stances_move_control_away_without_pressing_end(self):
        for kind in ("battle", "ult"):
            with self.subTest(kind=kind):
                task, logic = _control_logic(["梨诺", "佩丽卡"])
                _accept_cast(logic, "1", kind)
                task.now = 3.0
                logic.step()
                self.assertEqual(task.keys, ["f2"])
                self.assertGreater(logic.state_until["1"], task.now)

    def test_stance_and_unknown_dead_or_temporarily_busy_members_are_excluded(self):
        task, logic = _control_logic(["洁尔佩塔", "梨诺", "佩丽卡", "?"])
        _accept_cast(logic, "2")
        _accept_cast(logic, "3")
        _accept_cast(logic, "1")
        task.now = 0.8
        logic.step()
        self.assertEqual(task.keys, [])
        task.now = 2.0
        logic.step()
        self.assertEqual(task.keys, ["f3"])
        task.current = 0
        logic.disabled_slots.add("3")
        task.now = 3.0
        logic.step()
        self.assertEqual(task.keys, ["f3"])

    def test_single_member_has_no_replacement_and_does_not_cancel_channel(self):
        task, logic = _control_logic(["洁尔佩塔"])
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_unknown_main_arrow_does_not_authorize_a_blind_switch(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        _accept_cast(logic, "1")
        task.current = None
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_background_state_can_expire_while_the_script_is_paused(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        _accept_cast(logic, "1")
        task.now = 10.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_switch_confirmation_suspends_attack_and_skill_keys_without_blocking_time(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        task.link = True
        task.now = 1.1
        logic._hold(True, force=True)
        logic.step()
        self.assertEqual(task.keys, ["f2"])
        self.assertEqual(task.mouse[-1], "up")
        self.assertEqual(task.now, 1.1)

    def test_unconfirmed_switch_retries_at_most_once_per_second(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        for now in (1.2, 1.5, 1.7, 1.9):
            task.now = now
            logic.step()
        self.assertEqual(task.keys, ["f2"])
        task.now = 2.2
        logic.step()
        self.assertEqual(task.keys, ["f2", "f2"])

    def test_zhuang_ultimate_switches_to_herself_before_the_free_first_battle(self):
        task, logic = _control_logic(["庄方宜", "佩丽卡"])
        task.current = 1
        task.ults = {"1"}
        logic.ult_order = ["1"]
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "f1"])
        self.assertEqual(logic.forced_battle_token, "1")
        task.current = 0
        task.now += 0.1
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "f1", "1"])

    def test_zhuang_already_controlled_is_not_switched_away_during_her_battle(self):
        task, logic = _control_logic(["庄方宜", "洁尔佩塔"])
        _accept_cast(logic, "1", "ult")
        _accept_cast(logic, "1")
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_yvonne_native_auto_switch_is_preserved_and_teammate_skill_can_continue(self):
        task, logic = _control_logic(["伊冯", "佩丽卡"])
        task.current = 1
        task.ults = {"1"}
        task.auto_switch_ults = {"1"}
        logic.ult_order = ["1"]
        logic.step()
        self.assertEqual(task.current, 0)
        self.assertEqual(task.keys, ["ult_1"])
        task.sp = 100.0
        logic.order = ["2"]
        task.now += 0.3
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "2"])
        self.assertLess(task.now, logic.forced_main_control_until)

    def test_yvonne_manual_switch_during_enhanced_attacks_is_corrected(self):
        task, logic = _control_logic(["伊冯", "佩丽卡"])
        _accept_cast(logic, "1", "ult")
        task.current = 1
        task.now = 3.0
        logic.step()
        self.assertEqual(task.keys, ["f1"])

    def test_yvonne_full_control_window_begins_after_ultimate_hud_recovery(self):
        task, logic = _control_logic(["伊冯", "佩丽卡"])
        task.ults = {"1"}
        task.auto_switch_ults = {"1"}
        logic.ult_order = ["1"]
        logic.step()
        self.assertEqual(logic.forced_main_control_until, task.now + 7.0)
        self.assertEqual(logic.main_control.preferred_until("1"), logic.forced_main_control_until)
        task.now = 8.0
        task.current = 1
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "f1"])

    def test_latest_enhanced_attack_window_wins_then_returns_to_still_active_owner(self):
        task, logic = _control_logic(["庄方宜", "伊冯", "佩丽卡"])
        _accept_cast(logic, "1", "ult")
        task.now = 2.0
        _accept_cast(logic, "2", "ult")
        task.current = 1
        logic.step()
        self.assertEqual(task.keys, [])
        task.now = 9.1
        logic.step()
        self.assertEqual(task.keys, ["f1"])

    def test_dead_enhanced_attack_owner_is_never_selected(self):
        task, logic = _control_logic(["庄方宜", "佩丽卡"])
        _accept_cast(logic, "1", "ult")
        task.current = 1
        logic.disabled_slots.add("1")
        task.now = 2.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_successful_cast_is_required_before_changing_main_control(self):
        task, logic = _control_logic(["洁尔佩塔", "佩丽卡"])
        task.sp = 100.0
        self.assertTrue(logic._try_battle_token("1", 100.0))
        task.now = 0.8
        task.sp = 100.0
        logic.step()
        self.assertEqual(task.keys, ["1"])
        task.now = 1.3
        logic.step()
        self.assertEqual(task.keys, ["1"])

    def test_reentering_combat_clears_old_control_windows(self):
        task, logic = _control_logic(["庄方宜", "佩丽卡"])
        _accept_cast(logic, "1", "ult")
        task.current = 1
        logic.run(deadline=task.now)
        task.now = 1.0
        logic.step()
        self.assertEqual(task.keys, [])

    def test_direct_switch_uses_original_member_number_with_missing_skill_hud(self):
        task = _ControlTask()
        task._battle_team = ["佩丽卡", "?", "庄方宜"]
        task._battle_member_count = 0
        task._battle_team_disabled_slots = {1}
        self.assertTrue(task.switch_main_control(3))
        self.assertFalse(task.switch_main_control(2))
        self.assertFalse(task.switch_main_control(4))
        self.assertEqual(task.keys, ["f3"])

    def test_no_control_policy_adds_no_arrow_scans_to_normal_teams(self):
        task, logic = _control_logic(["佩丽卡", "狼卫"])
        for _ in range(4):
            logic.step()
        self.assertEqual(task.arrow_reads, 0)


if __name__ == "__main__":
    unittest.main()
