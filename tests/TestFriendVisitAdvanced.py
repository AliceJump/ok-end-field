import unittest
import unittest.mock

import numpy as np
from ok.feature.Box import Box

import src.tasks.onetime.FriendVisitAdvanced as friend_visit
from src.tasks.onetime.FriendVisitAdvanced import (
    LIST_COMPLETE_THRESHOLD,
    REMARK_PATTERN,
    FriendVisitAdvancedMixin,
)


class _AdvHarness(FriendVisitAdvancedMixin):
    """只验证纯逻辑，设备相关方法全部用最小替身。"""

    screen_width = 1920
    screen_height = 1080

    def __init__(self, frame=None, config=None):
        self.config = config or {}
        self._frame = frame
        self._visit_templates = {}
        self._list_snapshot = None
        self.logs = []
        self.errors = []

    @property
    def frame(self):
        return self._frame

    def adv_box(self, roi):
        return Box(0, 0, 640, 360)

    def log_debug(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_warning(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_error(self, message, *args, **kwargs):
        self.errors.append(message)
        self.logs.append(message)


def _row(text, key=None, remark=None):
    return {
        "text": text,
        "key": key if key is not None else FriendVisitAdvancedMixin.adv_row_key(text),
        "remark": bool(REMARK_PATTERN.search(text)) if remark is None else remark,
        "enter": Box(0, 0, 10, 10),
        "name_box": Box(0, 0, 100, 10),
    }


class _VisitOneHarness(FriendVisitAdvancedMixin):
    """adv_visit_one 的返回值与快速拜访额度。"""

    screen_width = 1920
    screen_height = 1080

    def __init__(self, quick_visits=0, enter_ok=True):
        self._quick_visits = quick_visits
        self._enter_ok = enter_ok
        self._list_snapshot = None
        self.quick_calls = 0
        self.leave_calls = 0
        self.logs = []

    def adv_enter_ship(self, row, targets):
        return self._enter_ok

    def adv_open_guest_terminal(self):
        return True

    def adv_terminal_actions(self, targets):
        pass

    def adv_exit_guest_terminal(self):
        pass

    def adv_quick_visit_next(self, targets):
        self.quick_calls += 1
        return self.quick_calls <= self._quick_visits

    def adv_leave_ship(self):
        self.leave_calls += 1

    def exit_is_set(self):
        return False

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_warning(self, message, *args, **kwargs):
        self.logs.append(message)


class _VisitLoopHarness(_AdvHarness):
    """advanced_visit 的计数与额度传递。"""

    CFG_MAX_VISITS = "max"

    def __init__(self, rows, visits_per_call, config=None):
        super().__init__(config=config or {})
        self._rows = rows
        self._visits = list(visits_per_call)
        self.limits = []

    def adv_collect_pending_credit(self):
        return True

    def adv_enter_friend_list(self):
        return True

    def exit_is_set(self):
        return False

    def adv_remaining_counts(self):
        return 5, 5

    def adv_scan_rows(self):
        return list(self._rows)

    def adv_list_at_bottom(self):
        return True

    def adv_scroll_list(self):
        pass

    def adv_reopen_friend_list(self):
        return True

    def adv_visit_one(self, row, targets, limit=0):
        self.limits.append(limit)
        return self._visits.pop(0) if self._visits else 0


class TestFriendVisitRowKey(unittest.TestCase):
    def test_plain_name_keeps_id(self):
        self.assertEqual(_AdvHarness.adv_row_key("旅行者#1234"), "旅行者#1234")

    def test_remark_after_id_is_dropped(self):
        self.assertEqual(_AdvHarness.adv_row_key("旅行者#1234（自动）"), "旅行者#1234")

    def test_remark_before_id_is_dropped(self):
        self.assertEqual(_AdvHarness.adv_row_key("（自动）旅行者#1234"), "旅行者#1234")

    def test_half_width_parentheses_are_dropped(self):
        self.assertEqual(_AdvHarness.adv_row_key("traveler#12(auto)"), "traveler#12")

    def test_same_person_with_and_without_remark_shares_key(self):
        with_remark = _AdvHarness.adv_row_key("旅行者#1234（自动）")
        without_remark = _AdvHarness.adv_row_key("旅行者#1234")
        self.assertEqual(with_remark, without_remark)

    def test_text_without_id_has_no_key(self):
        self.assertIsNone(_AdvHarness.adv_row_key("旅行者"))

    def test_nested_parentheses_keep_outer_name(self):
        self.assertEqual(_AdvHarness.adv_row_key("（a（b））旅行者#99"), "旅行者#99")


class TestFriendVisitPriority(unittest.TestCase):
    def test_priority_names_split_on_mixed_separators(self):
        harness = _AdvHarness(config={"prior": "甲, 乙、丙 丁;戊"})
        harness.CFG_PRIORITY = "prior"
        self.assertEqual(harness.adv_priority_names(), ["甲", "乙", "丙", "丁", "戊"])

    def test_empty_priority_list_disables_the_filter(self):
        self.assertIsNone(_AdvHarness.adv_priority_regex([]))

    def test_priority_regex_matches_any_name(self):
        pattern = _AdvHarness.adv_priority_regex(["甲", "乙"])
        self.assertTrue(pattern.search("乙#1"))
        self.assertFalse(pattern.search("丙#2"))

    def test_priority_names_are_escaped(self):
        pattern = _AdvHarness.adv_priority_regex(["a.b"])
        self.assertTrue(pattern.search("xa.by"))
        self.assertFalse(pattern.search("axb"))


class TestFriendVisitPickRow(unittest.TestCase):
    def test_visited_rows_are_skipped(self):
        rows = [_row("甲#1"), _row("乙#2")]
        picked = _AdvHarness.adv_pick_row(None, rows, {"甲#1"}, "normal", None)
        self.assertEqual(picked["key"], "乙#2")

    def test_remark_mode_requires_parentheses(self):
        rows = [_row("甲#1"), _row("乙#2（自动）")]
        picked = _AdvHarness.adv_pick_row(None, rows, set(), "remark", None)
        self.assertEqual(picked["key"], "乙#2")

    def test_priority_mode_requires_matching_name(self):
        rows = [_row("甲#1"), _row("乙#2")]
        pattern = _AdvHarness.adv_priority_regex(["乙"])
        picked = _AdvHarness.adv_pick_row(None, rows, set(), "priority", pattern)
        self.assertEqual(picked["key"], "乙#2")

    def test_no_candidate_returns_none(self):
        rows = [_row("甲#1")]
        self.assertIsNone(_AdvHarness.adv_pick_row(None, rows, {"甲#1"}, "normal", None))


class TestFriendVisitOffsetBox(unittest.TestCase):
    def test_offsets_scale_with_screen_width(self):
        harness = _AdvHarness()
        base = Box(300, 200, 30, 100)
        shifted = harness.adv_offset_box(base, (-1069, -29, 218, 5))
        self.assertEqual(shifted.x, 300 + round(-1069 * 1.5))
        self.assertEqual(shifted.y, 200 + round(-29 * 1.5))
        self.assertEqual(shifted.width, max(1, 30 + round(218 * 1.5)))
        self.assertEqual(shifted.height, max(1, 100 + round(5 * 1.5)))

    def test_scale_uses_base_1280(self):
        self.assertAlmostEqual(_AdvHarness().adv_scale(), 1.5)


class TestFriendVisitListBottom(unittest.TestCase):
    @staticmethod
    def _frame(seed):
        return np.random.default_rng(seed).integers(0, 255, (360, 640, 3), dtype=np.uint8)

    def test_first_observation_is_not_bottom(self):
        harness = _AdvHarness(frame=self._frame(1))
        self.assertFalse(harness.adv_list_at_bottom())

    def test_identical_frame_counts_as_bottom(self):
        frame = self._frame(2)
        harness = _AdvHarness(frame=frame)
        harness.adv_list_at_bottom()
        harness._frame = frame.copy()
        self.assertTrue(harness.adv_list_at_bottom())

    def test_changed_frame_is_not_bottom(self):
        harness = _AdvHarness(frame=self._frame(3))
        harness.adv_list_at_bottom()
        harness._frame = self._frame(4)
        self.assertFalse(harness.adv_list_at_bottom())

    def test_threshold_matches_maaend_value(self):
        self.assertEqual(LIST_COMPLETE_THRESHOLD, 0.98)

    def test_reset_forgets_snapshot(self):
        frame = self._frame(5)
        harness = _AdvHarness(frame=frame)
        harness.adv_list_at_bottom()
        harness._list_snapshot = None
        harness._frame = frame.copy()
        self.assertFalse(harness.adv_list_at_bottom())


class TestFriendVisitTemplateLoading(unittest.TestCase):
    def test_load_template_warns_once_about_missing_image(self):
        harness = _AdvHarness()
        with unittest.mock.patch.object(friend_visit.cv2, "imread", return_value=None):
            harness.adv_load_template("friend_visit/Missing.png")
            harness.adv_load_template("friend_visit/Missing.png")
        warnings = [message for message in harness.logs if "模板图片读取失败" in message]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Missing.png", warnings[0])


class TestAdvancedVisitVisitCount(unittest.TestCase):
    def test_visit_one_returns_count_of_quick_visits(self):
        harness = _VisitOneHarness(quick_visits=2)
        self.assertEqual(harness.adv_visit_one(_row("甲#1"), ["总控中枢"]), 3)
        self.assertEqual(harness.quick_calls, 3)

    def test_visit_one_stops_quick_visit_at_remaining_quota(self):
        harness = _VisitOneHarness(quick_visits=5)
        self.assertEqual(harness.adv_visit_one(_row("甲#1"), ["总控中枢"], limit=2), 2)
        self.assertEqual(harness.quick_calls, 1)

    def test_visit_one_returns_zero_when_entering_ship_fails(self):
        harness = _VisitOneHarness(enter_ok=False)
        self.assertEqual(harness.adv_visit_one(_row("甲#1"), ["总控中枢"]), 0)
        self.assertEqual(harness.leave_calls, 0)


class TestAdvancedVisitQuota(unittest.TestCase):
    def test_quota_is_passed_and_actual_visits_are_counted(self):
        harness = _VisitLoopHarness([_row("甲#1"), _row("乙#2"), _row("丙#3")], [2, 1], {"max": 3})
        self.assertTrue(harness.advanced_visit())
        self.assertEqual(harness.limits, [3, 1])
        self.assertTrue(any("共拜访 3 位好友" in message for message in harness.logs))

    def test_skipped_friend_does_not_use_up_the_limit(self):
        harness = _VisitLoopHarness([_row("甲#1"), _row("乙#2")], [0, 1], {"max": 3})
        self.assertTrue(harness.advanced_visit())
        self.assertEqual(harness.limits, [3, 3])
        self.assertTrue(any("共拜访 1 位好友" in message for message in harness.logs))

    def test_reopen_failure_after_bottom_is_logged(self):
        harness = _VisitLoopHarness([], [], {"remark_only": True})
        harness.CFG_REMARK_ONLY = "remark_only"
        harness.adv_reopen_friend_list = lambda: False
        self.assertFalse(harness.advanced_visit())
        self.assertTrue(any("重新打开好友列表失败" in message for message in harness.errors))


if __name__ == "__main__":
    unittest.main()
