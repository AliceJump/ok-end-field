import json
import unittest
from pathlib import Path

import cv2
import numpy as np
from ok import Box

from src.image.team_keycap_detector import detect_team_keycaps
from src.tasks.mixin.battle_mixin import BattleMixin
from tests.TestCombatPresenceLifecycle import _TeamHudTask

FIXTURES = Path(__file__).parent / "fixtures/team_keycaps"
SOURCES = json.loads((FIXTURES / "sources.json").read_text(encoding="utf-8"))


def load_frame(name):
    info = next(source for source in SOURCES if source["file"] == f"{name}.png")
    strip = cv2.imread(str(FIXTURES / info["file"]))
    frame = np.zeros((info["height"], info["width"], 3), dtype=np.uint8)
    frame[-strip.shape[0] :, -strip.shape[1] :] = strip
    return frame


class _ImageHudTask:
    def __init__(self, frame, team_count=0, disabled_slots=()):
        self.frame = frame
        self._battle_team = ["known"] * team_count
        self._battle_team_disabled_slots = set(disabled_slots)
        self._battle_member_count = team_count
        self.template_calls = []

    def _battle_feature_boxes(self, prefix):
        return [Box(index * 100, 10, 20, 20) for index in range(4)]

    def find_one(self, feature, box):
        self.template_calls.append(feature)
        return None

    def log_debug(self, message):
        pass


class TestTeamKeycapDetector(unittest.TestCase):
    def test_real_hud_and_menu_crops(self):
        for info in SOURCES:
            with self.subTest(source=info["source"]):
                frame = load_frame(Path(info["file"]).stem)
                mask = "".join("1" if slot else "0" for slot in detect_team_keycaps(frame))
                self.assertEqual(mask, info["mask"])

    def test_relative_text_recovers_delivery_fourth_key_when_dimmed(self):
        # Synthetic brightness changes, not captures of the game's disabled state.
        for gain, offset in ((1, 0), (0.546, 0), (0.3, 0), (0.546, 40)):
            with self.subTest(gain=gain, offset=offset):
                frame = np.clip(load_frame("delivery").astype(np.float32) * gain + offset, 0, 255).astype(np.uint8)
                self.assertEqual(detect_team_keycaps(frame), (True,) * 4)
                task = _ImageHudTask(frame)
                self.assertTrue(BattleMixin.in_team(task))
                self.assertEqual(task._battle_member_count, 4)
                self.assertEqual(task.template_calls, [])

    def test_bottom_right_anchors_survive_ultrawide_padding_and_uniform_resize(self):
        frame = load_frame("delivery")
        wide = np.pad(frame, ((0, 0), (640, 0), (0, 0)))
        self.assertEqual(detect_team_keycaps(wide), (True,) * 4)
        for width, height in ((2560, 1440), (3840, 2160)):
            with self.subTest(width=width):
                resized = cv2.resize(frame, (width, height))
                self.assertEqual(detect_team_keycaps(resized), (True,) * 4)

    def test_known_three_person_team_uses_right_aligned_keycaps(self):
        task = _ImageHudTask(load_frame("native_three"), team_count=3)
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 3)
        self.assertEqual(task.template_calls, [])

    def test_unknown_partial_layout_uses_digits_to_establish_numbering(self):
        task = _TeamHudTask(3, visible_slots=(1, 2, 3))
        task._battle_team = []
        task.frame = load_frame("native_three")
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 3)

    def test_missing_first_or_disabled_slot_keeps_known_four_person_mapping(self):
        for disabled in ((), (0,)):
            with self.subTest(disabled=disabled):
                frame = load_frame("effects")
                frame[:, :1600] = 0
                task = _ImageHudTask(frame, team_count=4, disabled_slots=disabled)
                self.assertTrue(BattleMixin.in_team(task))
                self.assertEqual(task._battle_member_count, 4)

    def test_known_two_person_team_can_use_two_outlines(self):
        frame = load_frame("effects")
        frame[:, :1700] = 0
        task = _ImageHudTask(frame, team_count=2)
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 2)
        self.assertEqual(task.template_calls, [])

    def test_extra_left_keycap_rechecks_stale_short_team_size(self):
        task = _TeamHudTask(4, visible_slots=(1, 2, 3, 4))
        task._battle_team = ["known"] * 3
        task.frame = load_frame("effects")
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 4)

    def test_last_survivor_and_single_outline_need_digit_confirmation(self):
        task = _TeamHudTask(4, visible_slots=(4,), disabled_slots=(0, 1, 2))
        task.frame = load_frame("delivery")
        self.assertTrue(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 4)
        task = _ImageHudTask(load_frame("single_outline"), team_count=4)
        self.assertFalse(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 0)
        self.assertTrue(task.template_calls)

    def test_menu_and_hidden_hud_clear_count_even_with_known_team(self):
        for name in [f"menu_{index}" for index in range(1, 9)]:
            with self.subTest(name=name):
                task = _ImageHudTask(load_frame(name), team_count=4)
                self.assertFalse(BattleMixin.in_team(task))
                self.assertEqual(task._battle_member_count, 0)
        task = _ImageHudTask(np.zeros((1080, 1920, 3), dtype=np.uint8), team_count=4)
        self.assertFalse(BattleMixin.in_team(task))
        self.assertEqual(task._battle_member_count, 0)

    def test_invalid_or_empty_frames(self):
        for frame in (
            None,
            np.empty((0, 0, 3), np.uint8),
            np.zeros((42, 44), np.uint8),
            np.zeros((42, 44, 4), np.uint8),
            np.zeros((42, 44, 3), np.float32),
        ):
            with self.subTest(shape=getattr(frame, "shape", None)):
                self.assertEqual(detect_team_keycaps(frame), (False,) * 4)


if __name__ == "__main__":
    unittest.main()
