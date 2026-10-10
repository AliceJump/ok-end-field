import json
import unittest
from pathlib import Path

import cv2
import numpy as np
from ok.feature.FeatureSet import FeatureSet

from src.tasks.mixin.battle_mixin import BattleMixin
from tests.TestSquadHudDetector import fixture_frame

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/team_portraits"
SOURCES = json.loads((FIXTURES / "sources.json").read_text(encoding="utf-8"))


class _PortraitTask:
    _collect_team_candidate_boxes = BattleMixin._collect_team_candidate_boxes
    _build_search_boxes = BattleMixin._build_search_boxes
    _union_find_cluster = staticmethod(BattleMixin._union_find_cluster)
    _match_team_slots = BattleMixin._match_team_slots
    _detect_team_core = BattleMixin._detect_team_core
    _update_squad_hud = BattleMixin._update_squad_hud
    BATTLE_ICON_GROUP_DISTANCE_THRESHOLD = BattleMixin.BATTLE_ICON_GROUP_DISTANCE_THRESHOLD

    def __init__(self, source):
        info = next(row for row in SOURCES if row["file"] == f"{source}.png")
        self.frame = np.zeros((info["height"], info["width"], 3), dtype=np.uint8)
        x, y, width, height = info["crop"]
        self.frame[y : y + height, x : x + width] = cv2.imread(str(FIXTURES / info["file"]))
        self.features = FeatureSet(
            False, str(ROOT / "assets/coco_annotations.json"), 0.002, 0.002, default_threshold=0.8
        )
        self.searched_slots = set()
        self.now = 0.0

    def active_time(self):
        return self.now

    def get_box_by_name(self, name):
        return self.features.get_box_by_name(self.frame, name)

    def find_one(self, name, box, frame=None):
        self.searched_slots.add(box.x)
        hits = self.features.find_one_feature(
            self.frame if frame is None else frame, name, box=box, threshold=0.8, limit=1
        )
        return hits[0] if hits else None


class TestTeamPortraitLayout(unittest.TestCase):
    def test_original_4k_portraits_with_hidden_medicine_keep_their_slots(self):
        task = _PortraitTask("shifted_4k")
        self.assertEqual(BattleMixin.detect_team(task), ["弧光", "佩丽卡", "陈千语", "艾尔黛拉"])
        self.assertTrue(all(score > 0.9 for _, score, _ in task._detect_team_core()))

    def test_original_2k_normal_layout_still_matches_all_four_characters(self):
        task = _PortraitTask("normal_2k")
        self.assertEqual(BattleMixin.detect_team(task), ["庄方宜", "佩丽卡", "艾维文娜", "梨诺"])

    def test_single_slot_probe_accepts_shifted_portrait_without_scanning_others(self):
        task = _PortraitTask("shifted_4k")
        self.assertEqual(BattleMixin.detect_team_slot(task, 2), "陈千语")
        self.assertEqual(task.searched_slots, {580})

    def test_two_person_layout_does_not_fill_empty_slots(self):
        task = _PortraitTask("native_two")
        self.assertEqual(BattleMixin.detect_team(task), ["艾尔黛拉", "余烬", "?", "?"])

    def test_single_slot_probe_does_not_confirm_a_partial_formation(self):
        task = _PortraitTask("shifted_4k")
        for now in (0.0, 0.1):
            task.now = now
            task.frame = task.frame.copy()
            self.assertEqual(BattleMixin.detect_team_slot(task, 2), "陈千语")
        self.assertEqual(task._squad_hud_state.member_count, 0)
        self.assertEqual(task.searched_slots, {580})
        # Full scans can still establish the original four-slot formation.
        for now in (0.2, 0.3):
            task.now = now
            task.frame = task.frame.copy()
            self.assertEqual(len([name for name in BattleMixin.detect_team(task) if name != "?"]), 4)
        self.assertEqual(task._squad_hud_state.member_count, 4)

    def test_dead_slot_is_skipped_without_moving_surviving_portrait_indices(self):
        task = _PortraitTask("native_two")
        task.frame = fixture_frame("four_one_dead")
        self.assertEqual(BattleMixin.detect_team_slot(task, 0), "?")
        self.assertEqual(task.searched_slots, set())
        self.assertEqual(BattleMixin.detect_team(task), ["?", "洁尔佩塔", "别礼", "艾尔黛拉"])
        self.assertEqual(task.searched_slots, {173, 290, 407})


if __name__ == "__main__":
    unittest.main()
