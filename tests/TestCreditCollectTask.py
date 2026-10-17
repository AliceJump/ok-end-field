import re
import unittest
from types import SimpleNamespace

from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.CreditCollectTask import CreditCollectTask


def text_box(name, x=0, y=0, height=20):
    return SimpleNamespace(name=name, x=x, y=y, height=height)


class _CreditHarness(CreditCollectTask):
    def __init__(self, visits=(), quick=(), lists=("visited",)):
        self.visits = iter(visits)
        self.quick_results = iter(quick)
        self.list_results = iter(lists)
        self.list_requests = []
        self.quick_requests = 0
        self.info = {}
        self.logged = []

    def _collect_current_friend_credit(self):
        return next(self.visits)

    def _quick_credit_visit(self):
        self.quick_requests += 1
        return next(self.quick_results)

    def _visit_credit_friend_from_list(self, remaining, first=False):
        self.list_requests.append((remaining, first))
        return next(self.list_results)

    def info_set(self, key, value):
        self.info[key] = value

    def log_info(self, message):
        self.logged.append(message)


class TestCreditCollectTask(unittest.TestCase):
    def test_counter_rows_support_split_ocr_and_refresh_text(self):
        boxes = [
            text_box("今日可生产助力次数", 150, 31),
            text_box("5", 60, 1),
            text_box("4 /", 0, 0),
            text_box("0／5", 0, 30),
            text_box("22:15:15后刷新", 0, 58),
        ]
        self.assertEqual(CreditCollectTask._parse_credit_remaining(boxes), (4, 0))

    def test_counter_header_supports_both_fractions_in_one_box(self):
        self.assertEqual(CreditCollectTask._parse_credit_remaining([text_box("0/5 | 0/5")]), (0, 0))

    def test_missing_or_invalid_counter_is_not_completion(self):
        for texts in ([], ["0/5"], ["8/5", "0/5"], ["0/0", "0/5"], ["0/5", "0/5", "1/5"]):
            with self.subTest(texts=texts):
                self.assertIsNone(
                    CreditCollectTask._parse_credit_remaining(
                        [text_box(text, y=index * 30) for index, text in enumerate(texts)]
                    )
                )

    def test_initial_list_with_zero_remaining_finishes_without_visiting(self):
        task = _CreditHarness(lists=("complete",))
        self.assertTrue(task._collect_friend_credit())
        self.assertEqual(task.quick_requests, 0)
        self.assertEqual(task.info, {"exchange_time": 0, "help_time": 0})

    def test_quick_visit_finishes_partly_completed_day_using_real_deltas(self):
        task = _CreditHarness(visits=(((1, 0), (1, 1)), ((0, 0), (1, 0))), quick=(True,))
        self.assertTrue(task._collect_friend_credit())
        self.assertEqual(task.list_requests, [((None, None), True)])
        self.assertEqual(task.quick_requests, 1)
        self.assertEqual(task.info, {"exchange_time": 2, "help_time": 1})

    def test_absent_button_with_remaining_actions_checks_list(self):
        task = _CreditHarness(
            visits=(((2, 1), (1, 1)), ((0, 0), (2, 1))),
            quick=(False,),
            lists=("visited", "visited"),
        )
        self.assertTrue(task._collect_friend_credit())
        self.assertEqual(task.list_requests[-1], ((2, 1), False))

    def test_no_candidate_with_positive_remaining_does_not_report_completion(self):
        task = _CreditHarness(visits=(((2, 1), (1, 1)),), quick=(False,), lists=("visited", "failed"))
        self.assertFalse(task._collect_friend_credit())
        self.assertEqual(task.info, {"exchange_time": 1, "help_time": 1})

    def test_two_visits_without_progress_disable_quick_visit(self):
        task = _CreditHarness(
            visits=(((2, 1), (0, 0)), ((2, 1), (0, 0)), ((0, 0), (2, 1))),
            quick=(True,),
            lists=("visited", "visited"),
        )
        self.assertTrue(task._collect_friend_credit())
        self.assertEqual(task.quick_requests, 1)
        self.assertEqual(len(task.list_requests), 2)

    def test_list_without_progress_is_bounded(self):
        task = _CreditHarness(
            visits=(((2, 1), (0, 0)), ((2, 1), (0, 0))),
            quick=(False,),
            lists=("visited", "visited"),
        )
        self.assertFalse(task._collect_friend_credit())
        self.assertEqual(task.info, {"exchange_time": 0, "help_time": 0})

    def test_failed_counter_read_preserves_confirmed_statistics(self):
        task = _CreditHarness(visits=(((2, 1), (1, 1)), None), quick=(True,))
        self.assertFalse(task._collect_friend_credit())
        self.assertEqual(task.info, {"exchange_time": 1, "help_time": 1})


class _TerminalHarness(CreditCollectTask):
    width = 1920

    def __init__(self, counts, exchange=False, buttons=(), prefer_grow=True):
        self.counts = iter(counts)
        self.exchange = exchange
        self.buttons = iter(buttons)
        self.config = {"尝试仅收培育室": prefer_grow}
        self.clicked = []
        self.keys = []

    def press_key(self, key):
        self.keys.append(key)

    def wait_ui_stable(self, **kwargs):
        pass

    def _wait_credit_remaining(self, previous=None):
        return next(self.counts)

    def box_of_screen(self, *args):
        return args

    def wait_click_feature(self, **kwargs):
        return self.exchange

    def find_feature(self, **kwargs):
        return next(self.buttons)

    def scroll_relative(self, *args, **kwargs):
        pass

    def click(self, target, **kwargs):
        self.clicked.append(target)

    def wait_pop_up(self, **kwargs):
        pass

    def back(self):
        self.keys.append("esc")

    def ensure_in_friend_boat(self):
        return True

    def log_info(self, message):
        pass


