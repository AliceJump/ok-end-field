"""Fixed-slot identities remain separate from queue existence/cast success."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.image.combo_skill_detector import (
    PORTRAITS,
    ComboSkillDetector,
    PortraitBank,
    combo_slot_boxes,
    load_combo_portraits,
)
from src.tasks.mixin.battle_mixin import BattleMixin


def render_heads(identities, *, width=1920, height=1080, dim=()):
    bank = load_combo_portraits()
    frame = np.full((height, width, 3), 40, np.uint8)
    scale = min(width / 1920, height / 1080)
    for index, (native_id, box) in enumerate(zip(identities, combo_slot_boxes(width, height), strict=False)):
        if native_id is None:
            continue
        size = round((83 if index == 0 else 61) * scale)
        portrait = cv2.resize(bank.portraits[native_id], (size, size))
        x, y, x2, y2 = box
        cx, cy = round((x + x2) / 2), round((y + y2) / 2)
        x, y = cx - size // 2, cy - size // 2
        background = frame[y:y + size, x:x + size]
        alpha = portrait[:, :, 3:4].astype(float) / 255
        if index in dim:
            alpha *= .7
        background[:] = np.round(portrait[:, :, :3] * alpha + background * (1 - alpha)).astype(np.uint8)
    return frame


TEAM = ("佩丽卡", "庄方宜", "艾维文娜", "祀")
HEADS = ("chr_0004_pelica", "chr_0030_zhuangfy", "chr_0012_avywen", "chr_0036_jsspsi")


class TestComboSkillDetector(unittest.TestCase):
    def test_atlas_covers_catalog_and_both_administrators(self):
        bank = load_combo_portraits()
        catalog = json.loads((PORTRAITS.parents[2] / "assets/data/characters.json").read_bytes())
        self.assertEqual(len(bank.portraits), 35)
        for key, row in catalog.items():
            with self.subTest(character=key):
                self.assertTrue(bank.candidates([key]))
                self.assertEqual(bank.candidates([key]), bank.candidates([row["zh"]]))
        self.assertEqual(set(bank.candidates(["管理员"])), {"chr_0002_endminm", "chr_0003_endminf"})

    def test_fourth_box_is_equal_pitch_and_center_anchored(self):
        boxes = combo_slot_boxes(1920, 1080)
        self.assertEqual(len(boxes), 4)
        self.assertEqual(boxes[3], (1370, 470, 1436, 536))
        self.assertEqual(boxes[3][0] - boxes[2][0], boxes[2][0] - boxes[1][0])
        self.assertEqual(combo_slot_boxes(2560, 1080)[0][0] - boxes[0][0], 320)
        self.assertEqual(combo_slot_boxes(3840, 2160)[3], tuple(v * 2 for v in boxes[3]))

    def test_four_independent_identities_without_circle_detector(self):
        detector = ComboSkillDetector()
        with patch.object(cv2, "HoughCircles", side_effect=AssertionError("No broad/ring search")):
            result = detector.observe(render_heads(HEADS), TEAM, ready=True, observed_at=1)
        self.assertEqual([r.native_id for r in result.slots], list(HEADS))
        self.assertEqual([r.team_slot for r in result.slots], [1, 2, 3, 4])
        self.assertEqual(result.head.character, "佩丽卡")
        self.assertEqual(result.status, "observed")

    def test_unknown_middle_does_not_shift_or_block_later_slots(self):
        heads = (HEADS[0], None, HEADS[2], HEADS[3])
        result = ComboSkillDetector().observe(render_heads(heads), TEAM, ready=True, observed_at=1)
        self.assertEqual([r.position for r in result.slots], [1, 2, 3, 4])
        self.assertEqual([r.native_id for r in result.slots], list(heads))
        self.assertIsNone(result.slots[1].character)
        self.assertEqual(result.slots[3].team_slot, 4)
        # Unknown head must not promote the next recognized portrait to head.
        result = ComboSkillDetector().observe(render_heads((None, *HEADS[1:])), TEAM, ready=True, observed_at=2)
        self.assertIsNone(result.head)
        self.assertEqual(result.slots[1].character, "庄方宜")

    def test_slots_are_not_team_slot_order_and_dim_does_not_mean_can_cast(self):
        result = ComboSkillDetector().observe(render_heads(HEADS[::-1], dim=(1,)), TEAM, ready=True, observed_at=1)
        self.assertEqual([r.team_slot for r in result.slots], [4, 3, 2, 1])
        self.assertEqual(result.slots[1].character, "艾维文娜")
        self.assertFalse(hasattr(result.slots[1], "can_cast"))

    def test_cached_pixels_reuse_but_same_count_replacement_invalidates(self):
        detector = ComboSkillDetector()
        frame = render_heads(HEADS)
        with patch.object(detector, "_match_slot", wraps=detector._match_slot) as match:
            first = detector.observe(frame, TEAM, ready=True, observed_at=1)
            calls = match.call_count
            second = detector.observe(frame.copy(), TEAM, ready=True, observed_at=2)
            self.assertEqual(match.call_count, calls)
            self.assertEqual(second.slots, first.slots)
            self.assertEqual(second.observed_at, 2)
            changed = detector.observe(render_heads((HEADS[3], *HEADS[1:])), TEAM, ready=True, observed_at=3)
            self.assertGreater(match.call_count, calls)
            self.assertEqual(changed.head.character, "祀")

    def test_hidden_unknown_and_explicit_reset_discard_old_identities(self):
        detector = ComboSkillDetector()
        frame = render_heads(HEADS)
        for ready in (False, None):
            detector.observe(frame, TEAM, ready=True, observed_at=1)
            hidden = detector.observe(frame, TEAM, ready=ready, observed_at=2)
            self.assertIsNone(hidden.head)
            self.assertTrue(all(r.character is None for r in hidden.slots))
            self.assertFalse(detector._slots)
        detector.observe(frame, TEAM, ready=True, observed_at=3)
        detector.reset()
        self.assertFalse(detector._slots)

    def test_team_change_and_missing_team_do_not_reuse_or_search_all_35(self):
        detector = ComboSkillDetector()
        frame = render_heads(HEADS)
        detector.observe(frame, TEAM, ready=True, observed_at=1)
        result = detector.observe(frame, ["狼卫"], ready=True, observed_at=2)
        self.assertTrue(all(r.character is None for r in result.slots))
        with patch.object(detector, "_match_slot", side_effect=AssertionError("No unknown-team full scan")):
            self.assertEqual(detector.observe(frame, ["?"], ready=True, observed_at=3).status, "unknown_team")

    def test_gender_is_read_from_portrait_not_assigned_from_team(self):
        for native in ("chr_0002_endminm", "chr_0003_endminf"):
            result = ComboSkillDetector().observe(render_heads((native,)), ["管理员"], ready=True, observed_at=1)
            self.assertEqual(result.head.native_id, native)
            self.assertEqual(result.head.character, "管理员")

    def test_duplicate_actor_aliases_do_not_invent_a_unique_team_slot(self):
        bank = load_combo_portraits()
        choices = bank.candidates(["管理员", "chr_0002_endminm"])
        self.assertTrue(all(team_slot is None for _name, team_slot in choices.values()))

    def test_resolution_change_rebuilds_cache_and_supports_bgra(self):
        detector = ComboSkillDetector()
        detector.observe(render_heads(HEADS), TEAM, ready=True, observed_at=1)
        for width, height in ((1440, 810), (2400, 1350)):
            frame = cv2.cvtColor(render_heads(HEADS, width=width, height=height), cv2.COLOR_BGR2BGRA)
            result = detector.observe(frame, TEAM, ready=True, observed_at=2)
            self.assertEqual([r.native_id for r in result.slots], list(HEADS))
            self.assertEqual(result.frame_size, (width, height))

    def test_invalid_frame_and_blank_are_unknown_not_empty_queue(self):
        for frame in (None, np.zeros((10, 10), np.uint8), np.zeros((1080, 1920, 3), np.float32)):
            self.assertEqual(ComboSkillDetector().observe(frame, TEAM, ready=True, observed_at=1).status, "unknown")
        result = ComboSkillDetector().observe(np.full((1080, 1920, 3), 100, np.uint8), TEAM, ready=True, observed_at=2)
        self.assertIsNone(result.head)
        self.assertEqual(len(result.slots), 4)
        with self.assertRaises(ValueError):
            ComboSkillDetector().observe(None, TEAM, ready=True, observed_at=float("nan"))

    def test_hidden_hint_does_not_load_assets_and_missing_assets_fail_soft(self):
        with patch("src.image.combo_skill_detector.load_combo_portraits", side_effect=OSError("missing")) as load:
            detector = ComboSkillDetector()
            self.assertEqual(detector.observe(None, TEAM, ready=False, observed_at=1).status, "not_ready")
            load.assert_not_called()
            self.assertEqual(detector.observe(render_heads(HEADS), ["?"], ready=True, observed_at=1).status, "unknown_team")
            load.assert_not_called()
            result = detector.observe(render_heads(HEADS), TEAM, ready=True, observed_at=2)
            self.assertEqual(result.status, "portraits_unavailable")

    def test_atlas_hash_and_tile_bounds_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            manifest = json.loads((PORTRAITS / "index.json").read_bytes())
            raw = (PORTRAITS / "portraits.png").read_bytes()
            (path / "portraits.png").write_bytes(raw + b" ")
            (path / "index.json").write_text(json.dumps(manifest), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                PortraitBank(path)
            manifest["atlas_sha256"] = hashlib.sha256(raw).hexdigest()
            manifest["portraits"][0]["tile"][0] = -1
            (path / "portraits.png").write_bytes(raw)
            (path / "index.json").write_text(json.dumps(manifest), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "tile"):
                PortraitBank(path)


class _Task:
    observe_link_skills = BattleMixin.observe_link_skills
    use_link_skill = BattleMixin.use_link_skill

    def __init__(self):
        self._battle_team = TEAM
        self.frame = render_heads(HEADS)
        self.ready = True
        self.keys = []

    def is_link_skill_ready(self):
        return self.ready

    def press_combat_key(self, key):
        self.keys.append(key)


class TestComboSkillAdapter(unittest.TestCase):
    def test_explicit_observation_is_read_only_and_e_keeps_original_input(self):
        task = _Task()
        observation = task.observe_link_skills()
        self.assertFalse(task.keys)
        self.assertEqual(observation.head.character, "佩丽卡")
        self.assertTrue(task.use_link_skill())
        self.assertEqual(task.keys, ["e"])
        self.assertEqual(task.link_skill_observation.slots[3].character, "祀")
        self.assertFalse(task._combo_skill_detector._slots)

    def test_unknown_identity_does_not_block_original_e_or_invent_actor(self):
        task = _Task()
        task._battle_team = ["?"]
        self.assertTrue(task.use_link_skill())
        self.assertEqual(task.keys, ["e"])
        self.assertIsNone(task.link_skill_observation.head)
        task.ready = False
        self.assertFalse(task.use_link_skill())
        self.assertEqual(task.keys, ["e"])
        self.assertFalse(task.link_skill_observation.ready)
        self.assertIsNone(task.link_skill_observation.head)

    def test_failed_input_also_invalidates_cached_queue(self):
        task = _Task()
        with patch.object(task, "press_combat_key", side_effect=RuntimeError("input failed")):
            with self.assertRaisesRegex(RuntimeError, "input failed"):
                task.use_link_skill()
        self.assertFalse(task._combo_skill_detector._slots)
