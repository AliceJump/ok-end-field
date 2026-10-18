"""Run trade, liaison and demo entry paths through the real alignment session."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.core.learning.models import ContextConfidence
from src.core.learning.policy import deny_learning, learning_epoch
from src.data.FeatureList import FeatureList as fL
from src.data.world_map import areas_list
from src.tasks.mixin.common import LiaisonResult
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.mixin.workflow_learning import (
    demo_entry_after_transfer,
    liaison_entry_after_transfer,
    liaison_entry_after_walk,
    take_workflow_context,
    trade_entry_after_visit,
)
from src.tasks.onetime.DemoBattleTask import DemoBattleTask
from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask
from tests import TestInitialAlignmentLearning as initial_tests
from tests.TestInitialAlignmentLearning import box


class TestWorkflowInitialLearning(unittest.TestCase):
    def setUp(self):
        """Use a temporary cache and real input/frame observation for every test."""
        self.fixtures = initial_tests.TestInitialAlignmentLearning()
        self.fixtures.setUp()
        self.addCleanup(self.fixtures.doCleanups)
        self.store = self.fixtures.store

    def task(self):
        """Supply only external UI effects; alignment and entry checks stay real."""
        task = self.fixtures.workflow_task([box()])
        task.find_feature.side_effect = None
        task.find_feature.return_value = box()
        task.find_one = Mock(return_value=box())
        task.wait_feature = Mock(return_value=box())
        task.wait_click_feature = Mock(return_value=box())
        task.wait_ui_stable = Mock(return_value=True)
        task.wait_ocr = Mock(return_value=[box()])
        task.click = Mock()
        task.press_key = Mock()
        task.move_keys = Mock()
        task.ensure_main = Mock()
        task.ensure_map = Mock()
        task.ensure_in_friend_boat = Mock(return_value=True)
        task.to_near_transfer_point = Mock(return_value=True)
        task.navigate_until_target = Mock(return_value=True)
        task.mark_task_failure = Mock()
        task.box = SimpleNamespace(bottom_right=None, left=None, center=None, top=None, top_left=None)
        task.lang = SimpleNamespace(
            daily_trade_mixin=SimpleNamespace(k_fa04e4df="terminal"),
            liaison_mixin=SimpleNamespace(k_80b758b9="hall"),
        )
        task.start_tracking_and_align_target = lambda *a, **k: NavigationMixin.start_tracking_and_align_target(
            task, *a, **k
        )
        return task

    def trade_entry(self, task):
        """Model a successful visit to the common friend-boat spawn."""
        return trade_entry_after_visit(task, epoch=learning_epoch())

    def test_trade_spawn_is_separated_from_other_workflows_and_capture_profiles(self):
        task = self.task()
        entry = self.trade_entry(task)
        demo = demo_entry_after_transfer(task, epoch=learning_epoch())
        liaison = liaison_entry_after_transfer(task, epoch=learning_epoch())
        task.width = 2560
        wide = self.trade_entry(task)
        self.assertEqual(len({item.context.fingerprint for item in (entry, demo, liaison, wide)}), 4)
        self.assertEqual(entry.confidence, ContextConfidence.HIGH)

    def test_entry_is_single_use_and_rejects_wrong_owner_target_size_or_epoch(self):
        task = self.task()
        for invalid in ("owner", "target", "size", "epoch"):
            with self.subTest(invalid=invalid):
                entry = self.trade_entry(task)
                caller, target = task, fL.market_dispatch_terminal_out
                if invalid == "owner":
                    caller = self.task()
                elif invalid == "target":
                    target = fL.operator_liaison_station_out_map
                elif invalid == "size":
                    task.width = 2560
                else:
                    with deny_learning("battle"):
                        pass
                self.assertIsNone(take_workflow_context(caller, entry, target)[0])
                task.width = 1920
                self.assertIsNone(take_workflow_context(task, entry, fL.market_dispatch_terminal_out)[0])

    def test_unstable_or_denied_entry_does_not_grant_or_touch_cache(self):
        task = self.task()
        task.wait_ui_stable.return_value = False
        self.assertIsNone(self.trade_entry(task))
        task.wait_ui_stable.return_value = True
        with deny_learning("post_battle"):
            self.assertIsNone(self.trade_entry(task))
            self.assertIsNone(demo_entry_after_transfer(task, epoch=learning_epoch()))
        epoch = learning_epoch()

        def change_epoch(**kwargs):
            with deny_learning("battle"):
                pass
            return True

        task.wait_ui_stable.side_effect = change_epoch
        self.assertIsNone(demo_entry_after_transfer(task, epoch=epoch))
        self.assertEqual(self.store.snapshot(), {})

    def test_trade_replays_before_first_world_detection_and_saves_before_failed_navigation(self):
        task = self.task()
        entry = self.trade_entry(task)
        context = entry.context
        self.fixtures.seed(context, dx=100, dy=0)
        events = []
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("replay") or True
        task.find_feature.side_effect = lambda **k: events.append("detect") or box()

        def navigate(**kwargs):
            self.assertNotIn("learning_session", kwargs)
            self.assertEqual(self.store.get(context.fingerprint).motion.samples, 4)
            task.active_and_send_mouse_delta(999, 0)
            return False

        task.navigate_until_target.side_effect = navigate
        self.assertFalse(RegionalBuildTask.navigate_to_friend_exchange(task, learning_entry=entry))
        self.assertEqual(events[:2], ["replay", "detect"])
        self.assertEqual(self.store.get(context.fingerprint).motion.mean_dx, 100)
        self.assertEqual(self.store.get(context.fingerprint).failure_count, 0)
        self.assertTrue(entry.consumed)

    def test_entry_stability_error_falls_back_and_stop_is_never_swallowed(self):
        task = self.task()
        task.wait_ui_stable.side_effect = OSError("frame")
        self.assertIsNone(self.trade_entry(task))
        task.wait_ui_stable.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            self.trade_entry(task)
        self.assertEqual(self.store.snapshot(), {})

    def test_arbitrary_friend_boat_call_and_failed_confirmation_never_look_up_history(self):
        task = self.task()
        entry = self.trade_entry(task)
        task.ensure_in_friend_boat.return_value = False
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertTrue(RegionalBuildTask.navigate_to_friend_exchange(task, learning_entry=entry))
            task.ensure_in_friend_boat.return_value = True
            self.assertTrue(RegionalBuildTask.navigate_to_friend_exchange(task))
        self.assertEqual(self.store.snapshot(), {})

    def test_failed_map_selection_consumes_entry_and_never_replays(self):
        task = self.task()
        entry = self.trade_entry(task)
        self.fixtures.seed(entry.context, dx=100, dy=0)
        task.find_one.return_value = None
        self.assertFalse(
            task.start_tracking_and_align_target("map", fL.market_dispatch_terminal_out, learning_entry=entry)
        )
        self.assertTrue(entry.consumed)
        task.active_and_send_mouse_delta.assert_not_called()

    def test_missing_tracking_button_and_epoch_change_on_map_close_disable_learning(self):
        for failure in ("tracking", "epoch"):
            with self.subTest(failure=failure):
                task = self.task()
                entry = self.trade_entry(task)
                if failure == "tracking":
                    task.wait_feature.return_value = None
                else:

                    def close_map(*args, **kwargs):
                        with deny_learning("battle"):
                            pass

                    task.press_key.side_effect = close_map
                with patch(
                    "src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")
                ):
                    self.assertTrue(
                        task.start_tracking_and_align_target(
                            "map", fL.market_dispatch_terminal_out, learning_entry=entry
                        )
                    )

    def test_missing_world_marker_fails_prior_without_saving_a_sample(self):
        task = self.task()
        entry = self.trade_entry(task)
        self.fixtures.seed(entry.context, dx=100, dy=0)
        task.find_feature.return_value = None
        task.next_frame.side_effect = lambda: task.sleep(1) or object()
        self.assertFalse(
            task.start_tracking_and_align_target("map", fL.market_dispatch_terminal_out, learning_entry=entry)
        )
        record = self.store.get(entry.context.fingerprint)
        self.assertEqual((record.motion.samples, record.failure_count), (3, 1))

    def test_map_close_waits_for_stable_main_before_replay_without_requiring_target(self):
        task = self.task()
        entry = self.trade_entry(task)
        self.fixtures.seed(entry.context, dx=100, dy=0)
        events = []
        task.ensure_main.side_effect = lambda: events.append("main")
        task.wait_ui_stable.side_effect = lambda **kwargs: events.append("stable") or True
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("replay") or True
        task.find_feature.side_effect = lambda **kwargs: events.append("detect") or box()
        self.assertTrue(
            task.start_tracking_and_align_target("map", fL.market_dispatch_terminal_out, learning_entry=entry)
        )
        self.assertEqual(events[:4], ["main", "stable", "replay", "detect"])

    def test_unstable_map_close_or_cancel_cannot_replay(self):
        for failure in (False, TaskDisabledException()):
            with self.subTest(failure=failure):
                task = self.task()
                entry = self.trade_entry(task)
                self.fixtures.seed(entry.context, dx=100, dy=0)
                before = self.store.get(entry.context.fingerprint)
                task.wait_ui_stable.side_effect = failure if isinstance(failure, Exception) else None
                task.wait_ui_stable.return_value = failure
                with patch(
                    "src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")
                ):
                    if isinstance(failure, TaskDisabledException):
                        with self.assertRaises(TaskDisabledException):
                            task.start_tracking_and_align_target(
                                "map", fL.market_dispatch_terminal_out, learning_entry=entry
                            )
                    else:
                        self.assertTrue(
                            task.start_tracking_and_align_target(
                                "map", fL.market_dispatch_terminal_out, learning_entry=entry
                            )
                        )
                task.active_and_send_mouse_delta.assert_not_called()
                self.assertEqual(self.store.get(entry.context.fingerprint), before)

    def test_learning_world_confirmation_errors_fall_back_but_main_stop_propagates(self):
        for operation, error in (
            ("ensure_main", RuntimeError("not main")),
            ("wait_ui_stable", OSError("frame")),
            ("ensure_main", TaskDisabledException()),
        ):
            with self.subTest(operation=operation, error=type(error).__name__):
                task = self.task()
                entry = self.trade_entry(task)
                self.fixtures.seed(entry.context, dx=100, dy=0)
                before = self.store.get(entry.context.fingerprint)
                getattr(task, operation).side_effect = error
                with patch(
                    "src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")
                ):
                    if isinstance(error, TaskDisabledException):
                        with self.assertRaises(TaskDisabledException):
                            task.start_tracking_and_align_target(
                                "map", fL.market_dispatch_terminal_out, learning_entry=entry
                            )
                    else:
                        self.assertTrue(
                            task.start_tracking_and_align_target(
                                "map", fL.market_dispatch_terminal_out, learning_entry=entry
                            )
                        )
                if operation == "ensure_main":
                    # Only the pre-map entry check ran; failed world confirmation
                    # must not start a second stability wait or touch experience.
                    self.assertEqual(task.wait_ui_stable.call_count, 1)
                task.active_and_send_mouse_delta.assert_not_called()
                self.assertEqual(self.store.get(entry.context.fingerprint), before)

    def test_partial_replay_keeps_history_and_stop_restores_input_sender(self):
        for failure in (OSError("partial"), TaskDisabledException()):
            with self.subTest(failure=type(failure).__name__):
                task = self.task()
                entry = self.trade_entry(task)
                self.fixtures.seed(entry.context, dx=100, dy=0)
                before = self.store.get(entry.context.fingerprint)
                sender = task.active_and_send_mouse_delta
                sender.side_effect = failure
                if isinstance(failure, TaskDisabledException):
                    with self.assertRaises(TaskDisabledException):
                        task.start_tracking_and_align_target(
                            "map", fL.market_dispatch_terminal_out, learning_entry=entry
                        )
                else:
                    self.assertTrue(
                        task.start_tracking_and_align_target(
                            "map", fL.market_dispatch_terminal_out, learning_entry=entry
                        )
                    )
                self.assertIs(task.active_and_send_mouse_delta, sender)
                self.assertEqual(self.store.get(entry.context.fingerprint), before)

    def test_buy_sell_passes_entry_only_after_go_and_confirmed_new_visit(self):
        task = self.task()
        area = areas_list[0]
        task.config = {area: True, f"{area}买入价": 900, f"{area}卖出价": 4500}
        task.lang.daily_trade_mixin = SimpleNamespace(
            **dict.fromkeys(("k_33fb3f9c", "k_d6bdcc47", "k_7cf40bbd", "k_cd3a269a", "k_23926d61", "k_b84e4cb0"), "ui")
        )
        good = SimpleNamespace(
            stock_quantity=1,
            good_name="good",
            name_box=SimpleNamespace(name="good"),
            friend_name_box=SimpleNamespace(name="friend-a", x=800, y=300, height=30),
        )
        task.to_model_area = Mock(return_value=True)
        task.wait_click_ocr = Mock(return_value=True)
        task.collect_market_goods_info = Mock(return_value=([], None))
        task.analyze_goods_info = Mock(return_value=(None, [good], False))
        task.wait_pop_up = Mock()
        task.plus_max = Mock(return_value=True)
        task.tr = lambda value: value
        task.navigate_to_friend_exchange = Mock(return_value=True)
        self.assertEqual(RegionalBuildTask.buy_sell(task, target_areas=[area]), (True, True))
        entry = task.navigate_to_friend_exchange.call_args.kwargs["learning_entry"]
        self.assertEqual(entry.context.task, "trade")
        good.friend_name_box.name = "friend-b"
        self.assertEqual(RegionalBuildTask.buy_sell(task, target_areas=[area]), (True, True))
        other = task.navigate_to_friend_exchange.call_args.kwargs["learning_entry"]
        self.assertIsNot(entry, other)
        self.assertEqual(entry.context.fingerprint, other.context.fingerprint)
        task.navigate_to_friend_exchange.reset_mock()
        task.ensure_in_friend_boat.return_value = False
        self.assertEqual(RegionalBuildTask.buy_sell(task, target_areas=[area]), (False, False))
        task.navigate_to_friend_exchange.assert_not_called()

    def test_liaison_transfer_walk_proof_tracks_actual_attempt_count(self):
        task = self.task()
        self.assertTrue(LiaisonMixin.transfer_to_home_point(task))
        task.wait_ocr.side_effect = [[], [box()]]
        self.assertTrue(LiaisonMixin.navigate_to_main_hall(task))
        entry = task._liaison_station_entry
        self.assertEqual(entry.context.entry_route_signature, (1000, 1000))
        self.assertEqual(entry.confidence, ContextConfidence.MEDIUM)
        self.assertEqual(task.move_keys.call_count, 2)
        other = liaison_entry_after_walk(task, liaison_entry_after_transfer(task, epoch=learning_epoch()), 1)
        self.assertNotEqual(entry.context.fingerprint, other.context.fingerprint)

    def test_liaison_shared_boat_state_or_custom_point_cannot_grant_bridge_proof(self):
        task = self.task()
        task._daily_boat_state_confirmed = True
        for kwargs in ({"should_check_out_boat": True}, {"box": object()}):
            task._liaison_station_entry = object()
            self.assertTrue(LiaisonMixin.transfer_to_home_point(task, **kwargs))
            self.assertIsNone(task._liaison_transfer_entry)
            self.assertIsNone(task._liaison_station_entry)

    def test_unconfirmed_hall_keeps_legacy_flow_but_does_not_create_learning_entry(self):
        task = self.task()
        task._liaison_transfer_entry = liaison_entry_after_transfer(task, epoch=learning_epoch())
        task.wait_ocr.return_value = []
        self.assertTrue(LiaisonMixin.navigate_to_main_hall(task))
        self.assertIsNone(task._liaison_station_entry)
        self.assertNotIn("_liaison_transfer_entry", vars(task))

    def test_liaison_first_call_saves_before_special_chat_and_retry_has_no_session(self):
        task = self.task()
        entry = liaison_entry_after_walk(task, liaison_entry_after_transfer(task, epoch=learning_epoch()), 1)
        task._liaison_station_entry = entry
        self.fixtures.seed(entry.context, dx=100, dy=0)
        task.click_with_alt = Mock()
        task.send_key_up = Mock()

        def navigate(**kwargs):
            self.assertNotIn("learning_session", kwargs)
            self.assertEqual(self.store.get(entry.context.fingerprint).motion.samples, 4)
            return kwargs["found_special_callback"]()

        task.navigate_until_target.side_effect = navigate
        self.assertEqual(LiaisonMixin.navigate_to_operator_liaison_station(task), LiaisonResult.FIND_CHAT_ICON)
        before = self.store.snapshot()
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertEqual(LiaisonMixin.navigate_to_operator_liaison_station(task), LiaisonResult.FIND_CHAT_ICON)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(task.active_and_send_mouse_delta.call_args.args[:2], (50, 0))

    def test_demo_full_replay_and_save_do_not_wait_for_navigation_success(self):
        task = self.task()
        context = demo_entry_after_transfer(task, epoch=learning_epoch()).context
        self.fixtures.seed(context, dx=100, dy=20)
        task.navigate_until_target.return_value = False
        self.assertFalse(DemoBattleTask._demo_click_track_and_transfer(task))
        self.assertEqual(task.active_and_send_mouse_delta.call_args.args[:2], (100, 20))
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.failure_count), (4, 0))
        self.assertNotIn("learning_session", task.navigate_until_target.call_args.kwargs)

    def test_failed_demo_transfer_and_denied_tracking_do_not_access_learning_store(self):
        task = self.task()
        task.to_near_transfer_point.return_value = False
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertFalse(DemoBattleTask._demo_click_track_and_transfer(task))
            entry = self.trade_entry(task)
            with deny_learning("post_battle"):
                self.assertTrue(
                    task.start_tracking_and_align_target("map", fL.market_dispatch_terminal_out, learning_entry=entry)
                )
        self.assertEqual(self.store.snapshot(), {})


if __name__ == "__main__":
    unittest.main()
