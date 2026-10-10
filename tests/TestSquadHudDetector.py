import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.image.squad_hud_detector import (
    SquadHudObservation,
    SquadHudState,
    SquadSlotObservation,
    observe_squad_hud,
)
from src.tasks.mixin.battle_mixin import BattleMixin

FIXTURES = Path(__file__).parent / "fixtures/squad_hud"


def fixture_frame(name, height=1080):
    crop = cv2.imread(str(FIXTURES / f"{name}.png"))
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[900:1007, :520] = crop
    return cv2.resize(frame, (round(1920 * height / 1080), height))


class TestSquadHudDetector(unittest.TestCase):
    def test_two_person_health_and_dead_placeholder_occupancy(self):
        alive = observe_squad_hud(fixture_frame("two_alive"), read_health=True)
        self.assertEqual(alive.count_candidate, 2)
        self.assertAlmostEqual(alive.slots[0].hp_fraction, 0.44, delta=0.02)
        self.assertAlmostEqual(alive.slots[1].hp_fraction, 0.92, delta=0.02)
        dead = observe_squad_hud(fixture_frame("two_dead"), read_health=True)
        self.assertEqual(dead.count_candidate, 2)
        self.assertEqual([slot.death_visible for slot in dead.slots], [True, True, False, False])
        self.assertEqual([slot.hp_fraction for slot in dead.slots[:2]], [0, 0])

    def test_death_skips_health_reader_even_with_remaining_blue_vfx(self):
        with patch("src.image.squad_hud_detector._read_hp_fraction", side_effect=AssertionError("read dead HP")):
            observed = observe_squad_hud(fixture_frame("two_dead"), read_health=True)
        self.assertEqual(observed.slots[1].hp_fraction, 0)

    def test_dead_and_normal_slots_keep_physical_positions_across_resolutions(self):
        for height in (1080, 1440, 2160):
            with self.subTest(height=height):
                observed = observe_squad_hud(fixture_frame("four_two_markers", height), read_health=True)
                self.assertEqual(observed.count_candidate, 4)
                self.assertEqual([slot.death_visible for slot in observed.slots], [True, True, False, False])
                self.assertAlmostEqual(observed.slots[2].hp_fraction, 0.70, delta=0.025)

    def test_three_person_and_original_4k_crops(self):
        self.assertEqual(observe_squad_hud(fixture_frame("three_alive")).count_candidate, 3)
        self.assertEqual(observe_squad_hud(fixture_frame("four_4k")).count_candidate, 4)

    def test_unreadable_health_is_unknown_and_not_zero(self):
        observed = observe_squad_hud(fixture_frame("four_faded"), read_health=True)
        self.assertTrue(observed.slots[0].death_visible)
        self.assertTrue(all(slot.hp_fraction is None for slot in observed.slots[1:]))

    def test_invalid_frame_and_black_hud_are_distinct(self):
        self.assertIsNone(observe_squad_hud(None))
        self.assertIsNone(observe_squad_hud(np.zeros((20, 30, 3), np.uint8)))
        self.assertFalse(observe_squad_hud(np.zeros((1080, 1920, 3), np.uint8)).present)

    def test_interior_unknown_never_compacts_slots(self):
        occupied = SquadSlotObservation(True, False, 0, 0.5)
        unknown = SquadSlotObservation(False, False, 0)
        self.assertIsNone(SquadHudObservation((occupied, unknown, occupied, unknown)).count_candidate)

    def test_same_frame_cannot_confirm_death_and_recovery_preserves_count(self):
        state = SquadHudState()
        death_frame = fixture_frame("four_two_markers")
        observed = observe_squad_hud(death_frame, read_health=True)
        state.update(observed, 0, death_frame)
        state.update(observed, 0.1, death_frame)
        self.assertEqual(state.dead_slots, set())
        state.update(observed, 0.2, death_frame.copy())
        self.assertEqual(state.dead_slots, {0, 1})
        self.assertEqual(state.member_count, 4)
        alive_frame = fixture_frame("two_alive")
        alive = observe_squad_hud(alive_frame, read_health=True)
        state.update(alive, 0.4, alive_frame)
        self.assertEqual(state.dead_slots, {0, 1})
        state.update(alive, 0.6, alive_frame.copy())
        self.assertEqual(state.dead_slots, set())
        self.assertEqual(state.member_count, 4)

    def test_unknown_frames_do_not_kill_or_resurrect_slots(self):
        state = SquadHudState(member_count=4, dead_slots={0})
        for index in range(12):
            frame = np.zeros((1080, 1920, 3), np.uint8)
            state.update(observe_squad_hud(frame), index * 0.1, frame)
        self.assertEqual(state.dead_slots, {0})
        self.assertEqual(state.member_count, 4)

    def test_cheap_probe_before_identity_scan_can_confirm_recovery(self):
        state = SquadHudState(member_count=2, dead_slots={0, 1})
        for now in (0, 0.1):
            frame = fixture_frame("two_alive")
            state.update(observe_squad_hud(frame), now, frame, confirm_count=False)
            state.update(observe_squad_hud(frame, read_health=True), now, frame, ("A", "B"))
        self.assertEqual(state.dead_slots, set())

    def test_expired_or_interrupted_death_candidate_needs_two_new_frames(self):
        state = SquadHudState()
        frame = fixture_frame("two_dead")
        state.update(observe_squad_hud(frame), 0, frame)
        state.update(None, 0.2, None)
        state.update(observe_squad_hud(frame), 0.4, frame.copy())
        self.assertEqual(state.dead_slots, set())
        state.update(observe_squad_hud(frame), 4, frame.copy())
        self.assertEqual(state.dead_slots, set())
        state.update(observe_squad_hud(frame), 4.1, frame.copy())
        self.assertEqual(state.dead_slots, {0, 1})

    def test_one_person_count_from_real_slot_with_other_slots_removed(self):
        frame = fixture_frame("two_alive")
        frame[:, 145:] = 0  # Constructed one-slot fixture, not a native one-person capture.
        observed = observe_squad_hud(frame)
        self.assertEqual(observed.count_candidate, 1)


