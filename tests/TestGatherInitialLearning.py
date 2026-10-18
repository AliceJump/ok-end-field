"""Exercise gather production entry points and their actual initial-alignment loops."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.core.learning.models import ContextConfidence
from src.core.learning.policy import deny_learning, learning_epoch
from src.tasks.mixin.gather_learning import (
    gather_entry_after_transfer,
    gather_initial_context,
    gather_transfer_alignment,
)
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.onetime.BattleTask import BattleContext, BattleTask
from tests import TestInitialAlignmentLearning as initial_tests
from tests import TestZipLineGoldGate as gold_tests
from tests.TestInitialAlignmentLearning import box


class TestGatherInitialLearning(unittest.TestCase):
    def setUp(self):
        self.fixtures = initial_tests.TestInitialAlignmentLearning()
        self.fixtures.setUp()
        self.addCleanup(self.fixtures.doCleanups)
        self.store = self.fixtures.store

    def entry_task(self, detections=None):
        task = self.fixtures.workflow_task(detections or [box()])
        task.battle_ctx = BattleContext(stage_name="武陵城")
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.wait_ui_stable = Mock(return_value=True)
        task.ensure_main = Mock()
        task.to_near_transfer_point = Mock(return_value=True)
        task._read_zip_line_settled_arrow_angle = Mock(return_value=(90, 0.9))
        task._ZIP_LINE_ARROW_MIN_SCORE = 0.6
        task._click_track_and_transfer = lambda: BattleTask._click_track_and_transfer(task)
        task._navigate_via_zip_line = lambda **kwargs: BattleTask._navigate_via_zip_line(task, **kwargs)
        task.navigate_to_gather = lambda: BattleTask.navigate_to_gather(task)
        task.get_zip_line_config_value = Mock(return_value="")
        task.lang = SimpleNamespace(
            daily_battle_mixin=SimpleNamespace(
                k_bfe73e18="challenge",
                k_b8a81b7a="abandon",
                k_b0e3a2da="board",
            )
        )
        task.box = SimpleNamespace(bottom_right=None)
        task.wait_ocr = Mock(return_value=[])
        task.wait_click_ocr = Mock(return_value=True)
        task.wait_click_feature = Mock(return_value=False)
        task.navigate_until_target = Mock(return_value=True)
        task._switch_team_before_activate_for_gather = Mock()
        return task

    def entry(self, task):
        return gather_entry_after_transfer(task, True)

    def board_task(self):
        task = self.fixtures.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        task.battle_ctx = BattleContext(stage_name="武陵城")
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.wait_ui_stable = Mock(return_value=True)
        task.ensure_main = Mock()
        task.wait_ocr = Mock(return_value=[])
        task.find_feature = Mock(return_value=box())
        return task

    def run_board(self, task, context, **kwargs):
        return ZipLineMixin.zip_line_list_go(
            task,
            [108],
            config_key="武陵城",
            need_v=False,
            target=("gather", "feature"),
            target_learning_context=context,
            **kwargs,
        )

    def test_transfer_proof_requires_clicked_transfer_and_stable_world(self):
        task = self.entry_task()
        for failed in (False, None):
            task.to_near_transfer_point.return_value = failed
            self.assertIsNone(task._click_track_and_transfer())
        task.wait_ui_stable.assert_not_called()
        task.to_near_transfer_point.return_value = True
        task.wait_ui_stable.return_value = False
        self.assertIsNone(task._click_track_and_transfer())
        task.wait_ui_stable.return_value = True
        self.assertEqual(task._click_track_and_transfer().stage, "武陵城")

    def test_world_confirmation_and_stability_propagate_stop(self):
        task = self.entry_task()
        task.ensure_main.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            task._click_track_and_transfer()
        task.wait_ui_stable.assert_not_called()
        task.ensure_main.side_effect = None
        task.wait_ui_stable.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            task._click_track_and_transfer()

    def test_unknown_stage_or_denied_transfer_never_creates_entry_evidence(self):
        task = self.entry_task()
        task.battle_ctx.stage_name = "unknown"
        self.assertIsNone(self.entry(task))
        task.battle_ctx.stage_name = "武陵城"
        with deny_learning("battle"):
            self.assertIsNone(self.entry(task))
        task.wait_ui_stable.assert_not_called()

    def test_transfer_entry_does_not_survive_combat_during_stability_check(self):
        task = self.entry_task()

        def changed(**kwargs):
            with deny_learning("battle"):
                pass
            return True

        task.wait_ui_stable.side_effect = changed
        self.assertIsNone(self.entry(task))

    def test_stage_route_pose_and_capture_are_separate_contexts(self):
        task = self.entry_task()
        entry = self.entry(task)
        transfer = gather_initial_context(task, entry)
        board = gather_initial_context(task, entry, distances=(89, 64))
        fork = gather_initial_context(task, entry, distances=(89, 68))
        self.assertEqual(len({transfer.fingerprint, board.fingerprint, fork.fingerprint}), 3)
        self.assertEqual((transfer.capture_profile.axes, board.capture_profile.axes), ("x", "xy"))
        task.battle_ctx.stage_name = "首墩"
        self.assertIsNone(gather_initial_context(task, entry))
        other = gather_initial_context(task, self.entry(task), distances=(89, 64))
        self.assertNotEqual(other.fingerprint, board.fingerprint)
        task.battle_ctx.stage_name = "武陵城"
        task.width = 2560
        self.assertIsNone(gather_initial_context(task, entry))
        task.width = 1920
        task.zip_line_scroll_enabled.return_value = True
        self.assertIsNone(gather_initial_context(task, entry))

    def test_expired_transfer_epoch_cannot_create_any_initial_context(self):
        task = self.entry_task()
        entry = self.entry(task)
        with deny_learning("post_battle"):
            pass
        self.assertIsNone(gather_initial_context(task, entry))

    def test_index_fast_transfer_is_high_confidence_and_yaw_is_optional(self):
        task = self.entry_task()
        entry = self.entry(task)
        context, confidence = gather_transfer_alignment(task, entry)
        self.assertEqual((context.start_yaw_bucket, confidence), (18, ContextConfidence.HIGH))
        task._read_zip_line_settled_arrow_angle.return_value = None
        unknown, confidence = gather_transfer_alignment(task, entry)
        self.assertIsNone(unknown.start_yaw_bucket)
        self.assertEqual(confidence, ContextConfidence.HIGH)
        self.assertNotEqual(unknown.fingerprint, context.fingerprint)

    def test_no_route_replays_before_detection_then_saves_before_navigation_failure(self):
        for reading in ((90, 0.9), None, (90, 0.2)):
            with self.subTest(reading=reading):
                task = self.entry_task()
                task._read_zip_line_settled_arrow_angle.return_value = reading
                context, _ = gather_transfer_alignment(task, self.entry(task))
                self.fixtures.seed(context, dx=100, dy=0)
                before = self.store.get(context.fingerprint).motion.samples
                events = []
                task.active_and_send_mouse_delta.side_effect = lambda *a, _events=events, **k: (
                    _events.append("replay") or True
                )
                task.find_feature.side_effect = lambda _events=events, **kwargs: _events.append("detect") or box()

                def navigate(_context=context, _before=before, _events=events, _task=task, **kwargs):
                    self.assertNotIn("learning_session", kwargs)
                    self.assertEqual(self.store.get(_context.fingerprint).motion.samples, _before + 1)
                    _events.append("nav")
                    _task.active_and_send_mouse_delta(500, 0)
                    raise RuntimeError("navigation failed later")

                task.navigate_until_target.side_effect = navigate
                with self.assertRaisesRegex(RuntimeError, "later"):
                    BattleTask.battle_gather(task)
                self.assertEqual(events[:3], ["replay", "detect", "nav"])
                self.assertEqual(task.active_and_send_mouse_delta.call_args_list[0].args[:2], (100, 0))
                self.assertEqual(self.store.get(context.fingerprint).motion.samples, before + 1)
                self.assertEqual(self.store.get(context.fingerprint).motion.mean_dx, 100)

    def test_failed_transfer_retains_visual_alignment_without_store_access(self):
        task = self.entry_task([box(1500, 800), box()])
        task.to_near_transfer_point.return_value = False
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertFalse(BattleTask.battle_gather(task))
        task.navigate_until_target.assert_called_once()
        task._read_zip_line_settled_arrow_angle.assert_not_called()
        self.assertEqual(self.store.snapshot(), {})

    def test_existing_route_passes_stage_private_identity_and_on_board_entry(self):
        task = self.entry_task()
        task.get_zip_line_config_value.return_value = "89,64"
        task.zip_line_list_go = Mock()
        entry = self.entry(task)
        self.assertTrue(BattleTask._navigate_via_zip_line(task, initial_entry=entry))
        call = task.zip_line_list_go.call_args
        self.assertEqual(call.args[0], [89, 64])
        self.assertEqual(call.kwargs["config_key"], "武陵城")
        self.assertNotIn("route_family", call.kwargs)
        self.assertNotIn("need_v", call.kwargs)
        self.assertEqual(call.kwargs["target_learning_context"].entry, "after_zipline_on_board")
        self.assertEqual(call.kwargs["target_learning_epoch"], entry.epoch)

    def test_on_board_first_alignment_learns_before_dismount_and_never_presses_v(self):
        task = self.board_task()
        context = replace(gather_initial_context(task, self.entry(task), distances=(108,)), start_yaw_bucket=18)
        self.fixtures.seed(context)
        task.press_key = Mock()
        events = []
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("input") or True
        task.find_feature.side_effect = lambda **kwargs: events.append("gather") or box()

        def dismount(*args, **kwargs):
            self.assertEqual(self.store.get(context.fingerprint).motion.samples, 4)
            events.append("dismount")

        task.click.side_effect = dismount
        self.run_board(task, context, target_learning_epoch=learning_epoch())
        self.assertEqual(events, ["input", "gather", "dismount"])
        task.press_key.assert_not_called()
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)

    def test_mismatched_route_and_reboard_pose_cannot_borrow_board_prior(self):
        for change in ({"entry_route_signature": (109,)}, {"entry": "after_zipline_reboard"}):
            task = self.board_task()
            context = replace(gather_initial_context(task, self.entry(task), distances=(108,)), start_yaw_bucket=18)
            self.fixtures.seed(context)
            before = self.store.get(context.fingerprint)
            self.run_board(task, replace(context, **change))
            self.assertEqual(self.store.get(context.fingerprint), before)
            task.active_and_send_mouse_delta.assert_not_called()

    def test_stale_epoch_before_terminal_alignment_blocks_board_replay(self):
        task = self.board_task()
        context = replace(gather_initial_context(task, self.entry(task), distances=(108,)), start_yaw_bucket=18)
        self.fixtures.seed(context)
        original_epoch = learning_epoch()

        def ride(*args, **kwargs):
            with deny_learning("battle"):
                pass
            return [box()]

        task.ocr.side_effect = ride
        self.run_board(task, context, target_learning_epoch=original_epoch)
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 3)
        task.active_and_send_mouse_delta.assert_not_called()

    def test_reboarding_cannot_borrow_a_gather_on_board_pose(self):
        task = self.board_task()
        context = replace(gather_initial_context(task, self.entry(task), distances=(108,)), start_yaw_bucket=18)
        self.fixtures.seed(context)
        task.box = SimpleNamespace(bottom_right=None)
        task.wait_ocr.side_effect = [[box()], []]
        task.strafe_search = lambda check, **kwargs: check()
        task.press_key = Mock()
        task.click_with_alt = Mock()
        ZipLineMixin.zip_line_list_go(
            task,
            [108],
            config_key="武陵城",
            need_v=True,
            target=("gather", "feature"),
            target_learning_context=context,
        )
        task.press_key.assert_called_once_with("v", after_sleep=1)
        task.active_and_send_mouse_delta.assert_not_called()
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 3)

    def test_after_zipline_fallback_alignment_cannot_learn_transfer_entry(self):
        task = self.entry_task([box(1500, 800), box()])
        task._navigate_via_zip_line = Mock(return_value=True)
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertFalse(BattleTask.battle_gather(task))
        task.navigate_until_target.assert_called_once()
        self.assertEqual(self.store.snapshot(), {})

    def test_epoch_changed_during_yaw_read_cannot_grant_a_new_session(self):
        task = self.entry_task()
        entry = self.entry(task)

        def combat():
            with deny_learning("battle"):
                pass
            return 90, 0.9

        task._read_zip_line_settled_arrow_angle.side_effect = combat
        self.assertEqual(gather_transfer_alignment(task, entry), (None, ContextConfidence.INVALID))
        task = self.board_task()
        context = replace(gather_initial_context(task, self.entry(task), distances=(108,)), start_yaw_bucket=18)
        self.fixtures.seed(context)
        epoch = learning_epoch()
        task._read_zip_line_settled_arrow_angle.side_effect = combat
        self.run_board(task, context, target_learning_epoch=epoch)
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 3)
        task.active_and_send_mouse_delta.assert_not_called()

    def test_search_during_gather_alignment_continues_without_saving(self):
        task = self.entry_task()
        task.find_feature.side_effect = lambda **kwargs: box() if task.active_time() > 1.2 else None
        with patch("src.tasks.mixin.navigation_mixin.random.randint", return_value=20):
            self.assertFalse(BattleTask.battle_gather(task))
        task.navigate_until_target.assert_called_once()
        self.assertEqual(self.store.snapshot(), {})

    def test_partial_gather_replay_keeps_prior_and_continues_visually(self):
        task = self.entry_task([box(1500, 800), box()])
        context, _ = gather_transfer_alignment(task, self.entry(task))
        self.fixtures.seed(context, dx=100, dy=0)
        before = self.store.get(context.fingerprint)
        task.active_and_send_mouse_delta.side_effect = [OSError("partial input"), True]
        self.assertFalse(BattleTask.battle_gather(task))
        task.navigate_until_target.assert_called_once()
        self.assertEqual(self.store.get(context.fingerprint), before)

    def test_post_battle_transfer_and_board_adapters_have_zero_workflow_access(self):
        task = self.entry_task()
        entry = self.entry(task)
        task.get_zip_line_config_value.return_value = "108"
        task.zip_line_list_go = Mock()
        with deny_learning("post_battle_retry"):
            self.assertIsNone(task._click_track_and_transfer())
            self.assertIsNone(gather_initial_context(task, entry, distances=(108,)))
            self.assertTrue(BattleTask._navigate_via_zip_line(task, initial_entry=entry))
        self.assertNotIn("target_learning_context", task.zip_line_list_go.call_args.kwargs)
        task._read_zip_line_settled_arrow_angle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
