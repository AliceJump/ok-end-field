import unittest

from ok.feature.Box import Box

from src.tasks.onetime.BatchRemarkFriendsTask import BatchRemarkFriendsTask


class _RemarkHarness(BatchRemarkFriendsTask):
    """只验证筛选逻辑，设备与 OCR 全部用最小替身。"""

    screen_width = 1920
    screen_height = 1080

    def __init__(self, rows, clue_keys=None):
        self.config = {}
        self._rows = rows
        self._clue_keys = set(clue_keys or ())
        self._visit_templates = {}
        self._list_snapshot = None

    def adv_scan_rows(self):
        return self._rows

    def row_clue_available(self, row):
        return row["key"] in self._clue_keys

    def adv_offset_box(self, box, offset):
        return box


def _row(text, remark=False):
    return {
        "text": text,
        "key": BatchRemarkFriendsTask.adv_row_key(text),
        "remark": remark,
        "enter": Box(0, 0, 10, 10),
        "name_box": Box(0, 0, 100, 10),
    }


class TestPickRowToRemark(unittest.TestCase):
    def test_skips_friends_that_already_have_remark(self):
        task = _RemarkHarness([_row("甲#1", remark=True), _row("乙#2")])
        picked = task.pick_row_to_remark(set(), clue_only=False)
        self.assertEqual(picked["key"], "乙#2")

    def test_skips_already_visited_friends(self):
        task = _RemarkHarness([_row("甲#1"), _row("乙#2")])
        picked = task.pick_row_to_remark({"甲#1"}, clue_only=False)
        self.assertEqual(picked["key"], "乙#2")

    def test_clue_only_keeps_only_friends_available_for_intel_exchange(self):
        task = _RemarkHarness([_row("甲#1"), _row("乙#2")], clue_keys={"乙#2"})
        picked = task.pick_row_to_remark(set(), clue_only=True)
        self.assertEqual(picked["key"], "乙#2")

    def test_clue_only_returns_none_when_no_friend_qualifies(self):
        task = _RemarkHarness([_row("甲#1")])
        self.assertIsNone(task.pick_row_to_remark(set(), clue_only=True))

    def test_returns_none_when_every_friend_is_done(self):
        task = _RemarkHarness([_row("甲#1", remark=True), _row("乙#2", remark=True)])
        self.assertIsNone(task.pick_row_to_remark(set(), clue_only=False))

    def test_row_with_remark_before_id_is_still_treated_as_remarked(self):
        row = _row("甲#1", remark=True)
        row["text"] = "（自动）甲#1"
        task = _RemarkHarness([row])
        self.assertIsNone(task.pick_row_to_remark(set(), clue_only=False))


class TestRemarkTextHandling(unittest.TestCase):
    def test_empty_remark_text_is_rejected_before_clicking(self):
        task = _RemarkHarness([_row("甲#1")])
        task.adv_enter_friend_list = lambda: True
        task.exit_is_set = lambda: False
        logs = []
        task.log_error = lambda message, *a, **k: logs.append(message)
        task.log_info = lambda message, *a, **k: logs.append(message)
        # TopmostMixin wraps run() on subclass creation；这里直接调用原始 run()。
        BatchRemarkFriendsTask.run.__wrapped__(task)
        self.assertTrue(any("备注内容" in message for message in logs))
        self.assertEqual(task._rows[0]["key"], "甲#1")


if __name__ == "__main__":
    unittest.main()
