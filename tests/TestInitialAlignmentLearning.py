"""Exercise actual alignment loops, input timing and production zipline entry adapters."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.core.learning.models import ContextConfidence
from src.core.learning.policy import deny_learning
from src.core.learning.routes import RouteLearning
from src.core.learning.session import create_learning_session
from src.core.learning.store import LearningStore
from src.data.navigation_contexts import DELIVERY_DISPATCH_BOARD, DELIVERY_TRANSFER_BOARD
from src.tasks.mixin.delivery_learning import delivery_learning_kwargs
from src.tasks.mixin.navigation_learning import align_initial_target, initial_alignment
from src.tasks.mixin.route_learning import route_context_for
from src.tasks.mixin.zip_line_motion_cache import build_zip_line_route_fingerprint
from src.tasks.navigation.mixin.navigation_mixin import NavigationMixin
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineMixin
from tests import TestNavigationMovement as movement_tests
from tests import TestZipLineGoldGate as gold_tests
from tests.TestLearningCore import workflow_context


def box(x=960, y=540):
    return SimpleNamespace(x=x, y=y, width=0, height=0)


class TestInitialAlignmentLearning(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = LearningStore(Path(self.directory.name) / "learning.json")
        source = Path(self.directory.name) / "legacy.json"
        self.source = source
        patcher = patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", source)
        patcher.start()
        self.addCleanup(patcher.stop)

    def workflow_task(self, detections):
        task, _ = movement_tests.TestNavigationMovement().task(detections)
        task._context_learning_store = self.store
        task.align_ocr_or_find_target_to_center = lambda *a, **k: NavigationMixin.align_ocr_or_find_target_to_center(
            task, *a, **k
        )
        return task

    def seed(self, context, dx=100, dy=20):
        for now in range(3):
            self.store.update(
                context.fingerprint, lambda record, timestamp=now: record.successful(dx, dy, now=timestamp)
            )

    def zip_task(self, detections=None):
        task = gold_tests.TestZipLineGoldGate._alignment_stub()
        task._context_learning_store = self.store
        task._find_zip_line_template_target.side_effect = detections or [
            gold_tests.TestZipLineGoldGate._matched_icon(1400, 800),
            gold_tests.TestZipLineGoldGate._matched_icon(),
        ]
        task.active_and_send_mouse_delta = Mock(return_value=True)
        task._move_to_zip_line_target.side_effect = lambda target: task.active_and_send_mouse_delta(40, 20)
        task._align_zip_line_distance = lambda *a, **k: ZipLineMixin._align_zip_line_distance(task, *a, **k)
        task._align_and_activate_zip_line = lambda *a, **k: ZipLineMixin._align_and_activate_zip_line(task, *a, **k)
        task._read_zip_line_settled_arrow_angle = Mock(return_value=(90.0, 0.9))
        task._normalize_zip_line_segment_yaw = Mock(return_value=(90.0, True))
        task._ZIP_LINE_ARROW_MIN_SCORE = 0.60
        task._zip_line_motion_route_fingerprint = lambda *a, **k: ZipLineMixin._zip_line_motion_route_fingerprint(
            task, *a, **k
        )
        task._try_click_on_zip_line = Mock(return_value=(True, None))
        task.lang = SimpleNamespace(
            zip_line_mixin=SimpleNamespace(k_2f4f4a2f="move", k_0b1e4f35="leave", k_b0e3a2da="board")
        )
        task.ocr.return_value = [box()]
        task.send_key = Mock()
        task.click = Mock()
        task._normalize_values = ZipLineMixin._normalize_values
        task._value_for_index = ZipLineMixin._value_for_index
        task._zip_line_distance_matcher = ZipLineMixin._zip_line_distance_matcher
        task.zip_line_target_select_mode = lambda: "OCR"
        task._zip_line_stop_state = lambda: ZipLineMixin._zip_line_stop_state(task)
        return task

    def zip_context(self, task, *, yaw=90, config_key="route", distances=(108,), scroll=False, family=()):
        return route_context_for(
            task, build_zip_line_route_fingerprint(config_key, distances), 0, yaw, need_scroll=scroll, family=family
        )

    def run_zip(self, task, **kwargs):
        return ZipLineMixin.zip_line_list_go(task, [108], config_key="route", stay_on_zip_line=True, **kwargs)

    def test_grid_distance_tolerance_reaches_initial_learning_and_gate_retry(self):
        task = self.zip_task(
            [
                gold_tests.TestZipLineGoldGate._matched_icon(1400, 800),
                gold_tests.TestZipLineGoldGate._matched_icon(),
                gold_tests.TestZipLineGoldGate._matched_icon(),
            ]
        )
        task._try_click_on_zip_line.side_effect = [(False, "gate"), (True, None)]
        align = task._align_zip_line_distance
        task._align_zip_line_distance = Mock(side_effect=align)
        self.run_zip(task, distance_tolerance=7)
        calls = task._align_zip_line_distance.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual([call.kwargs["distance_tolerance"] for call in calls], [7, 7])
        self.assertIn("learning_session", calls[0].kwargs)
        self.assertNotIn("learning_session", calls[1].kwargs)
        self.assertEqual(self.store.get(self.zip_context(task).fingerprint).success_count, 1)

    def test_grid_distance_tolerance_survives_disabled_learning(self):
        task = self.zip_task()
        align = task._align_zip_line_distance
        task._align_zip_line_distance = Mock(side_effect=align)
        with deny_learning("post_battle"):
            self.run_zip(task, distance_tolerance=7)
        self.assertEqual(task._align_zip_line_distance.call_args.kwargs["distance_tolerance"], 7)
        self.assertNotIn("learning_session", task._align_zip_line_distance.call_args.kwargs)

    def test_workflow_replays_before_first_detection_then_commits_without_arrival(self):
        events = []
        context = workflow_context()
        self.seed(context)
        task = self.workflow_task([box()])
        sender = task.active_and_send_mouse_delta
        sender.side_effect = lambda *a, **k: events.append("input") or True
        task.find_feature.side_effect = lambda **k: events.append("detect") or box()
        self.assertEqual(align_initial_target(task, context, "hud", ocr=False), (960, 540))
        self.assertEqual(events, ["input", "detect"])
        self.assertEqual(self.store.get(context.fingerprint).success_count, 4)
        self.assertEqual(self.store.get(context.fingerprint).target.aligned_position, (0.5, 0.5))
        self.assertIs(task.active_and_send_mouse_delta, sender)
        before = self.store.snapshot()
        task.active_and_send_mouse_delta(999, 999)
        self.assertEqual(self.store.snapshot(), before)

    def test_workflow_cold_alignment_records_real_correction(self):
        task = self.workflow_task([box(1500, 800), box()])
        context = workflow_context()
        sender = task.active_and_send_mouse_delta
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False))
        record = self.store.get(context.fingerprint)
        self.assertEqual(record.motion.samples, 1)
        self.assertEqual((record.motion.mean_dx, record.motion.mean_dy), sender.call_args.args[:2])

    def test_missing_frame_and_random_search_cannot_save_a_sample(self):
        context = workflow_context()
        task = self.workflow_task([box()])
        task.next_frame.return_value = None
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False))
        self.assertIsNone(self.store.get(context.fingerprint))
        task = self.workflow_task([])
        detector = Mock(side_effect=[[], box()])
        self.assertTrue(align_initial_target(task, context, "hud", ocr=True, ocr_target_detector=detector))
        self.assertIsNone(self.store.get(context.fingerprint))

    def test_partial_or_rejected_replay_recovers_visually_without_changing_history(self):
        context = workflow_context()
        self.seed(context)
        before = self.store.get(context.fingerprint)
        for result in (False, OSError("partial input")):
            with self.subTest(result=result):
                task = self.workflow_task([box(1500, 800), box()])
                sender = task.active_and_send_mouse_delta
                sender.side_effect = [result, True]
                self.assertTrue(align_initial_target(task, context, "hud", ocr=False))
                self.assertEqual(self.store.get(context.fingerprint), before)
                self.assertIs(task.active_and_send_mouse_delta, sender)

    def test_timeout_after_prior_marks_failure_without_creating_success(self):
        context = workflow_context()
        self.seed(context)
        task = self.workflow_task([])
        result = align_initial_target(
            task,
            context,
            "hud",
            ocr=True,
            ocr_target_detector=lambda frame: [],
            allow_random_move=False,
            max_time=1,
            raise_if_fail=False,
        )
        self.assertFalse(result)
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.success_count, record.failure_count), (3, 1))

    def test_stop_restores_sender_and_never_penalizes_prior(self):
        context = workflow_context()
        self.seed(context)
        task = self.workflow_task([])
        sender = task.active_and_send_mouse_delta
        sender.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            align_initial_target(task, context, "hud", ocr=False)
        self.assertIs(task.active_and_send_mouse_delta, sender)
        self.assertEqual(self.store.get(context.fingerprint).failure_count, 0)

    def test_ended_budget_and_mismatched_axes_never_read_experience(self):
        task = self.workflow_task([box(), box()])
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertFalse(
                align_initial_target(task, workflow_context(), "hud", ocr=False, max_time=0, raise_if_fail=False)
            )
            self.assertTrue(align_initial_target(task, workflow_context(), "hud", ocr=False, only_x=True))

    def test_battle_epoch_and_low_confidence_cannot_commit(self):
        context = workflow_context()
        task = self.workflow_task([box()])

        def detect(**kwargs):
            with deny_learning("battle"):
                pass
            return box()

        task.find_feature.side_effect = detect
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False))
        self.assertIsNone(self.store.get(context.fingerprint))
        task = self.workflow_task([box()])
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False, confidence=ContextConfidence.LOW))
        self.assertIsNone(self.store.get(context.fingerprint))

    def test_zipline_saves_before_activation_and_riding_failure_does_not_change_it(self):
        task = self.zip_task()
        context = self.zip_context(task)

        def activate(*args, **kwargs):
            self.assertEqual(self.store.get(context.fingerprint).motion.mean_dx, 40)
            return True, None

        task._try_click_on_zip_line.side_effect = activate
        task.ocr.side_effect = RuntimeError("later arrival failure")
        with self.assertRaisesRegex(RuntimeError, "later arrival"):
            self.run_zip(task)
        self.assertEqual(
            (self.store.get(context.fingerprint).motion.mean_dx, self.store.get(context.fingerprint).motion.mean_dy),
            (40, 20),
        )

    def test_gate_retry_never_replays_or_adds_to_completed_initial_sample(self):
        task = self.zip_task(
            [gold_tests.TestZipLineGoldGate._matched_icon(1400, 800), gold_tests.TestZipLineGoldGate._matched_icon()]
            * 2
        )
        context = self.zip_context(task)
        self.seed(context)
        task._try_click_on_zip_line.side_effect = [(False, "gate"), (True, None)]
        self.run_zip(task)
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.motion.mean_dx, record.motion.mean_dy), (4, 110, 25))
        self.assertEqual(task._move_to_zip_line_target.call_count, 2)
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 3)

    def test_nearest_yaw_rechecks_actual_bucket_and_learns_total_at_original_yaw(self):
        task = self.zip_task()
        original = self.zip_context(task, yaw=30)
        borrowed = self.zip_context(task, yaw=90)
        self.seed(borrowed)
        task._read_zip_line_settled_arrow_angle.return_value = (30, 0.9)

        def turn(yaw):
            task.active_and_send_mouse_delta(-300, 0)
            return 90, True

        task._normalize_zip_line_segment_yaw.side_effect = turn
        self.run_zip(task)
        record = self.store.get(original.fingerprint)
        self.assertEqual((record.motion.mean_dx, record.motion.mean_dy), (-160, 40))
        self.assertEqual(self.store.get(borrowed.fingerprint).motion.samples, 3)

    def test_nearest_requested_yaw_alone_does_not_authorize_replay(self):
        task = self.zip_task()
        self.seed(self.zip_context(task, yaw=90))
        task._read_zip_line_settled_arrow_angle.return_value = (30, 0.9)
        task._normalize_zip_line_segment_yaw.return_value = (95, True)
        self.run_zip(task)
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)
        self.assertEqual(self.store.get(self.zip_context(task, yaw=30).fingerprint).motion.mean_dx, 40)

    def test_failed_normalization_or_unsettled_replay_cannot_strengthen(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                task = self.zip_task()
                original = self.zip_context(task, yaw=30)
                borrowed = self.zip_context(task, yaw=90)
                self.seed(borrowed)
                task._read_zip_line_settled_arrow_angle.return_value = (30, 0.9)
                task._normalize_zip_line_segment_yaw.return_value = (90, ready)
                task._wait_zip_line_prefeed_settled.return_value = False
                self.run_zip(task)
                self.assertIsNone(self.store.get(original.fingerprint))

    def test_unknown_or_unreliable_yaw_uses_visual_without_new_store_access(self):
        task = self.zip_task()
        task._read_zip_line_settled_arrow_angle.return_value = (30, 0.3)
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.run_zip(task)
        self.assertEqual(self.store.snapshot(), {})

    def test_zoom_is_normalized_once_before_recording_not_once_per_correction(self):
        task = self.zip_task()
        self.run_zip(task, need_scroll=True)
        task.do_scroll.assert_called_once_with(1, 400)
        context = self.zip_context(task, scroll=True)
        self.assertEqual(self.store.get(context.fingerprint).motion.mean_dx, 40)
        self.assertIsNone(self.store.get(self.zip_context(task).fingerprint))

    def test_production_route_entry_migrates_legacy_privately_without_replaying_it(self):
        task = self.zip_task()
        inner = build_zip_line_route_fingerprint("route", [108])
        key = json.dumps([inner, [1920, 1080], 18])
        self.source.write_text(
            json.dumps(
                {"version": 4, "routes": {key: {"segments": {"0": {"samples": 100, "mean_dx": 900, "mean_dy": 10}}}}}
            ),
            encoding="utf-8",
        )
        before = self.source.read_bytes()
        self.run_zip(task)
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.store.get(self.zip_context(task).fingerprint).motion.samples, 1)
        self.assertEqual(len(self.store.snapshot()), 3)

    def test_route_session_only_borrows_yaw_not_another_route_or_capture(self):
        task = self.zip_task()
        context = self.zip_context(task)
        session = create_learning_session(context, self.store, started_at=0)
        with self.assertRaises(ValueError):
            session.take_action_prior(replace(context, config_key="other"))

    def test_shared_replay_failure_is_rejected_even_during_private_warmup(self):
        task = self.zip_task()
        family = ("area", "place", "board", "out", "zipline")
        context = self.zip_context(task, family=family)
        source = replace(context, config_key="another")
        self.seed(source)
        session = create_learning_session(context, self.store, started_at=0)
        self.assertEqual(session.take_action_prior(), (100, 20))
        self.assertTrue(session.fail(now=1))
        self.assertIsNone(RouteLearning(self.store).lookup(context).action)

    def test_delivery_context_needs_verified_area_location_and_keeps_target_route_identity(self):
        task = SimpleNamespace(
            delivery_area="武陵",
            _accepted_delivery_location="武陵城",
            width=1920,
            height=1080,
            zip_line_scroll_enabled=lambda: False,
            get_zip_line_config_value=Mock(return_value="64,108"),
        )
        a = delivery_learning_kwargs(task, "常沄", DELIVERY_DISPATCH_BOARD, phase="dropoff_initial")
        b = delivery_learning_kwargs(task, "彦宁", DELIVERY_DISPATCH_BOARD, phase="dropoff_initial")
        self.assertEqual(a["route_family"], b["route_family"])
        self.assertNotEqual(a["target_learning_context"].fingerprint, b["target_learning_context"].fingerprint)
        pickup = delivery_learning_kwargs(
            task, "通向武陵城送货点", DELIVERY_TRANSFER_BOARD, phase="pickup_initial", distances=(36,)
        )
        self.assertEqual(pickup["target_learning_context"].target_id, "delivery-pickup:武陵城")
        self.assertEqual(pickup["target_learning_context"].entry_route_signature, (36,))
        task._accepted_delivery_location = None
        self.assertEqual(delivery_learning_kwargs(task, "常沄", DELIVERY_DISPATCH_BOARD, phase="dropoff_initial"), {})
        task._accepted_delivery_location = "试验园区"
        self.assertEqual(delivery_learning_kwargs(task, "常沄", DELIVERY_DISPATCH_BOARD), {})

    def test_actual_delivery_terminal_first_alignment_replays_before_blue_detection(self):
        task = self.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        context = replace(workflow_context(), entry_route_signature=(108,), start_yaw_bucket=18)
        self.seed(context)
        events = []
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("input") or True
        task.find_feature = Mock(side_effect=lambda **kwargs: events.append("blue") or box())
        task.ensure_main = Mock()
        task.box = SimpleNamespace(bottom_right=None)
        task.wait_ocr = Mock(side_effect=[[box()], []])
        task.strafe_search = lambda check, **kwargs: check()
        task.press_key = Mock()
        task.click_with_alt = Mock()
        ZipLineMixin.zip_line_list_go(
            task, [108], config_key="route", target=("blue", "feature"), need_v=True, target_learning_context=context
        )
        self.assertEqual(events, ["input", "blue"])
        self.assertEqual(self.store.get(context.fingerprint).motion.samples, 4)
        before = self.store.get(context.fingerprint)
        # Subsequent correction has no session, even for exactly the same blue marker.
        task.align_ocr_or_find_target_to_center("blue", ocr=False, only_x=True)
        self.assertEqual(self.store.get(context.fingerprint), before)

    def test_reboard_after_wasd_recovery_starts_learning_at_the_reboarded_entry(self):
        task = self.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        context = replace(workflow_context(), entry_route_signature=(108,), start_yaw_bucket=18)
        self.seed(context)
        events = []
        task.find_feature = Mock(side_effect=lambda **kwargs: events.append("blue") or box())
        task.active_and_send_mouse_delta.side_effect = lambda *a, **k: events.append("replay") or True
        task.ensure_main = Mock()
        task.box = SimpleNamespace(bottom_right=None)
        task.wait_ocr = Mock(side_effect=[[], [box()], []])

        def search(check, **kwargs):
            check()
            events.append("walk")
            task.send_key("s")
            return check()

        task.strafe_search = search
        task.press_key = Mock(side_effect=lambda *a, **k: events.append("v"))
        task.click_with_alt = Mock(side_effect=lambda *a, **k: events.append("board"))
        ZipLineMixin.zip_line_list_go(
            task, [108], config_key="route", target=("blue", "feature"), need_v=True, target_learning_context=context
        )
        self.assertEqual(events, ["walk", "v", "board", "replay", "blue"])
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.motion.mean_dx, record.motion.mean_dy), (4, 100, 20))
        self.assertEqual(task.active_and_send_mouse_delta.call_count, 1)

    def test_terminal_without_reboarding_cannot_borrow_a_reboarded_entry_prior(self):
        task = self.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        context = replace(workflow_context(), entry_route_signature=(108,), start_yaw_bucket=18)
        self.seed(context)
        before = self.store.get(context.fingerprint)
        task.find_feature = Mock(return_value=box())
        task.ensure_main = Mock()
        task.wait_ocr = Mock(return_value=[])
        ZipLineMixin.zip_line_list_go(
            task,
            [108],
            config_key="route",
            target=("blue", "feature"),
            need_v=False,
            target_learning_context=context,
        )
        task.active_and_send_mouse_delta.assert_not_called()
        self.assertEqual(self.store.get(context.fingerprint), before)

    def test_initial_scope_disturbances_invalidate_and_restore_all_methods(self):
        context = workflow_context()
        for name in ("move_keys", "do_scroll", "press_key", "rotate_search", "click"):
            with self.subTest(name=name):
                task = self.workflow_task([])
                original = Mock()
                setattr(task, name, original)
                with initial_alignment(task, context) as learning:
                    getattr(task, name)("anything")
                    with patch.object(self.store, "get", side_effect=AssertionError("disturbed prior lookup")):
                        self.assertIsNone(learning.replay())
                    learning.frame_acquired(object())
                    self.assertFalse(learning.confirm_alignment((960, 540)))
                self.assertIs(getattr(task, name), original)
                self.assertNotIn("_initial_alignment_scope", vars(task))
                self.assertIsNone(self.store.get(context.fingerprint))

    def test_nested_initial_alignment_cannot_commit_outer_or_create_inner_store(self):
        task = self.workflow_task([])
        context = workflow_context()
        with initial_alignment(task, context) as outer:
            with initial_alignment(task, replace(context, target_id="other")) as inner:
                self.assertIsNone(inner)
            outer.frame_acquired(object())
            self.assertFalse(outer.confirm_alignment((960, 540)))
        self.assertEqual(self.store.snapshot(), {})

    def test_semantic_post_battle_and_invalid_context_do_not_get_store(self):
        task = self.workflow_task([box(), box()])
        with patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")):
            self.assertTrue(align_initial_target(task, workflow_context(phase="post_battle"), "hud", ocr=False))
            self.assertTrue(
                align_initial_target(task, workflow_context(), "hud", ocr=False, confidence=ContextConfidence.INVALID)
            )

    def test_store_write_failure_does_not_block_real_time_alignment(self):
        task = self.workflow_task([box(1500, 800), box()])
        sender = task.active_and_send_mouse_delta
        with patch("src.core.learning.store.os.replace", side_effect=PermissionError("locked")):
            self.assertTrue(align_initial_target(task, workflow_context(), "hud", ocr=False))
        self.assertEqual(self.store.snapshot(), {})
        self.assertIs(task.active_and_send_mouse_delta, sender)

    def test_x_only_verification_preserves_observed_y_in_optional_target_prior(self):
        task = self.workflow_task([box(960, 800)])
        context = workflow_context()
        context = replace(context, capture_profile=replace(context.capture_profile, axes="x"))
        self.assertTrue(align_initial_target(task, context, "hud", ocr=False, only_x=True))
        self.assertEqual(self.store.get(context.fingerprint).target.aligned_position, (0.5, 800 / 1080))

    def test_capture_change_and_expired_epoch_block_new_samples_and_nearest_lookup(self):
        task = self.workflow_task([])
        with initial_alignment(task, workflow_context()) as learning:
            task.width = 2560
            learning.frame_acquired(object())
            self.assertFalse(learning.confirm_alignment((1280, 540)))
        self.assertEqual(self.store.snapshot(), {})
        task = self.zip_task()
        session = create_learning_session(self.zip_context(task), self.store, started_at=0)
        with deny_learning("battle"):
            pass
        with patch.object(self.store, "snapshot", side_effect=AssertionError("lookup")):
            self.assertIsNone(session.nearest_yaw())

    def test_production_prefix_sharing_replays_another_destination_before_detection(self):
        task = self.zip_task()
        family = ("area", "place", "board", "out", "zipline")
        context = self.zip_context(task, family=family)
        source = replace(context, config_key="another-destination", distances=(108, 60))
        self.seed(source)
        self.run_zip(task, route_family=family)
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.mean_dx, record.motion.mean_dy), (140, 40))
        self.assertEqual(self.store.get(source.fingerprint).motion.samples, 3)

    def test_verified_zero_action_uses_visual_confirmation_without_borrowing_other_yaw(self):
        task = self.zip_task([gold_tests.TestZipLineGoldGate._matched_icon()])
        self.seed(self.zip_context(task), dx=0, dy=0)
        self.seed(self.zip_context(task, yaw=30))
        self.run_zip(task)
        task._normalize_zip_line_segment_yaw.assert_not_called()
        task.active_and_send_mouse_delta.assert_not_called()
        self.assertEqual(self.store.get(self.zip_context(task).fingerprint).motion.samples, 4)

    def test_persisted_recency_uses_wall_time_while_evidence_uses_active_clock(self):
        task = self.workflow_task([box()])
        context = workflow_context()
        with patch("src.tasks.mixin.navigation_learning.time.time", return_value=1234567):
            self.assertTrue(align_initial_target(task, context, "hud", ocr=False))
        self.assertEqual(self.store.get(context.fingerprint).updated_at, 1234567)

    def test_shared_replay_that_needs_random_recovery_is_rejected_before_search(self):
        task = self.zip_task([None, None, None, gold_tests.TestZipLineGoldGate._matched_icon()])
        family = ("area", "place", "board", "out", "zipline")
        context = self.zip_context(task, family=family)
        self.seed(replace(context, config_key="source"))
        task.ocr.side_effect = [[], [], [box()]]
        self.run_zip(task, route_family=family)
        record = self.store.get(context.fingerprint)
        self.assertEqual((record.motion.samples, record.failure_count), (0, 1))
        self.assertIsNone(RouteLearning(self.store).lookup(context).action)


if __name__ == "__main__":
    unittest.main()