class _ExitHarness:
    _check_single_exit_condition = BattleMixin._check_single_exit_condition
    is_combat_ended = BattleMixin.is_combat_ended
    ULT_EXIT_DELAY = 3.0

    def __init__(self):
        self.now = 10.0
        self.lv = False
        self.present = True
        self.exit_check_count = 0
        self._battle_member_count = 4

    def active_time(self):
        return self.now

    def find_feature(self, **kwargs):
        return False

    def ocr_lv(self):
        return self.lv

    def has_team_hud(self):
        return self.present

    def in_team(self):
        raise AssertionError("Exit must never scan right skill keys")

    def log_info(self, *args):
        pass

    def log_debug(self, *args):
        pass


class TestSquadHudExit(unittest.TestCase):
    def test_lv_still_exits_with_visible_dead_or_alive_slots(self):
        task = _ExitHarness()
        task.lv = True
        self.assertFalse(task.is_combat_ended())
        self.assertTrue(task.is_combat_ended())
        self.assertEqual(task._battle_member_count, 0)

    def test_transient_absence_invalid_frames_and_reappearance_reset_exit(self):
        task = _ExitHarness()
        task.present = False
        self.assertFalse(task.is_combat_ended())
        task.now += 0.5
        self.assertFalse(task.is_combat_ended())
        task.present = None
        task.now += 0.5
        self.assertFalse(task.is_combat_ended())
        task.present = True
        self.assertFalse(task.is_combat_ended())
        task.present = False
        task.now += 1
        self.assertFalse(task.is_combat_ended())
        task.now += 1
        self.assertFalse(task.is_combat_ended())
        task.now += 0.5
        self.assertTrue(task.is_combat_ended())

    def test_ultimate_protection_preserved(self):
        task = _ExitHarness()
        task._last_ult_release_time = 9.5
        task.lv = True
        self.assertFalse(task.is_combat_ended())

    def test_all_dead_hud_does_not_exit_when_lv_absent(self):
        task = _ExitHarness()
        task._squad_dead_slots = {0, 1, 2, 3}
        for _ in range(4):
            task.now += 0.5
            self.assertFalse(task.is_combat_ended())


class _LeftEntryHarness:
    in_team = BattleMixin.in_team
    observe_team_hud = BattleMixin.observe_team_hud
    _update_squad_hud = BattleMixin._update_squad_hud

    def __init__(self, frame):
        self.frame = frame
        self.now = 0
        self._battle_member_count = 0

    def active_time(self):
        return self.now

    def detect_team(self):
        # Identity is intentionally unknown: death/count must still work.
        hud = observe_squad_hud(self.frame, read_health=True)
        self._last_squad_observation = hud
        self._update_squad_hud(hud, self.frame, read_health=True, confirm_count=True)
        return ["?"] * 4

    def _battle_feature_boxes(self, prefix):
        return []

    def log_debug(self, *args):
        pass


class TestSquadHudEntry(unittest.TestCase):
    def test_count_confirms_from_left_without_right_keys_or_known_identity(self):
        cases = [("two_alive", 2), ("two_dead", 2), ("three_alive", 3), ("four_two_markers", 4)]
        for name, count in cases:
            with self.subTest(name=name):
                task = _LeftEntryHarness(fixture_frame(name))
                self.assertFalse(task.in_team())
                task.now = 0.1
                task.frame = task.frame.copy()
                self.assertTrue(task.in_team())
                self.assertEqual(task._battle_member_count, count)

    def test_missing_hud_keeps_established_right_mapping(self):
        task = _LeftEntryHarness(fixture_frame("four_one_dead"))
        task._battle_team = ["A", "B", "C", "D"]
        self.assertTrue(task.in_team())
        self.assertEqual(task._battle_member_count, 4)
        task.frame = np.zeros_like(task.frame)
        task.now = 0.1
        self.assertFalse(task.in_team())
        self.assertEqual(task._battle_member_count, 4)
