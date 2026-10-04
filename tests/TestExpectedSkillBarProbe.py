import unittest
from unittest.mock import patch

import numpy as np

from src.image.skill_bar_expected_probe import (
    SkillBarProbe,
    SkillBarState,
    classify_skill_bar_roi,
    read_expected_skill_bar_sp,
    resolve_expected_skill_bar_sp,
)


class TestExpectedSkillBarProbe(unittest.TestCase):
    def test_nearly_full_white_stays_partial(self):
        bar = np.zeros((8, 100, 3), dtype=np.uint8)
        bar[:, :99] = (240, 240, 240)

        probe = classify_skill_bar_roi(bar)

        self.assertEqual(probe.state, SkillBarState.PARTIAL)
        self.assertGreater(probe.ratio, 0.98)
        self.assertLess(probe.ratio, 1.0)

    def test_yellow_full_is_distinct_from_empty(self):
        full = np.zeros((8, 100, 3), dtype=np.uint8)
        full[:] = (30, 210, 250)
        empty = np.zeros((8, 100, 3), dtype=np.uint8)

        self.assertEqual(classify_skill_bar_roi(full).state, SkillBarState.FULL)
        self.assertEqual(classify_skill_bar_roi(empty).state, SkillBarState.EMPTY)

    def test_expected_hit_reads_one_slot(self):
        calls = []

        def probe_slot(index):
            calls.append(index)
            return SkillBarProbe(SkillBarState.PARTIAL, 0.35)

        self.assertEqual(resolve_expected_skill_bar_sp(235, probe_slot), 235.0)
        self.assertEqual(calls, [2])

    def test_full_moves_right_and_empty_moves_left(self):
        calls = []
        mapping = {
            1: SkillBarProbe(SkillBarState.FULL, 1.0),
            2: SkillBarProbe(SkillBarState.PARTIAL, 0.25),
        }

        result = resolve_expected_skill_bar_sp(135, lambda index: calls.append(index) or mapping[index])

        self.assertEqual(result, 225.0)
        self.assertEqual(calls, [1, 2])

        calls.clear()
        mapping = {
            2: SkillBarProbe(SkillBarState.EMPTY),
            1: SkillBarProbe(SkillBarState.PARTIAL, 0.80),
        }
        result = resolve_expected_skill_bar_sp(235, lambda index: calls.append(index) or mapping[index])
        self.assertEqual(result, 180.0)
        self.assertEqual(calls, [2, 1])

    def test_boundary_does_not_bounce_between_slots(self):
        calls = []
        mapping = {
            1: SkillBarProbe(SkillBarState.FULL, 1.0),
            2: SkillBarProbe(SkillBarState.EMPTY),
        }

        result = resolve_expected_skill_bar_sp(135, lambda index: calls.append(index) or mapping[index])

        self.assertEqual(result, 200.0)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(calls), len(set(calls)))

    def test_unknown_fallback_never_rechecks_a_slot(self):
        calls = []
        mapping = {
            1: SkillBarProbe(SkillBarState.UNKNOWN),
            2: SkillBarProbe(SkillBarState.EMPTY),
            0: SkillBarProbe(SkillBarState.PARTIAL, 0.42),
        }

        result = resolve_expected_skill_bar_sp(150, lambda index: calls.append(index) or mapping[index])

        self.assertEqual(result, 42.0)
        self.assertEqual(calls, [1, 2, 0])
        self.assertEqual(len(calls), len(set(calls)))

    def test_same_frame_is_reused_for_left_right_search(self):
        class Task:
            def __init__(self):
                self.frame = np.zeros((10, 10, 3), dtype=np.uint8)

            def get_skill_bar_sp(self):
                return -1.0

        task = Task()
        seen_frames = []
        probes = {
            2: SkillBarProbe(SkillBarState.EMPTY),
            1: SkillBarProbe(SkillBarState.PARTIAL, 0.75),
        }

        def fake_probe(_task, frame, index):
            seen_frames.append(frame)
            return probes[index]

        with patch("src.image.skill_bar_expected_probe.probe_skill_bar_slot", side_effect=fake_probe):
            result = read_expected_skill_bar_sp(task, 230)

        self.assertEqual(result, 175.0)
        self.assertEqual(len(seen_frames), 2)
        self.assertIs(seen_frames[0], seen_frames[1])

    def test_missing_frame_uses_legacy_reader(self):
        class Task:
            def __init__(self):
                self.reads = 0

            def get_skill_bar_sp(self):
                self.reads += 1
                return 175.0

        task = Task()

        self.assertEqual(read_expected_skill_bar_sp(task, 180), 175.0)
        self.assertEqual(task.reads, 1)


if __name__ == "__main__":
    unittest.main()
