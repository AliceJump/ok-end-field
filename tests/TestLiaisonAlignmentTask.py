"""Exercise the user-facing repeated liaison alignment runner without gift actions."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ok import TaskDisabledException
from ok.util.config import Config

from src.config import config as app_config
from src.core.learning.policy import learning_epoch
from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.liaison_mixin import LiaisonMixin
from src.tasks.mixin.navigation_mixin import NavigationMixin
from src.tasks.mixin.workflow_learning import liaison_entry_after_transfer, liaison_entry_after_walk
from src.tasks.test.LiaisonAlignmentTestTask import LiaisonAlignmentTestTask
from tests import TestWorkflowInitialLearning as workflow_tests
from tests.TestInitialAlignmentLearning import box


class HeadlessLiaisonTask(LiaisonAlignmentTestTask):
    width = 1920
    height = 1080


class TestLiaisonAlignmentTask(unittest.TestCase):
    def task(self, cycles=2):
        task = object.__new__(LiaisonAlignmentTestTask)
        task.run = lambda: LiaisonAlignmentTestTask.run.__wrapped__(task)
        task.config = {"循环次数": cycles}
        task._align_once = Mock(return_value=True)
        task.sleep = Mock()
        task.info_set = Mock()
        task.active_time = Mock(return_value=0)
        task.tr = lambda text: text
        task.log_info = Mock()
        task.mark_task_failure = Mock()
        task.send_key_up = Mock()
        return task

    def test_registered_visible_task_has_only_loop_count(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            task = LiaisonAlignmentTestTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
        self.assertTrue(task.visible)
        self.assertEqual(task.default_config, {"循环次数": 5})
        self.assertEqual(set(task.config_type), {"循环次数"})
        self.assertIn(
            ["src.tasks.test.LiaisonAlignmentTestTask", "LiaisonAlignmentTestTask"], app_config["onetime_tasks"]
        )

    def test_loops_record_alignment_failures_and_continue_after_errors(self):
        task = self.task(cycles=4)
        task._align_once.side_effect = [True, False, OSError("frame"), True]
        self.assertFalse(task.run())
        self.assertEqual(task._align_once.call_count, 4)
        self.assertEqual(task.send_key_up.call_args_list, [call(key) for key in "wasd"] * 4)
        self.assertIn("成功 2 次，失败 2 次", task.mark_task_failure.call_args.args[0])

    def test_invalid_counts_cannot_start_moving(self):
        for count in (-1, 1.5, True, "bad", None):
            task = self.task(count)
            with self.subTest(count=count), self.assertRaises(ValueError):
                task.run()
            task._align_once.assert_not_called()

    def test_zero_count_runs_until_manual_stop_and_releases_keys(self):
        task = self.task(cycles=0)
        task._align_once.side_effect = [True, True, TaskDisabledException()]
        with self.assertRaises(TaskDisabledException):
            task.run()
        self.assertEqual(task._align_once.call_count, 3)
        self.assertEqual(task.send_key_up.call_args_list, [call(key) for key in "wasd"] * 3)
        task.mark_task_failure.assert_not_called()

    def test_stop_before_round_cannot_start_moving(self):
        task = self.task()
        task.sleep.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            task.run()
        task._align_once.assert_not_called()

    def production_task(self):
        fixtures = workflow_tests.TestWorkflowInitialLearning()
        fixtures.setUp()
        self.addCleanup(fixtures.doCleanups)
        source = fixtures.task()
        task = object.__new__(HeadlessLiaisonTask)
        vars(task).update(vars(source))
        del task.align_ocr_or_find_target_to_center
        task.start_tracking_and_align_target = lambda *a, **k: NavigationMixin.start_tracking_and_align_target(
            task, *a, **k
        )
        task.ocr = Mock(return_value=[])
        task.lang.daily_liaison_mixin = SimpleNamespace(k_27d2b829="bridge", k_933056f0="trust")
        for name in (
            "navigate_until_target",
            "navigate_to_operator_liaison_station",
            "perform_operator_liaison",
            "collect_and_give_gifts",
        ):
            setattr(task, name, Mock(side_effect=AssertionError(f"must not call {name}")))
        return task, fixtures

    def test_real_one_and_two_walk_entries_replay_their_own_motion_without_navigation(self):
        for walks, dx in ((1, 100), (2, 300)):
            with self.subTest(walks=walks):
                task, fixtures = self.production_task()
                entry = liaison_entry_after_walk(
                    task, liaison_entry_after_transfer(task, epoch=learning_epoch()), walks
                )
                fixtures.fixtures.seed(entry.context, dx=dx, dy=0)
                task.wait_ocr.side_effect = [[]] + [[]] * (walks - 1) + [[box()]]
                events = []
                task.active_and_send_mouse_delta.side_effect = lambda *a, _events=events, **k: (
                    _events.append("replay") or True
                )

                def detect(_events=events, **kwargs):
                    if kwargs.get("feature") == fL.operator_liaison_station_out_map:
                        _events.append("detect")
                    return box()

                task.find_feature.side_effect = detect
                self.assertTrue(LiaisonAlignmentTestTask._align_once(task))
                task.move_keys.assert_has_calls([call("w", duration=1)] * walks)
                self.assertEqual(task.move_keys.call_count, walks)
                self.assertEqual(events[:2], ["replay", "detect"])
                self.assertEqual(task.active_and_send_mouse_delta.call_args.args[:2], (dx, 0))
                record = fixtures.store.get(entry.context.fingerprint)
                self.assertEqual((record.motion.samples, record.motion.mean_dx), (4, dx))
                self.assertNotIn("_liaison_station_entry", vars(task))
                task.navigate_until_target.assert_not_called()
                task.perform_operator_liaison.assert_not_called()
                task.collect_and_give_gifts.assert_not_called()

    def test_each_round_retransfers_and_creates_a_fresh_entry(self):
        task, fixtures = self.production_task()
        task.wait_ocr.side_effect = [[], [box()], [], [box()]]
        self.assertTrue(LiaisonAlignmentTestTask._align_once(task))
        self.assertTrue(LiaisonAlignmentTestTask._align_once(task))
        self.assertEqual(task.wait_click_feature.call_count, 2)
        self.assertEqual(task.move_keys.call_count, 2)
        self.assertEqual(len(fixtures.store.snapshot()), 1)

    def test_unconfirmed_hall_can_align_visually_without_saving(self):
        task, fixtures = self.production_task()
        task.wait_ocr.return_value = []
        self.assertTrue(LiaisonAlignmentTestTask._align_once(task))
        self.assertEqual(fixtures.store.snapshot(), {})
        self.assertTrue(any("learning_entry=False" in item.args[0] for item in task.log_info.call_args_list))

    def test_tracking_wrapper_success_cannot_hide_failed_alignment(self):
        task, _ = self.production_task()
        task.wait_ocr.side_effect = [[], [box()]]
        with patch.object(NavigationMixin, "align_ocr_or_find_target_to_center", return_value=False):
            self.assertFalse(LiaisonAlignmentTestTask._align_once(task))

    def test_failed_transfer_never_opens_tracking_or_uses_old_entry(self):
        task, fixtures = self.production_task()
        task.find_feature.return_value = None
        self.assertFalse(LiaisonAlignmentTestTask._align_once(task))
        self.assertEqual(fixtures.store.snapshot(), {})
        task.move_keys.assert_not_called()
        task.find_one.assert_not_called()

    def test_shared_preparation_preserves_bridge_popup_wait_before_walk(self):
        task, _ = self.production_task()
        task.transfer_to_home_point = Mock(return_value=True)
        task.navigate_to_main_hall = Mock(return_value=True)
        task.ocr.side_effect = [[box()], [], [box()], []]
        task.wait_ocr.return_value = [box()]
        self.assertTrue(LiaisonMixin.prepare_liaison_station_entry(task))
        self.assertEqual(task.next_frame.call_count, 2)
        task.navigate_to_main_hall.assert_called_once()


if __name__ == "__main__":
    unittest.main()
