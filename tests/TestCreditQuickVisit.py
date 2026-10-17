import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.CreditCollectTask import CreditCollectTask


class _VisitHarness(CreditCollectTask):
    width, height = 1920, 1080

    def __init__(self, actions, quick=True, prefer=True):
        self.actions = actions
        self.visit = -1
        self.quick = quick
        self.config = {"尝试仅收培育室": prefer}
        self.box = SimpleNamespace(top=None, bottom_left=None, right=None, top_left=None)
        self.lang = SimpleNamespace(
            daily_routine_mixin=SimpleNamespace(
                k_7be4248b="shop",
                k_b693e51a="claim",
                k_f646bcd5="empty",
                k_e84c3ae9="friends",
                k_a730d877="return",
            )
        )
        self.stats = {}
        self.keys = []
        self.ocr_calls = []
        self.backs = []
        self.quick_calls = 0
        self.list_clicks = 0
        self.scrolls = []
        self.help_box = SimpleNamespace(x=1600, y=900)
        self.tick = 0

    def info_set(self, name, value):
        self.stats[name] = value

    def press_key(self, key):
        self.keys.append(key)
        if key == "y":
            self.visit += 1

    def wait_click_ocr(self, **kwargs):
        self.ocr_calls.append(kwargs)
        return [SimpleNamespace(name="无待领取信用")]

    def ensure_main(self, **kwargs):
        pass

    def back(self):
        pass

    def box_of_screen(self, *args):
        return args

    def wait_ui_stable(self, **kwargs):
        pass

    def active_time(self):
        self.tick += 0.1
        return self.tick

    def find_feature(self, feature, **kwargs):
        if feature in (fL.can_exchange_info_icon, fL.can_help_icon):
            return [object()]
        if feature == fL.assist_friend and self.actions[self.visit][1]:
            return [self.help_box]
        return []

    def click(self, target):
        if isinstance(target, list):
            self.list_clicks += 1

    def click_confirm(self, **kwargs):
        pass

    def ensure_in_friend_boat(self):
        return True

    def sleep(self, *args):
        pass

    def log_info(self, message):
        pass

    def tr(self, message):
        return message

    def wait_click_feature(self, **kwargs):
        return self.actions[self.visit][0]

    def scroll_relative(self, *args, **kwargs):
        self.scrolls.append((args, kwargs))

    def wait_pop_up(self, **kwargs):
        pass

    def safe_back(self, feature, **kwargs):
        self.backs.append(feature)
        return True

    def try_quick_visit(self):
        self.quick_calls += 1
        return self.quick


