"""The dedicated navigation runner cannot enter challenge or combat code paths."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ok import TaskDisabledException
from ok.util.config import Config

from src.config import config as app_config
from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING, stages_dict
from src.tasks.test.GatherNavigationTestTask import GatherNavigationTestTask
from tests import TestGatherInitialLearning as gather_tests
from tests.TestInitialAlignmentLearning import box


class TestGatherNavigationTask(unittest.TestCase):
    def task(self, sequence=None, cycles=2):
        task = object.__new__(GatherNavigationTestTask)
        task.run = lambda: GatherNavigationTestTask.run.__wrapped__(task)
        task.config = {"淤积点序列": sequence or ["武陵城", "首墩"], "循环次数": cycles}
        task._navigate_once = Mock(return_value=True)
        task.sleep = Mock()
        task.info_set = Mock()
        task.active_time = Mock(return_value=0)
        task.tr = lambda text: text
        task.log_info = Mock()
        task.mark_task_failure = Mock()
        task.send_key_up = Mock()
        task.release_yolo_detector = Mock()
        for name in (
            "battle_gather",
            "battle_recycle",
            "auto_battle",
            "to_battle",
            "_switch_team_before_activate_for_gather",
        ):
            setattr(task, name, Mock(side_effect=AssertionError(f"must not call {name}")))
        return task

    def test_registered_visible_task_has_only_navigation_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            task = GatherNavigationTestTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
        self.assertTrue(task.visible)
        self.assertEqual(set(task.default_config), {"淤积点序列", "循环次数"})
        self.assertEqual(set(task.config_type), set(task.default_config))
        self.assertEqual(
            task.config_type["淤积点序列"]["options_available"], stages_dict[STAGE_CATEGORY_ENERGY_POOLING]
        )
        self.assertTrue(task.config_type["淤积点序列"]["allow_duplication"])
        self.assertIn(
            ["src.tasks.test.GatherNavigationTestTask", "GatherNavigationTestTask"], app_config["onetime_tasks"]
        )

    def test_order_and_duplicate_stages_are_preserved_for_each_cycle(self):
        task = self.task(["武陵城", "首墩", "武陵城"], cycles=2)
        self.assertTrue(task.run())
        self.assertEqual(
            task._navigate_once.call_args_list, [call(stage) for stage in ["武陵城", "首墩", "武陵城"] * 2]
        )
        self.assertEqual(task.send_key_up.call_args_list, [call(key) for key in ("w", "a", "s", "d")] * 6)
        task.release_yolo_detector.assert_called_once()
        task.mark_task_failure.assert_not_called()

    def test_invalid_plan_is_rejected_before_any_navigation(self):
        for sequence in ([], ["unknown"], ["武陵城", "干员经验"]):
            task = self.task()
            task.config["淤积点序列"] = sequence
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                task.run()
            task._navigate_once.assert_not_called()

    def test_non_integer_or_negative_cycle_count_is_rejected(self):
        for value in (-1, 1.5, True, "bad", None):
            task = self.task(cycles=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                task.run()
            task._navigate_once.assert_not_called()

    def test_string_sequence_accepts_chinese_commas_and_keeps_order(self):
        task = self.task("首墩， 武陵城,首墩", cycles=1)
        self.assertTrue(task.run())
        self.assertEqual(task._navigate_once.call_args_list, [call("首墩"), call("武陵城"), call("首墩")])

    def test_route_errors_and_arrival_failures_continue_and_report_failure(self):
        task = self.task(cycles=2)
        task._navigate_once.side_effect = [RuntimeError("transfer failed"), False, True, True]
        self.assertFalse(task.run())
        self.assertEqual(task._navigate_once.call_count, 4)
        task.mark_task_failure.assert_called_once()
        self.assertIn("成功 2 次，失败 2 次", task.mark_task_failure.call_args.args[0])

    def test_zero_cycles_repeats_until_manual_stop_and_restores_resources(self):
        task = self.task(["武陵城"], cycles=0)
        task._navigate_once.side_effect = [True, True, TaskDisabledException()]
        with self.assertRaises(TaskDisabledException):
            task.run()
        self.assertEqual(task._navigate_once.call_count, 3)
        self.assertEqual(task.send_key_up.call_args_list, [call(key) for key in ("w", "a", "s", "d")] * 3)
        task.release_yolo_detector.assert_called_once()
        task.mark_task_failure.assert_not_called()

    def test_stop_before_next_route_never_navigates(self):
        task = self.task()
        task.sleep.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            task.run()
        task._navigate_once.assert_not_called()
        task.release_yolo_detector.assert_called_once()

    def arrival_task(self):
        task = self.task()
        task._open_index = Mock()
        task.to_stage = Mock(return_value=True)
        task.navigate_to_gather = Mock(return_value=True)
        task.wait_ocr = Mock(return_value=[object()])
        task.lang = SimpleNamespace(daily_battle_mixin=SimpleNamespace(k_bfe73e18="challenge"))
        task.box_of_screen = Mock(return_value="arrival-box")
        task.wait_click_feature = Mock(side_effect=AssertionError("must not click activation"))
        task.wait_click_ocr = Mock(side_effect=AssertionError("must not click challenge"))
        return task

    def test_single_route_uses_production_navigation_then_only_observes_arrival(self):
        task = self.arrival_task()
        self.assertTrue(GatherNavigationTestTask._navigate_once(task, "首墩"))
        self.assertEqual(
            (task.battle_ctx.stage_name, task.battle_ctx.category_name), ("首墩", STAGE_CATEGORY_ENERGY_POOLING)
        )
        task._open_index.assert_called_once()
        task.to_stage.assert_called_once()
        task.navigate_to_gather.assert_called_once()
        task.wait_ocr.assert_called_once_with(
            match="challenge", box="arrival-box", time_out=3, settle_time=0.5, raise_if_not_found=False
        )
        task.wait_click_feature.assert_not_called()
        task.wait_click_ocr.assert_not_called()
        task.battle_gather.assert_not_called()
        task.battle_recycle.assert_not_called()

    def test_stage_selection_or_navigation_failure_cannot_report_arrival(self):
        for component in ("to_stage", "navigate_to_gather"):
            task = self.arrival_task()
            getattr(task, component).return_value = False
            with self.subTest(component=component):
                self.assertFalse(GatherNavigationTestTask._navigate_once(task, "首墩"))
            task.wait_ocr.assert_not_called()

    def test_navigation_result_alone_is_not_stable_arrival_evidence(self):
        task = self.arrival_task()
        task.wait_ocr.return_value = []
        self.assertFalse(GatherNavigationTestTask._navigate_once(task, "首墩"))

    def test_real_navigation_chain_saves_learning_without_entering_activation_code(self):
        fixtures = gather_tests.TestGatherInitialLearning()
        fixtures.setUp()
        self.addCleanup(fixtures.doCleanups)
        task = fixtures.entry_task([box(1500, 800), box()])
        task._open_index = Mock()
        task.to_stage = Mock(return_value=True)
        task.wait_ocr.return_value = [box()]
        task.wait_click_feature.side_effect = AssertionError("must not activate")
        task._switch_team_before_activate_for_gather.side_effect = AssertionError("must not switch for battle")
        self.assertTrue(GatherNavigationTestTask._navigate_once(task, "武陵城"))
        saved = list(fixtures.store.snapshot().values())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].motion.samples, 1)
        task.wait_click_feature.assert_not_called()
        task._switch_team_before_activate_for_gather.assert_not_called()


if __name__ == "__main__":
    unittest.main()
