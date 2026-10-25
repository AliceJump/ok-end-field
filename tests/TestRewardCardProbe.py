import unittest

import cv2
import numpy as np

from src.image.reward_card_probe import (
    CARD_CLAIMABLE,
    CARD_CLAIMED,
    CARD_UNFINISHED,
    card_state,
    is_claimable_card,
    red_ratio_near_box,
)

# 实测底色：可领取为暗红，已领取转灰，任务未完成是近黑。
# 黑与灰的实测亮度中位数分别是 29~42 与 70（1440p 实录）。
RED_CARD_BGR = (48, 46, 192)
CLAIMED_CARD_BGR = (70, 70, 70)
UNFINISHED_CARD_BGR = (35, 35, 35)


class _Box:
    """最小化的 OCR 文本框替身，与 ok.Box 的属性一致。"""

    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height


def _frame(card_colors, card_height=140, gap=8, width=1280):
    """按给定底色自上而下画若干任务卡，返回整帧。"""
    height = gap + len(card_colors) * (card_height + gap)
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    for index, color in enumerate(card_colors):
        top = gap + index * (card_height + gap)
        frame[top : top + card_height, 130 : width - 40] = color
    return frame


class TestRewardCardProbe(unittest.TestCase):
    def test_claimable_card_accepts_red_and_rejects_claimed(self):
        frame = _frame([RED_CARD_BGR, CLAIMED_CARD_BGR])
        self.assertTrue(is_claimable_card(frame, _Box(160, 40, 400, 30)))
        self.assertFalse(is_claimable_card(frame, _Box(160, 40 + 148, 400, 30)))

    def test_unfinished_card_is_also_not_claimable(self):
        frame = _frame([UNFINISHED_CARD_BGR])
        self.assertFalse(is_claimable_card(frame, _Box(160, 40, 400, 30)))

    def test_card_state_separates_all_three_backgrounds(self):
        # 可领取红底、未完成黑底、已领取灰底必须各归各态
        frame = _frame([RED_CARD_BGR, UNFINISHED_CARD_BGR, CLAIMED_CARD_BGR])
        self.assertEqual(card_state(frame, _Box(160, 40, 400, 30)), CARD_CLAIMABLE)
        self.assertEqual(card_state(frame, _Box(160, 40 + 148, 400, 30)), CARD_UNFINISHED)
        self.assertEqual(card_state(frame, _Box(160, 40 + 296, 400, 30)), CARD_CLAIMED)

    def test_black_and_gray_split_at_measured_threshold(self):
        # 实测黑底 V=29~42、灰底 V=70；两侧各留余量后都能正确分流
        for brightness, expected in ((29, CARD_UNFINISHED), (42, CARD_UNFINISHED), (70, CARD_CLAIMED)):
            with self.subTest(v=brightness):
                frame = _frame([(brightness, brightness, brightness)])
                self.assertEqual(card_state(frame, _Box(160, 40, 400, 30)), expected)

    def test_invalid_region_is_treated_as_unfinished(self):
        # 取样区无效时按未完成处理：宁可继续挑战，也不能误判成「已完成」而跳过任务
        self.assertEqual(card_state(None, _Box(0, 0, 10, 10)), CARD_UNFINISHED)
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        self.assertEqual(card_state(empty, _Box(0, 0, 10, 10)), CARD_UNFINISHED)

    def test_red_ratio_is_between_the_states(self):
        frame = _frame([RED_CARD_BGR, CLAIMED_CARD_BGR, UNFINISHED_CARD_BGR])
        red = red_ratio_near_box(frame, _Box(160, 40, 400, 30))
        claimed = red_ratio_near_box(frame, _Box(160, 40 + 148, 400, 30))
        unfinished = red_ratio_near_box(frame, _Box(160, 40 + 296, 400, 30))
        # 取样区会越过卡片左右边缘落入黑底，因此不要求接近 1.0；
        # 关键是与不可领取的两态差距足以区分。
        self.assertGreater(red, 0.5)
        self.assertLess(claimed, 0.1)
        self.assertLess(unfinished, 0.1)
        self.assertGreater(red - max(claimed, unfinished), 0.5)

    def test_text_inside_card_does_not_break_detection(self):
        frame = _frame([RED_CARD_BGR])
        # 卡片上写字、画进度条、放奖励图标
        cv2.putText(frame, "TEST", (200, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (230, 230, 230), 2)
        cv2.rectangle(frame, (200, 110), (420, 122), (60, 200, 120), -1)
        cv2.circle(frame, (1100, 90), 26, (30, 120, 240), -1)
        self.assertTrue(is_claimable_card(frame, _Box(160, 40, 400, 30)))

    def test_detection_holds_across_rendered_resolutions(self):
        for scale in (0.75, 1.0, 1.5):
            with self.subTest(scale=scale):
                base = _frame([RED_CARD_BGR, CLAIMED_CARD_BGR], width=1280)
                frame = cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                box = _Box(160 * scale, 40 * scale, 400 * scale, 30 * scale)
                self.assertTrue(is_claimable_card(frame, box))
                claimed = _Box(160 * scale, (40 + 148) * scale, 400 * scale, 30 * scale)
                self.assertFalse(is_claimable_card(frame, claimed))

    def test_invalid_input_returns_false(self):
        self.assertFalse(is_claimable_card(None, _Box(0, 0, 10, 10)))
        self.assertEqual(red_ratio_near_box(np.zeros((0, 0, 3), dtype=np.uint8), _Box(0, 0, 10, 10)), 0.0)


if __name__ == "__main__":
    unittest.main()
