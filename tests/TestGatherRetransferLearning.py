"""A real stable retransfer reuses pre-battle initialization, never reward navigation."""

import json
import unittest
from unittest.mock import Mock

from ok import TaskDisabledException

from src.core.learning.policy import (
    deny_learning,
    learning_after_verified_transfer,
    learning_allowed,
    learning_epoch,
)
from src.tasks.mixin.gather_learning import (
    gather_initial_context,
    gather_retransfer_learning,
    gather_transfer_alignment,
)
from src.tasks.mixin.navigation_learning import align_initial_target
from src.tasks.onetime.BattleTask import BattleTask
from tests import TestGatherInitialLearning as gather_tests
from tests.TestInitialAlignmentLearning import box


class TestGatherRetransferLearning(unittest.TestCase):
    def setUp(self):
        self.fixtures = gather_tests.TestGatherInitialLearning()
        self.fixtures.setUp()
        self.addCleanup(self.fixtures.doCleanups)
        self.store = self.fixtures.store

    def task(self, detections=None):
        task = self.fixtures.entry_task(detections)
        task.lang.daily_battle_mixin.k_39d12e73_1 = "claim"
        task._open_index = Mock(side_effect=lambda: self.assertFalse(learning_allowed()))
        task.to_stage = Mock(return_value=True)
        task.to_near_transfer_point.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        task.ensure_main.side_effect = lambda: self.assertFalse(learning_allowed())
        task.wait_ui_stable.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        task.wait_ocr.return_value = [box()]
        task.wait_feature = Mock(side_effect=lambda **kwargs: self.assertFalse(learning_allowed()) or True)

        def click_ocr(**kwargs):
            if kwargs.get("match") == task.lang.daily_battle_mixin.k_b0e3a2da:
                self.assertTrue(learning_allowed())
            else:
                self.assertFalse(learning_allowed())
            return True

        task.wait_click_ocr.side_effect = click_ocr
        task.mark_task_failure = Mock()
        task.click_confirm = Mock(side_effect=lambda: self.assertFalse(learning_allowed()))
        return task

    def prebattle_context(self, task, *, distances=()):
        # Build the ordinary identity before entering the post-battle chain.
        task.wait_ui_stable.side_effect = None
        entry = self.fixtures.entry(task)
        context = (
            gather_initial_context(task, entry, distances=distances)
            if distances
            else gather_transfer_alignment(task, entry)[0]
        )
        task.wait_ui_stable.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        return context

    def test_retransfer_replays_prebattle_identity_and_saves_before_failed_reward_navigation(self):
        task = self.task()
        context = self.prebattle_context(task)
        self.fixtures.fixtures.seed(context, dx=100, dy=0)

        def navigate(**kwargs):
            self.assertFalse(learning_allowed())
            self.assertNotIn("learning_session", kwargs)
            persisted = json.loads(self.store.path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["records"][context.fingerprint]["motion"]["samples"], 4)
            task.active_and_send_mouse_delta(999, 0)
            return False

        task.navigate_until_target.side_effect = navigate
        with deny_learning("post_battle"):
            self.assertFalse(BattleTask._gather_retry_navigate(task))
            self.assertFalse(learning_allowed())
        task.wait_click_ocr.assert_not_called()
        self.assertTrue(task.navigate_until_target.call_args.kwargs["nav_disappears_on_arrival"])
        self.assertEqual(task.navigate_until_target.call_args.kwargs["detection_mode"], "parallel")
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.motion.mean_dx, record.failure_count), (4, 100, 0))
        self.assertEqual(len(self.store.snapshot()), 1)
        self.assertEqual(task.active_and_send_mouse_delta.call_args_list[0].args[:2], (100, 0))
        self.assertTrue(learning_allowed())

    def test_cold_retransfer_records_real_correction_with_the_same_identity(self):
        task = self.task([box(1500), box()])
        context = self.prebattle_context(task)
        task.navigate_until_target.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        self.assertTrue(BattleTask._gather_retry_navigate(task))
        record = self.store.get(context.fingerprint)
        self.assertEqual(record.motion.samples, 1)
        self.assertNotEqual(record.motion.mean_dx, 0)

    def test_failed_transfer_never_restores_learning_or_checks_stability(self):
        task = self.task([box(1500), box()])
        task.to_near_transfer_point.side_effect = None
        task.to_near_transfer_point.return_value = False
        self.assertTrue(BattleTask._gather_retry_navigate(task))
        task.wait_ui_stable.assert_not_called()
        self.assertEqual(self.store.snapshot(), {})

    def test_failed_index_selection_never_transfers_or_opens_learning_window(self):
        task = self.task()
        task.to_stage.return_value = False
        self.assertFalse(BattleTask._gather_retry_navigate(task))
        task.to_near_transfer_point.assert_not_called()
        task.wait_ui_stable.assert_not_called()
        self.assertEqual(self.store.snapshot(), {})

    def test_unknown_stage_cannot_grant_a_reset_entry(self):
        task = self.task()
        task.battle_ctx.stage_name = "unknown"
        self.assertTrue(BattleTask._gather_retry_navigate(task))
        task.wait_ui_stable.assert_not_called()
        self.assertEqual(self.store.snapshot(), {})

    def test_unstable_transfer_or_stability_error_keeps_visual_only(self):
        for stable in (False, OSError("frame")):
            with self.subTest(stable=stable):
                task = self.task([box(1500), box()])
                task.wait_ui_stable.side_effect = stable if isinstance(stable, Exception) else None
                task.wait_ui_stable.return_value = stable
                self.assertTrue(BattleTask._gather_retry_navigate(task))
                self.assertEqual(self.store.snapshot(), {})

    def test_active_battle_or_unrelated_parent_deny_cannot_be_cleared_by_retransfer(self):
        for reason in ("battle", "manual", "unknown"):
            with self.subTest(reason=reason):
                task = self.task([box(1500), box()])
                with deny_learning(reason):
                    self.assertTrue(BattleTask._gather_retry_navigate(task))
                    self.assertFalse(learning_allowed())
                self.assertEqual(self.store.snapshot(), {})

    def test_stopping_after_reset_restores_deny_and_invalidates_old_entry(self):
        task = self.task()
        task.wait_ui_stable.side_effect = None
        old_entry = self.fixtures.entry(task)
        task.wait_ui_stable.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        task.active_and_send_mouse_delta.side_effect = TaskDisabledException()
        self.fixtures.fixtures.seed(gather_transfer_alignment(task, old_entry)[0], dx=100, dy=0)
        with deny_learning("post_battle"):
            with self.assertRaises(TaskDisabledException):
                BattleTask._gather_retry_navigate(task)
            self.assertFalse(learning_allowed())
        self.assertIsNone(gather_initial_context(task, old_entry))
        self.assertTrue(learning_allowed())

    def test_stopping_during_physical_confirmation_never_restores_learning(self):
        task = self.task()
        task.wait_ui_stable.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            BattleTask._gather_retry_navigate(task)
        self.assertEqual(self.store.snapshot(), {})
        self.assertTrue(learning_allowed())

    def test_epoch_stage_or_capture_change_during_confirmation_rejects_reset(self):
        for changed in ("epoch", "stage", "capture", "scroll"):
            task = self.task()

            def stable(_changed=changed, _task=task, **kwargs):
                if _changed == "epoch":
                    with deny_learning("battle"):
                        pass
                elif _changed == "stage":
                    _task.battle_ctx.stage_name = "首墩"
                elif _changed == "capture":
                    _task.width = 2560
                else:
                    _task.zip_line_scroll_enabled.return_value = True
                return True

            task.wait_ui_stable.side_effect = stable
            with (
                self.subTest(changed=changed),
                deny_learning("post_battle"),
                gather_retransfer_learning(task, True) as entry,
            ):
                self.assertIsNone(entry)
                self.assertFalse(learning_allowed())

    def test_zipline_retransfer_uses_the_same_route_and_on_board_identity(self):
        task = self.task()
        task.find_feature.side_effect = None
        task.find_feature.return_value = box()
        context = self.prebattle_context(task, distances=(108,))
        self.fixtures.fixtures.seed(context, dx=100, dy=20)
        task.get_zip_line_config_value.return_value = "108"
        task.wait_ocr.side_effect = [[], [box()]]

        def zipline(distances, **kwargs):
            self.assertTrue(learning_allowed())
            self.assertEqual(distances, [108])
            self.assertEqual(kwargs["target_learning_context"], context)
            self.assertEqual(kwargs["target_learning_epoch"], learning_epoch())
            align_initial_target(task, context, "gather", ocr=False)

        task.zip_line_list_go = Mock(side_effect=zipline)
        task.navigate_until_target.side_effect = lambda **kwargs: self.assertFalse(learning_allowed()) or True
        self.assertTrue(BattleTask._gather_retry_navigate(task))
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 4)
        self.assertEqual(len(self.store.snapshot()), 1)
        task.wait_ocr.assert_any_call(match="claim", box=None, time_out=3, raise_if_not_found=False)

    def test_policy_requires_verified_reset_and_invalidates_entries_on_both_edges(self):
        with deny_learning("post_battle"):
            before = learning_epoch()
            with learning_after_verified_transfer(verified=False) as allowed:
                self.assertFalse(allowed)
                self.assertFalse(learning_allowed())
            self.assertEqual(learning_epoch(), before)
            with learning_after_verified_transfer(verified=True) as allowed:
                self.assertTrue(allowed)
                self.assertTrue(learning_allowed())
                inside = learning_epoch()
                self.assertGreater(inside, before)
            self.assertFalse(learning_allowed())
            self.assertGreater(learning_epoch(), inside)


if __name__ == "__main__":
    unittest.main()
