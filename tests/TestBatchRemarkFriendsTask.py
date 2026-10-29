import unittest

from ok.feature.Box import Box

from src.tasks.onetime.BatchRemarkFriendsTask import BatchRemarkFriendsTask, FriendListLostError
from src.tasks.onetime.FriendVisitAdvanced import CLUE_AVAILABLE_OFFSET


class _RemarkHarness(BatchRemarkFriendsTask):
    """只验证筛选逻辑，设备与 OCR 全部用最小替身。"""

    screen_width = 1920
    screen_height = 1080
    frame = None

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


class _ClueHarness(BatchRemarkFriendsTask):
    """不替身 row_clue_available，用来验证它引用的常量与偏移。"""

    screen_width = 1280
    screen_height = 720
    frame = None

    def __init__(self):
        self.config = {}
        self._visit_templates = {}
        self._list_snapshot = None
        self.calls = []

    def log_warning(self, message, *args, **kwargs):
        pass

    def adv_find_one(self, relative_path, box, threshold=0.9):
        self.calls.append((relative_path, box))
        return None


class _RemarkOneHarness(BatchRemarkFriendsTask):
    """remark_one 的成功判定与清理顺序。"""

    screen_width = 1280
    screen_height = 720
    frame = None

    def __init__(self, open_ok=True, input_ok=True, confirm_ok=True, back_ok=True, visible=True):
        self.config = {}
        self._visit_templates = {}
        self._list_snapshot = None
        self.logs = []
        self.events = []
        self._open_ok = open_ok
        self._input_ok = input_ok
        self._confirm_ok = confirm_ok
        self._back_ok = back_ok
        self._visible = visible

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_warning(self, message, *args, **kwargs):
        self.logs.append(message)

    def log_error(self, message, *args, **kwargs):
        self.logs.append(message)

    def click(self, box, *args, **kwargs):
        self.events.append("click")

    def open_remark_editor(self):
        self.events.append("open")
        return self._open_ok

    def input_remark_text(self, remark):
        self.events.append("input")
        return self._input_ok

    def confirm_remark(self):
        self.events.append("confirm")
        return self._confirm_ok

    def back_to_friend_list(self):
        self.events.append("back")
        return self._back_ok

    def remark_visible_in_list(self, key):
        self.events.append("visible")
        return self._visible


def _row(text, remark=False):
    return {
        "text": text,
        "key": BatchRemarkFriendsTask.adv_row_key(text),
        "remark": remark,
        "enter": Box(0, 0, 10, 10),
        "name_box": Box(0, 0, 100, 10),
    }


class _RemarkOneRunHarness(_RemarkOneHarness):
    """run() 在无法回到好友列表时必须结束，而不是继续扫描错误的画面。"""

    def __init__(self):
        super().__init__()
        self.config = {BatchRemarkFriendsTask.CFG_REMARK_TEXT: "自动1"}
        self._rows = [_row("甲#1")]

    def adv_enter_friend_list(self):
        return True

    def adv_scan_rows(self):
        return self._rows

    def exit_is_set(self):
        return False

    def remark_one(self, row, remark):
        self.events.append("remark_one")
        raise FriendListLostError("无法回到好友列表")


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


class _ReadbackHarness(BatchRemarkFriendsTask):
    """remark_text_visible 只认输入框里的完整备注文本。"""

    screen_width = 1280
    screen_height = 720
    frame = None

    def __init__(self, ocr_text):
        self.config = {}
        self._visit_templates = {}
        self._list_snapshot = None
        self._ocr_text = ocr_text

    def adv_box(self, roi):
        return Box(0, 0, 10, 10)

    def adv_find_text(self, box, pattern, threshold=0.6):
        # 与 ok 的 OCR 一致：用 match 正则去搜索识别到的整行文本
        return Box(0, 0, 10, 10, name=self._ocr_text) if pattern.search(self._ocr_text) else None


class TestRemarkTextReadback(unittest.TestCase):
    def test_full_remark_is_recognized(self):
        self.assertTrue(_ReadbackHarness("自动1").remark_text_visible("自动1"))

    def test_placeholder_text_is_not_accepted_as_input(self):
        self.assertFalse(_ReadbackHarness("请输入备注").remark_text_visible("自动1"))

    def test_longer_text_does_not_count_as_full_match(self):
        self.assertFalse(_ReadbackHarness("自动12").remark_text_visible("自动1"))


class TestRowClueAvailable(unittest.TestCase):
    def test_uses_module_level_offset_constant(self):
        task = _ClueHarness()
        row = {
            "text": "甲#1",
            "key": "甲#1",
            "remark": False,
            "enter": Box(100, 200, 20, 40),
            "name_box": Box(0, 0, 10, 10),
        }
        self.assertFalse(task.row_clue_available(row))
        self.assertEqual(len(task.calls), 1)
        relative_path, region = task.calls[0]
        self.assertTrue(relative_path.endswith("ClueExchangeAvailable.png"))
        dx, dy, dw, dh = CLUE_AVAILABLE_OFFSET
        self.assertEqual((region.x, region.y, region.width, region.height), (100 + dx, 200 + dy, 20 + dw, 40 + dh))


class TestRemarkOneResult(unittest.TestCase):
    def test_returns_true_only_when_remark_is_visible_in_list(self):
        task = _RemarkOneHarness()
        self.assertTrue(task.remark_one(_row("甲#1"), "自动1"))
        self.assertEqual(task.events, ["click", "open", "input", "confirm", "back", "visible"])

    def test_returns_false_when_remark_is_not_visible_after_returning(self):
        task = _RemarkOneHarness(visible=False)
        self.assertFalse(task.remark_one(_row("甲#1"), "自动1"))
        self.assertIn("visible", task.events)

    def test_editor_failure_still_returns_to_friend_list(self):
        task = _RemarkOneHarness(open_ok=False)
        self.assertFalse(task.remark_one(_row("甲#1"), "自动1"))
        self.assertEqual(task.events, ["click", "open", "back"])
        self.assertNotIn("input", task.events)

    def test_confirm_failure_counts_as_failure(self):
        task = _RemarkOneHarness(confirm_ok=False)
        self.assertFalse(task.remark_one(_row("甲#1"), "自动1"))
        self.assertIn("back", task.events)

    def test_unable_to_return_to_list_stops_the_task(self):
        task = _RemarkOneHarness(back_ok=False)
        with self.assertRaises(FriendListLostError):
            task.remark_one(_row("甲#1"), "自动1")


class TestRunAbortsWhenFriendListIsLost(unittest.TestCase):
    def test_run_stops_and_logs_after_list_is_lost(self):
        task = _RemarkOneRunHarness()
        BatchRemarkFriendsTask.run.__wrapped__(task)
        self.assertEqual(task.events, ["remark_one"])
        self.assertTrue(any("无法回到好友列表" in message for message in task.logs))
        self.assertTrue(any("本次处理 0 位好友" in message for message in task.logs))


if __name__ == "__main__":
    unittest.main()