class TestCreditVisitActions(unittest.TestCase):
    def test_missing_exchange_button_preserves_exchange_budget(self):
        task = _TerminalHarness(counts=((3, 0),))
        self.assertEqual(task._collect_current_friend_credit(), ((3, 0), (0, 0)))

    def test_rejected_help_click_does_not_increment_success(self):
        task = _TerminalHarness(counts=((0, 2), None), buttons=([text_box("assist", x=100)],))
        self.assertEqual(task._collect_current_friend_credit(), ((0, 2), (0, 0)))
        self.assertEqual(len(task.clicked), 1)

    def test_grow_preference_uses_rightmost_button_once(self):
        left, right = text_box("assist", x=100), text_box("assist", x=1600)
        task = _TerminalHarness(counts=((0, 3), (0, 2)), buttons=([right, left],))
        self.assertEqual(task._collect_current_friend_credit(), ((0, 2), (0, 1)))
        self.assertEqual(task.clicked, [right])

    def test_non_preferred_help_refreshes_buttons_after_each_click(self):
        first, second = text_box("assist", x=100), text_box("assist", x=300)
        task = _TerminalHarness(
            counts=((0, 2), (0, 1), (0, 0)),
            buttons=([first, second], [second]),
            prefer_grow=False,
        )
        self.assertEqual(task._collect_current_friend_credit(), ((0, 0), (0, 2)))
        self.assertEqual(task.clicked, [first, second])

    def test_grow_preference_without_available_rightmost_room_falls_back(self):
        other = text_box("assist", x=100)
        task = _TerminalHarness(counts=((0, 1), (0, 0)), buttons=([], [other]))
        self.assertEqual(task._collect_current_friend_credit(), ((0, 0), (0, 1)))
        self.assertEqual(task.clicked, [other])


class TestQuickCreditVisit(unittest.TestCase):
    def task(self, label, departed=True):
        task = _TerminalHarness(counts=())
        task.lang = SimpleNamespace(
            credit_collect=SimpleNamespace(
                quick_visit=re.compile("快速拜访下位"),
                no_next_visit=re.compile("暂无下位可拜访"),
            )
        )
        task.box = SimpleNamespace(top=object())
        task.wait_ocr = lambda **kwargs: [text_box(label)] if label else []
        task.wait_until = lambda *args, **kwargs: departed
        task.find_one = lambda **kwargs: fL.left_button
        return task

    def test_empty_or_missing_button_does_not_send_tab(self):
        for label in ("暂无下位可拜访", ""):
            task = self.task(label)
            self.assertFalse(task._quick_credit_visit())
            self.assertEqual(task.keys, [])

    def test_unchanged_old_hud_is_not_a_successful_visit(self):
        task = self.task("快速拜访下位", departed=False)
        self.assertFalse(task._quick_credit_visit())
        self.assertEqual(task.keys, ["tab"])

    def test_quick_visit_requires_departure_then_arrival(self):
        task = self.task("快速拜访下位")
        self.assertTrue(task._quick_credit_visit())
        self.assertEqual(task.keys, ["tab"])


class _ListHarness(_TerminalHarness):
    def __init__(self, header, features=()):
        super().__init__(counts=())
        self.header = iter(header)
        self.features = iter(features)
        self.searched = []
        self.lang = SimpleNamespace(daily_routine_mixin=SimpleNamespace(k_e84c3ae9="好友"))
        self.box = SimpleNamespace(right=object())

    def wait_click_ocr(self, **kwargs):
        return True

    def safe_back(self, **kwargs):
        return True

    def ocr(self, **kwargs):
        return next(self.header)

    def find_feature(self, feature, **kwargs):
        self.searched.append(feature)
        return next(self.features)

    def click_confirm(self, **kwargs):
        return True


class TestCreditListFallback(unittest.TestCase):
    def test_actual_zero_header_does_not_select_any_friend(self):
        task = _ListHarness(header=([text_box("0/5")], [text_box("0/5")]))
        self.assertEqual(task._visit_credit_friend_from_list((None, None), first=True), "complete")
        self.assertEqual(task.searched, [])

    def test_partial_header_cannot_report_completion(self):
        task = _ListHarness(header=([text_box("0/5")], []), features=([],) * 16)
        self.assertEqual(task._visit_credit_friend_from_list((None, None), first=True), "failed")

    def test_exhausted_exchange_budget_only_searches_help(self):
        target = text_box("assist", x=100)
        task = _ListHarness(header=([text_box("0/5")], [text_box("2/5")]), features=([target],))
        self.assertEqual(task._visit_credit_friend_from_list((3, 3)), "visited")
        self.assertEqual(task.searched, [fL.can_help_icon])

    def test_no_exchange_candidate_still_searches_help(self):
        target = text_box("assist", x=100)
        task = _ListHarness(
            header=([text_box("2/5")], [text_box("2/5")]),
            features=(*([[]] * 8), [target]),
        )
        self.assertEqual(task._visit_credit_friend_from_list((2, 2)), "visited")
        self.assertEqual(task.searched[-1], fL.can_help_icon)
