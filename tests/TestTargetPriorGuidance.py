"""Exercise real initial alignment with advisory geometry, replay and full-range fallback."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from ok import TaskDisabledException

from src.core.learning.models import ContextConfidence, LearningRecord, TargetPrior
from src.core.learning.policy import deny_learning
from src.core.learning.routes import RouteLearning
from src.core.learning.target_prior import roi_around
from src.tasks.mixin.navigation_learning import align_initial_target, initial_alignment
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineMixin
from tests import TestInitialAlignmentLearning as initial_tests
from tests import TestZipLineGoldGate as gold_tests
from tests.TestInitialAlignmentLearning import box
from tests.TestLearningCore import workflow_context


def screen_box(left, top, right, bottom):
    return SimpleNamespace(x=left * 1920, y=top * 1080, width=(right - left) * 1920, height=(bottom - top) * 1080)


class TestTargetPriorGuidance(unittest.TestCase):
    def setUp(self):
        self.fixtures = initial_tests.TestInitialAlignmentLearning()
        self.fixtures.setUp()
        self.addCleanup(self.fixtures.doCleanups)
        self.store = self.fixtures.store
        self.context = workflow_context()
        self.prior = TargetPrior(
            initial_position=(0.8, 0.6),
            aligned_position=(0.5, 0.5),
            roi=roi_around((0.8, 0.6)),
            search_direction="right",
        )

    def task(self, detections=None):
        task = self.fixtures.workflow_task(detections or [box()])
        task.box_of_screen = screen_box
        return task

    def seed(self, *, count=1, context=None, dx=100, dy=20):
        context = context or self.context
        for index in range(count):
            self.store.update(
                context.fingerprint,
                lambda record, now=index: record.successful(dx, dy, target=self.prior, now=now),
            )

    def test_cold_alignment_records_real_original_pose_roi_and_direction(self):
        task = self.task([box(1500, 800), box()])
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        target = self.store.get(self.context.fingerprint).target
        self.assertEqual(target.initial_position, (1500 / 1920, 800 / 1080))
        self.assertEqual(target.aligned_position, (0.5, 0.5))
        self.assertEqual(target.roi, roi_around(target.initial_position))
        self.assertEqual(target.search_direction, "right")

    def test_low_sample_roi_hit_uses_original_pose_without_action_replay(self):
        self.seed()
        task = self.task()
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        task.active_and_send_mouse_delta.assert_not_called()
        detected_box = task.find_feature.call_args.kwargs["box"]
        self.assertAlmostEqual(detected_box.x / 1920, self.prior.roi[0])
        self.assertAlmostEqual((detected_box.x + detected_box.width) / 1920, 1550 / 1920)

    def test_local_miss_falls_back_to_original_box_in_the_same_real_frame(self):
        self.seed()
        task = self.task([None, box()])
        original_box = screen_box((1920 - 1550) / 1920, 150 / 1080, 1550 / 1920, (1080 - 150) / 1080)
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        calls = task.find_feature.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0].kwargs["frame"], calls[1].kwargs["frame"])
        self.assertEqual(vars(calls[1].kwargs["box"]), vars(original_box))
        self.assertEqual(self.store.get(self.context.fingerprint).motion.samples, 2)

    def test_full_replay_precedes_detection_and_uses_aligned_pose_roi(self):
        self.seed(count=3)
        task = self.task()
        events = []
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("input") or True
        task.find_feature.side_effect = lambda **k: events.append("detect") or box()
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        self.assertEqual(events, ["input", "detect"])
        detected_box = task.find_feature.call_args.kwargs["box"]
        self.assertAlmostEqual(detected_box.x / 1920, 0.38)
        self.assertAlmostEqual(detected_box.y / 1080, 0.38)
        record = self.store.get(self.context.fingerprint)
        self.assertEqual(record.motion.samples, 4)
        self.assertEqual(record.target.initial_position, self.prior.initial_position)
        self.assertEqual(record.target.roi, self.prior.roi)

    def test_partial_replay_does_not_use_original_or_final_pose_roi(self):
        self.seed(count=3)
        task = self.task()
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False, confidence=ContextConfidence.MEDIUM))
        self.assertEqual(task.active_and_send_mouse_delta.call_args.args[:2], (50, 10))
        self.assertAlmostEqual(task.find_feature.call_args.kwargs["box"].x / 1920, (1920 - 1550) / 1920)

    def test_local_detector_error_falls_back_but_cancellation_propagates(self):
        for error in (OSError("local detector"), TaskDisabledException()):
            with self.subTest(error=type(error).__name__):
                self.seed()
                task = self.task()
                task.find_feature.side_effect = [error, box()]
                if isinstance(error, TaskDisabledException):
                    with self.assertRaises(TaskDisabledException):
                        align_initial_target(task, self.context, "hud", ocr=False)
                    self.assertEqual(task.find_feature.call_count, 1)
                else:
                    self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
                    self.assertEqual(task.find_feature.call_count, 2)

    def test_roi_is_intersected_with_caller_box_and_disjoint_roi_is_skipped(self):
        for allowed in ((0.7, 0.4, 0.75, 0.65), (0.0, 0.0, 0.1, 0.1)):
            self.seed()
            task = self.task()
            restriction = screen_box(*allowed)
            self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False, box=restriction))
            actual = task.find_feature.call_args.kwargs["box"]
            self.assertGreaterEqual(actual.x, restriction.x)
            self.assertGreaterEqual(actual.y, restriction.y)
            self.assertLessEqual(actual.x + actual.width, restriction.x + restriction.width + 1e-8)
            self.assertLessEqual(actual.y + actual.height, restriction.y + restriction.height + 1e-8)

    def test_ocr_and_yolo_use_real_local_detection_with_original_range_fallback(self):
        for kind in ("ocr", "yolo"):
            with self.subTest(kind=kind):
                self.seed()
                task = self.task()
                detector = Mock(side_effect=[[], [box()]])
                if kind == "ocr":
                    task.ocr = detector
                else:
                    task.yolo_detect = detector
                self.assertTrue(
                    align_initial_target(task, self.context, "target", ocr=kind == "ocr", use_yolo=kind == "yolo")
                )
                self.assertEqual(detector.call_count, 2)
                self.assertIs(detector.call_args_list[0].kwargs["frame"], detector.call_args_list[1].kwargs["frame"])
                self.assertEqual(self.store.get(self.context.fingerprint).target.aligned_position, (0.5, 0.5))

    def test_search_direction_is_one_probe_and_never_saves_search_sample(self):
        self.seed()
        before = self.store.get(self.context.fingerprint)
        task = self.task()
        task.find_feature.side_effect = lambda **kwargs: box() if task.active_time() >= 1.2 else None
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False, only_x=False, max_time=4))
        self.assertEqual(task.active_and_send_mouse_delta.call_args_list[0].args[:2], (480, 0))
        self.assertEqual(self.store.get(self.context.fingerprint), before)
        self.assertTrue(any("search target=" in item.args[0] for item in task.log_info.call_args_list))

    def test_roi_and_prior_alone_cannot_manufacture_alignment_success(self):
        self.seed(count=3)
        task = self.task()
        task.find_feature.side_effect = None
        task.find_feature.return_value = None
        self.assertFalse(
            align_initial_target(
                task, self.context, "hud", ocr=False, max_time=1, raise_if_fail=False, allow_random_move=False
            )
        )
        self.assertEqual(self.store.get(self.context.fingerprint).motion.samples, 3)

    def test_target_hint_cannot_survive_epoch_or_battle_or_capture_change(self):
        self.seed()
        for changed in ("epoch", "battle", "capture"):
            task = self.task()
            detector = Mock(return_value=box())
            with self.subTest(changed=changed), initial_alignment(task, self.context) as learning:
                learning.replay()
                if changed == "epoch":
                    with deny_learning("battle"):
                        pass
                if changed == "capture":
                    task.width = 2560
                    learning.frame_acquired(object())
                if changed == "battle":
                    with deny_learning("battle"):
                        learning.detect_target(detector)
                        self.assertIsNone(learning.search_step(480))
                else:
                    learning.detect_target(detector)
                    self.assertIsNone(learning.search_step(480))
                detector.assert_called_once_with(None)

    def test_rejected_input_disables_geometry_and_does_not_replace_history(self):
        self.seed(count=3)
        before = self.store.get(self.context.fingerprint)
        task = self.task()
        task.active_and_send_mouse_delta.return_value = False
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        self.assertAlmostEqual(task.find_feature.call_args.kwargs["box"].x / 1920, (1920 - 1550) / 1920)
        self.assertEqual(self.store.get(self.context.fingerprint), before)

    def test_route_private_records_geometry_but_shared_action_does_not_borrow_source_roi(self):
        context = self.fixtures.zip_context(self.fixtures.zip_task(), family=("a", "b", "c", "d", "e"))
        route_learning = RouteLearning(self.store)
        for now in range(3):
            self.assertTrue(route_learning.record_success(context, 100, 20, now=now, target=self.prior))
        self.assertEqual(route_learning.lookup(context).target, self.prior)
        shared = route_learning.lookup(replace(context, config_key="other"))
        self.assertEqual(shared.action, (100, 20))
        self.assertIsNone(shared.target)

    def test_zipline_numeric_detector_tries_roi_then_its_original_detector(self):
        task = self.fixtures.zip_task()
        task.box_of_screen = screen_box
        context = self.fixtures.zip_context(task)
        self.seed(context=context)
        task._find_zip_line_template_target.side_effect = [None, gold_tests.TestZipLineGoldGate._matched_icon()]
        with initial_alignment(task, context) as learning:
            learning.replay()
            self.assertTrue(ZipLineMixin._align_zip_line_distance(task, 108, learning_session=learning))
        calls = task._find_zip_line_template_target.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("search_box", calls[0].kwargs)
        self.assertNotIn("search_box", calls[1].kwargs)
        self.assertIs(calls[0].args[1], calls[1].args[1])
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 2)

    def test_zipline_gold_gate_persists_geometry_with_the_complete_raw_acquisition(self):
        task = self.fixtures.zip_task()
        task.box_of_screen = screen_box
        context = self.fixtures.zip_context(task)

        def gate(*args, on_gate_confirmed=None):
            self.assertIsNone(self.store.get(context.fingerprint))
            on_gate_confirmed(task.next_frame())
            return True, None

        task._try_click_on_zip_line.side_effect = gate
        self.fixtures.run_zip(task)
        record = self.store.get(context.fingerprint)
        self.assertEqual(record.target.initial_position, (1400 / 1920, 800 / 1080))
        self.assertEqual(record.target.roi, roi_around(record.target.initial_position))
        self.assertEqual(record.target.aligned_position, (0.5, 0.5))
        sample = record.observations[-1]
        self.assertEqual((sample.dx, sample.dy, sample.verification), (40, 20, "gold_gate"))
        self.assertEqual([(item.dx, item.dy) for item in sample.inputs], [(40, 20)])
        self.assertEqual(sample.environment.capture_size, (1920, 1080))

    def test_zipline_warm_gold_gate_keeps_original_geometry_and_records_replay(self):
        task = self.fixtures.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        task.box_of_screen = screen_box
        context = self.fixtures.zip_context(task)
        self.seed(count=3, context=context)
        self.fixtures.run_zip(task)
        record = self.store.get(context.fingerprint)
        self.assertEqual(record.target, self.prior)
        sample = record.observations[-1]
        self.assertEqual((sample.dx, sample.dy, sample.verification), (100, 20, "gold_gate"))
        self.assertEqual([(item.dx, item.dy) for item in sample.inputs], [(100, 20)])
        self.assertEqual(len(record.observations), 4)

    def test_legacy_aligned_only_record_remains_usable_without_initial_roi(self):
        record = LearningRecord().successful(100, 20, now=0, target=TargetPrior(aligned_position=(0.5, 0.5)))
        self.store.put(self.context.fingerprint, record)
        task = self.task()
        self.assertTrue(align_initial_target(task, self.context, "hud", ocr=False))
        self.assertEqual(task.find_feature.call_count, 1)
        self.assertAlmostEqual(task.find_feature.call_args.kwargs["box"].x / 1920, (1920 - 1550) / 1920)

    def test_zipline_roi_keeps_full_screen_coordinates_and_full_range_still_finds_both_icons(self):
        task = gold_tests.TestZipLineGoldGate._detector_stub()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        template = ZipLineMixin._zip_line_icon_template(task)
        mask = template > 0
        height, width = template.shape
        frame[500 : 500 + height, 780 : 780 + width][mask] = (255, 255, 255)
        frame[500 : 500 + height, 1080 : 1080 + width][mask] = (255, 255, 255)
        local = ZipLineMixin._detect_zip_line_icon_candidates(task, frame, screen_box(0.5, 0.4, 0.7, 0.65))
        self.assertEqual(len(local), 1)
        self.assertAlmostEqual(local[0]["x"], 1080, delta=1)
        full = ZipLineMixin._detect_zip_line_icon_candidates(task, frame)
        self.assertEqual(len(full), 2)
        self.assertEqual({round(candidate["x"]) for candidate in full}, {780, 1080})

    def test_incompatible_direction_does_not_send_cross_axis_motion(self):
        context = replace(self.context, capture_profile=replace(self.context.capture_profile, axes="x"))
        self.store.put(
            context.fingerprint,
            LearningRecord().successful(0, 0, target=replace(self.prior, search_direction="up"), now=1),
        )
        task = self.task()
        with initial_alignment(task, context) as learning:
            learning.replay()
            self.assertIsNone(learning.search_step(480, only_x=True))
        task.active_and_send_mouse_delta.assert_not_called()

    def test_direction_observation_obeys_axes_and_warm_samples_preserve_original_pose(self):
        context = replace(self.context, capture_profile=replace(self.context.capture_profile, axes="x"))
        task = self.task([box(960, 800)])
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False, only_x=True))
        prior = self.store.get(context.fingerprint).target
        self.assertEqual(prior.initial_position, (0.5, 800 / 1080))
        self.assertIsNone(prior.search_direction)


if __name__ == "__main__":
    unittest.main()
