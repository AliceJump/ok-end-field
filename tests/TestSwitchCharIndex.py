"""切人图标（main_char 箭头）搜索框组与当前角色判定的单元测试。"""

import unittest
from itertools import pairwise

from ok import Box

from src.tasks.mixin.battle_mixin import (
    SWITCH_CHAR_BASE_X,
    SWITCH_CHAR_EXPAND,
    SWITCH_CHAR_H,
    SWITCH_CHAR_SLOTS,
    SWITCH_CHAR_W,
    SWITCH_CHAR_X_PITCH,
    SWITCH_CHAR_Y,
    BattleMixin,
)

BASE_W, BASE_H = 1920, 1080


class _SwitchCharHarness:
    """最小化桩：模拟归一化搜索框生成与各槽位的模板命中。"""

    def __init__(self, slot_scores=None, frame_size=(BASE_W, BASE_H)):
        self.slot_scores = slot_scores or {}
        self.width, self.height = frame_size
        self.searched_features = []

    def box_of_screen(self, x, y, to_x=1.0, to_y=1.0, name=None, **kwargs):
        return Box(
            round(x * self.width),
            round(y * self.height),
            round((to_x - x) * self.width),
            round((to_y - y) * self.height),
            name=name,
        )

    def find_one(self, feature, box=None, frame=None, threshold=None):
        self.searched_features.append((feature, box.name if box else None))
        score = self.slot_scores.get(box.name)
        if score is None:
            return None
        return Box(box.x, box.y, box.width, box.height, name=box.name, confidence=score)

    def log_debug(self, message):
        pass

    # 挂上被测方法，使 harness 满足 detect_current_char_index 的调用面
    get_switch_char_boxes = BattleMixin.get_switch_char_boxes


class TestSwitchCharBoxes(unittest.TestCase):
    def test_box_group_geometry_at_base_resolution(self):
        """基准 1920x1080 下：4 个框、外扩 1/8、公差 0.0608。"""
        task = _SwitchCharHarness()
        boxes = BattleMixin.get_switch_char_boxes(task)

        self.assertEqual(len(boxes), SWITCH_CHAR_SLOTS)
        dx = SWITCH_CHAR_W * SWITCH_CHAR_EXPAND
        dy = SWITCH_CHAR_H * SWITCH_CHAR_EXPAND
        for i, box in enumerate(boxes):
            nx = SWITCH_CHAR_BASE_X + i * SWITCH_CHAR_X_PITCH
            self.assertEqual(box.x, round((nx - dx) * BASE_W))
            self.assertEqual(box.y, round((SWITCH_CHAR_Y - dy) * BASE_H))
            self.assertEqual(box.width, round((SWITCH_CHAR_W + 2 * dx) * BASE_W))
            self.assertEqual(box.height, round((SWITCH_CHAR_H + 2 * dy) * BASE_H))
            self.assertEqual(box.name, f"switch_char_slot_{i}")
        # 槽位中心间距 = 公差 * 基准宽 ≈ 116.7px
        centers = [b.x + b.width / 2 for b in boxes]
        pitch = (centers[-1] - centers[0]) / (SWITCH_CHAR_SLOTS - 1)
        self.assertAlmostEqual(pitch, SWITCH_CHAR_X_PITCH * BASE_W, delta=1.0)

    def test_boxes_do_not_overlap(self):
        task = _SwitchCharHarness()
        boxes = BattleMixin.get_switch_char_boxes(task)
        for prev, cur in pairwise(boxes):
            self.assertLess(prev.x + prev.width, cur.x)

    def test_box_covers_annotation_at_other_resolutions(self):
        """4K 下槽 0 框仍覆盖等比换算后的标注区域 (160,1796,16,32)。"""
        task = _SwitchCharHarness(frame_size=(3840, 2160))
        box = BattleMixin.get_switch_char_boxes(task)[0]
        ann = (160, 1796, 16, 32)
        self.assertLessEqual(box.x, ann[0])
        self.assertLessEqual(box.y, ann[1])
        self.assertGreaterEqual(box.x + box.width, ann[0] + ann[2])
        self.assertGreaterEqual(box.y + box.height, ann[1] + ann[3])


class TestDetectCurrentCharIndex(unittest.TestCase):
    def test_returns_none_when_no_slot_hits(self):
        task = _SwitchCharHarness()
        self.assertIsNone(BattleMixin.detect_current_char_index(task))
        features = {feature for feature, _ in task.searched_features}
        self.assertEqual(features, {"main_char"})
        self.assertEqual(len(task.searched_features), SWITCH_CHAR_SLOTS)

    def test_returns_slot_with_highest_confidence(self):
        task = _SwitchCharHarness(
            slot_scores={"switch_char_slot_0": 0.8, "switch_char_slot_2": 0.95}
        )
        self.assertEqual(BattleMixin.detect_current_char_index(task), 2)

    def test_returns_first_slot_index(self):
        task = _SwitchCharHarness(slot_scores={"switch_char_slot_0": 0.99})
        self.assertEqual(BattleMixin.detect_current_char_index(task), 0)


if __name__ == "__main__":
    unittest.main()