class TestCreditQuickVisit(unittest.TestCase):
    def test_first_visit_uses_list_then_quick_visits(self):
        task = _VisitHarness([(True, True)] * 5)
        self.assertTrue(task.collect_credit())
        self.assertEqual((task.list_clicks, task.quick_calls), (1, 4))
        self.assertEqual(task.stats["exchange_time"], 5)
        self.assertEqual(task.stats["help_time"], 5)
        self.assertEqual(len(task.ocr_calls), 3)  # 仅保留原有商店和好友菜单入口。
        self.assertEqual(task.backs, [fL.friend_page])
        self.assertEqual(task.stats["visit_end_reason"], "action_counts_completed")

    def test_missing_local_action_does_not_exhaust_daily_budget(self):
        task = _VisitHarness([(False, False)] + [(True, True)] * 5)
        self.assertTrue(task.collect_credit())
        self.assertEqual(task.stats["exchange_time"], 5)
        self.assertEqual(task.stats["help_time"], 5)
        self.assertEqual(task.visit, 5)

    def test_switch_failure_falls_back_to_list(self):
        task = _VisitHarness([(True, True)] * 5, quick=False)
        self.assertTrue(task.collect_credit())
        self.assertEqual(task.list_clicks, 5)
        self.assertEqual(task.backs, [fL.friend_page] * 5)

    def test_no_next_person_ends_visit_without_scanning_more_friends(self):
        task = _VisitHarness([(False, False)], quick=None)
        self.assertTrue(task.collect_credit())
        self.assertEqual((task.list_clicks, task.quick_calls), (1, 1))
        self.assertEqual(task.backs, [fL.friend_page])
        self.assertEqual(task.stats["visit_end_reason"], "no_next_person")
        self.assertEqual((task.stats["exchange_time"], task.stats["help_time"]), (0, 0))

    def test_partial_counts_and_no_candidate_are_not_reported_as_full(self):
        task = _VisitHarness([(True, True)], quick=None)
        self.assertTrue(task.collect_credit())
        self.assertEqual(task.stats["visit_end_reason"], "no_next_person")
        self.assertEqual((task.stats["exchange_time"], task.stats["help_time"]), (1, 1))

    def test_two_no_op_visits_do_not_disable_quick_path(self):
        task = _VisitHarness([(False, False)] * 2 + [(True, True)] * 5)
        self.assertTrue(task.collect_credit())
        self.assertEqual((task.list_clicks, task.quick_calls), (1, 6))
        self.assertEqual(task.stats["visit_end_reason"], "action_counts_completed")

    def test_unproductive_visits_remain_bounded_without_list_fallback(self):
        task = _VisitHarness([(False, False)] * 15)
        self.assertFalse(task.collect_credit())
        self.assertEqual(task.quick_calls, 14)
        self.assertEqual(task.list_clicks, 1)
        self.assertEqual(task.stats["exchange_time"], 0)

    def test_help_search_runs_after_exchange_list_exhausted(self):
        task = _VisitHarness([(True, True)] * 5, quick=False)
        original = task.find_feature
        task.find_feature = lambda feature, **kwargs: (
            [] if feature == fL.can_exchange_info_icon else original(feature, **kwargs)
        )
        self.assertTrue(task.collect_credit())
        self.assertTrue(any(args[-1] == 40 for args, kwargs in task.scrolls if args))

    def test_final_allowed_visit_can_complete_the_task(self):
        task = _VisitHarness([(False, False)] * 10 + [(True, True)] * 5)
        self.assertTrue(task.collect_credit())
        self.assertEqual(task.visit, 14)

    def test_failed_boat_entry_recovery_is_bounded(self):
        task = _VisitHarness([])
        task.ensure_in_friend_boat = lambda: False
        self.assertFalse(task.collect_credit())
        self.assertEqual(task.list_clicks, 15)

    def test_empty_list_is_not_reported_as_all_daily_actions_done(self):
        task = _VisitHarness([])
        task.find_feature = lambda **kwargs: []
        self.assertFalse(task.collect_credit())
        self.assertEqual(task.quick_calls, 0)

    def test_non_preferred_help_mode_still_respects_budget(self):
        task = _VisitHarness([(True, True)] * 5, prefer=False)
        self.assertTrue(task.collect_credit())
        self.assertEqual(task.stats["help_time"], 5)

    @staticmethod
    def _run_standalone_business(task):
        # TopmostMixin wraps run() on subclass creation. The lightweight harness
        # intentionally skips runtime setup; exercise the original run() body here.
        return CreditCollectTask.run.__wrapped__(task)

    def test_standalone_run_reports_incomplete_search_as_error(self):
        task = _VisitHarness([])
        task.find_feature = lambda **kwargs: []
        with self.assertRaisesRegex(RuntimeError, "收信用任务未完成"):
            self._run_standalone_business(task)

    def test_standalone_run_accepts_no_next_candidate(self):
        task = _VisitHarness([(False, False)], quick=None)
        self.assertTrue(self._run_standalone_business(task))
        self.assertEqual(task.stats["visit_end_reason"], "no_next_person")

    def test_standalone_run_accepts_completed_action_counts(self):
        task = _VisitHarness([(True, True)] * 5)
        self.assertTrue(self._run_standalone_business(task))
        self.assertEqual(task.stats["visit_end_reason"], "action_counts_completed")

    def transition_task(self, departed=True, arrived=True, hud_stable=True, hud_rechecked=True):
        task = _VisitHarness([])
        task.wait_feature = Mock(return_value=object())
        initial_hud = [object() if hud_stable else None, object() if hud_rechecked else None]

        def hud_match(*args, **kwargs):
            if task.keys:  # Tab 已发送后，检查旧 HUD 是否离开。
                return None if departed else object()
            return initial_hud.pop(0) if initial_hud else object()

        task.find_one = Mock(side_effect=hud_match)
        task.in_friend_boat = Mock(return_value=arrived)
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: bool(condition()))
        return task

    def test_switch_observes_departure_before_accepting_arrival(self):
        task = self.transition_task()
        self.assertTrue(CreditCollectTask.try_quick_visit(task))
        self.assertEqual(task.keys, ["tab"])
        self.assertEqual(task.wait_until.call_count, 3)
        self.assertEqual([c.kwargs["settle_time"] for c in task.wait_until.call_args_list], [0.5] * 3)

    def test_stale_hud_cannot_be_accepted_as_new_boat(self):
        task = self.transition_task(departed=False)
        self.assertFalse(CreditCollectTask.try_quick_visit(task))
        task.in_friend_boat.assert_not_called()

    def test_loading_timeout_falls_back(self):
        task = self.transition_task(arrived=False)
        self.assertFalse(CreditCollectTask.try_quick_visit(task))

    def test_missing_template_on_stable_hud_means_no_candidate(self):
        task = self.transition_task()
        task.wait_feature.return_value = None
        self.assertIsNone(CreditCollectTask.try_quick_visit(task))
        self.assertEqual(task.keys, [])
        self.assertEqual(task.wait_until.call_count, 2)
        self.assertEqual(task.wait_feature.call_args.kwargs["time_out"], 5)

    def test_missing_template_on_unstable_hud_falls_back(self):
        task = self.transition_task(hud_rechecked=False)
        task.wait_feature.return_value = None
        self.assertFalse(CreditCollectTask.try_quick_visit(task))
        self.assertEqual(task.keys, [])

    def test_initial_hud_not_stable_falls_back_without_template_search(self):
        task = self.transition_task(hud_stable=False)
        self.assertFalse(CreditCollectTask.try_quick_visit(task))
        task.wait_feature.assert_not_called()

    def test_terminal_must_close_before_switch(self):
        task = self.transition_task()
        task.safe_back = Mock(return_value=False)
        self.assertFalse(CreditCollectTask.try_quick_visit(task))
        task.wait_feature.assert_not_called()


if __name__ == "__main__":
    unittest.main()
