"""Behavior contracts for source-preserving route prefix sharing."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.core.learning.models import CaptureProfile, ContextConfidence, LearningRecord, MotionStatistics, RouteContext
from src.core.learning.policy import deny_learning
from src.core.learning.routes import RouteLearning
from src.core.learning.store import LearningStore
from src.data.navigation_contexts import DELIVERY_DISPATCH_BOARD, DELIVERY_TRANSFER_BOARD, delivery_route_family


def route_context(**changes):
    context = RouteContext(
        "常沄",
        (64, 108, 80, 45),
        2,
        CaptureProfile(1920, 1080, "zipline-scroll-off"),
        18,
        ("武陵", "武陵城", "delivery_dispatch_board", "outbound", "delivery"),
    )
    return replace(context, **changes)


class TestRouteLearning(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = LearningStore(Path(self.directory.name) / "learning.json")
        self.learning = RouteLearning(self.store)

    def successes(self, context, dx=100, dy=20, count=3):
        for index in range(count):
            self.assertTrue(self.learning.record_success(context, dx, dy, now=index + 1))

    def test_private_priority_and_shared_prefix_need_no_target_detection(self):
        source = route_context()
        target = replace(source, config_key="彦宁", distances=(64, 108, 80, 72))
        self.successes(source)
        shared = self.learning.lookup(target)
        self.assertEqual((shared.action, shared.source, shared.samples), ((100, 20), "shared-prefix", 3))
        self.assertIsNone(shared.target)
        self.successes(target, dx=110)
        private = self.learning.lookup(target)
        self.assertEqual((private.action, private.source), ((110, 20), "private"))

    def test_forks_cannot_rejoin_by_later_matching_distance(self):
        source = route_context(segment_index=3)
        self.successes(source)
        for distances in ((64, 108, 80, 72), (64, 108, 96, 45)):
            self.assertIsNone(self.learning.lookup(replace(source, config_key="other", distances=distances)))

    def test_repeated_distances_use_prefix_and_segment(self):
        source = route_context(distances=(64, 64, 64), segment_index=0)
        self.successes(source)
        self.assertIsNone(self.learning.lookup(replace(source, segment_index=1)))

    def test_family_capture_camera_axes_and_movement_version_are_isolated(self):
        source = route_context()
        self.successes(source)
        for changes in (
            {"family": ()},
            {"family": ("武陵", "试验园区", "delivery_dispatch_board", "outbound", "delivery")},
            {"family": (*source.family[:3], "return", "delivery")},
            {"capture_profile": replace(source.capture_profile, width=2560)},
            {"capture_profile": replace(source.capture_profile, camera="zipline-scroll-on")},
            {"capture_profile": replace(source.capture_profile, axes="x")},
            {"capture_profile": replace(source.capture_profile, movement_version="v2")},
            {"start_yaw_bucket": 19},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.learning.lookup(replace(source, config_key="other", **changes)))

    def test_no_family_still_learns_private_and_never_guesses_from_distances(self):
        context = route_context(family=())
        self.successes(context)
        self.assertEqual(self.learning.lookup(context).action, (100, 20))
        self.assertIsNone(self.learning.lookup(replace(context, config_key="unknown-stage")))

    def test_shared_pool_merges_sources_with_correct_sample_weights(self):
        a, b = route_context(), route_context(config_key="彦宁")
        self.successes(a, dx=100, count=2)
        self.successes(b, dx=106, count=1)
        hint = self.learning.lookup(route_context(config_key="new"))
        self.assertEqual((hint.action, hint.samples), ((102, 20), 3))
        # Repeated lookups do not persist or re-merge an aggregate into itself.
        for _ in range(5):
            self.assertEqual(self.learning.lookup(route_context(config_key="new")), hint)
        self.assertEqual(len(self.store.snapshot()), 2)

    def test_shared_source_limit_selects_recent_sources_without_deleting_private(self):
        self.learning = RouteLearning(self.store, max_sources_per_prefix=1)
        self.successes(route_context(config_key="a"))
        self.successes(route_context(config_key="b"), dx=110)
        self.assertTrue(self.learning.record_success(route_context(config_key="b"), 110, 20, now=10))
        hint = self.learning.lookup(route_context(config_key="new"))
        self.assertEqual((hint.action, hint.samples), ((110, 20), 4))
        self.assertEqual(len(self.store.snapshot()), 2)

    def test_new_conflicting_source_is_private_even_during_warmup(self):
        a, b = route_context(), route_context(config_key="彦宁")
        self.successes(a)
        self.successes(b, dx=-100, dy=-20, count=1)
        self.assertIsNone(self.learning.lookup(b).action)
        fresh = RouteLearning(LearningStore(self.store.path))
        self.assertIsNone(fresh.lookup(b).action)
        self.assertIsNone(fresh.nearest_yaw(replace(b, start_yaw_bucket=0)))
        self.successes(b, dx=-100, dy=-20, count=2)
        self.assertEqual(self.learning.lookup(b).action, (-100, -20))
        self.assertEqual(self.learning.lookup(route_context(config_key="new")).action, (100, 20))

    def test_ambiguous_conflicting_sources_do_not_average_to_zero(self):
        for key, dx in (("a", 100), ("b", -100)):
            record = LearningRecord(motion=MotionStatistics(5, dx, 0, variance_known=False), success_count=5)
            self.assertTrue(self.store.put(route_context(config_key=key).fingerprint, record))
        self.assertIsNone(self.learning.lookup(route_context(config_key="new")))
        self.assertEqual(len(self.store.snapshot()), 2)

    def test_shared_rejection_survives_successes_until_private_is_ready(self):
        a, b = route_context(), route_context(config_key="彦宁")
        self.successes(a)
        self.assertTrue(self.learning.reject_shared(b, now=5))
        self.successes(b, count=1)
        self.assertIsNone(self.learning.lookup(b).action)
        self.assertIsNone(self.learning.nearest_yaw(replace(b, start_yaw_bucket=0)))
        self.successes(b, count=2)
        self.assertEqual(self.learning.lookup(b).source, "private")

    def test_shared_rejection_still_allows_a_verified_private_nearest_yaw(self):
        context = route_context()
        self.assertTrue(self.learning.reject_shared(context, now=1))
        self.successes(replace(context, start_yaw_bucket=20))
        candidate = self.learning.nearest_yaw(context)
        self.assertEqual((candidate.yaw, candidate.hint.source), (100.0, "private"))

    def test_high_variance_pool_and_unverified_legacy_never_replay(self):
        source = route_context()
        for motion in (MotionStatistics(5, 100, 20, 100000, 0), MotionStatistics(50, 100, 20, variance_known=False)):
            self.assertTrue(self.store.put(source.fingerprint, LearningRecord(motion=motion, success_count=50)))
            self.assertIsNone(self.learning.lookup(route_context(config_key="new")).action)

    def test_clean_sources_can_replay_without_inventing_legacy_variance(self):
        source = route_context()
        self.store.put(source.fingerprint, LearningRecord(motion=MotionStatistics(500, 100, 20, variance_known=False)))
        self.successes(route_context(config_key="clean"))
        hint = self.learning.lookup(route_context(config_key="new"))
        self.assertEqual((hint.samples, hint.action), (3, (100, 20)))

    def test_nearest_yaw_wraps_and_lookup_rechecks_actual_bucket(self):
        source = route_context(start_yaw_bucket=71)
        self.successes(source)
        target = replace(source, config_key="new", start_yaw_bucket=0)
        candidate = self.learning.nearest_yaw(target)
        self.assertEqual(candidate.yaw, 355.0)
        self.assertIsNone(self.learning.lookup(target))
        # A physical turn landing at 350 instead of requested 355 must not replay.
        self.assertIsNone(self.learning.lookup(replace(target, start_yaw_bucket=70)))
        self.assertEqual(self.learning.lookup(replace(target, start_yaw_bucket=71)).action, (100, 20))
        self.assertIsNone(self.learning.nearest_yaw(target, ContextConfidence.LOW))

    def test_context_confidence_bounds_and_axis_restrictions(self):
        context = route_context()
        self.successes(context)
        self.assertEqual(self.learning.lookup(context, ContextConfidence.MEDIUM).action, (50, 10))
        self.assertIsNone(self.learning.lookup(context, ContextConfidence.LOW).action)
        self.assertIsNone(self.learning.lookup(context, ContextConfidence.INVALID))
        self.assertIsNone(self.learning.lookup(replace(context, start_yaw_bucket=None)))
        self.assertFalse(
            self.learning.record_success(
                replace(context, capture_profile=replace(context.capture_profile, axes="x")), 1, 1, now=1
            )
        )
        self.successes(context, dx=100000, dy=0, count=100)
        self.assertIsNone(self.learning.lookup(context).action)

    def test_route_limit_evicts_complete_routes_without_touching_workflows(self):
        self.learning = RouteLearning(self.store, max_routes=2)
        workflow = '{"kind":"workflow-test"}'
        self.assertTrue(self.store.put(workflow, LearningRecord()))
        self.successes(route_context(config_key="a"))
        self.successes(route_context(config_key="a", segment_index=1))
        self.successes(route_context(config_key="b"))
        self.successes(route_context(config_key="c"))
        keys = self.store.snapshot()
        self.assertEqual(len(keys), 3)
        self.assertIn(workflow, keys)
        self.assertNotIn(route_context(config_key="a").fingerprint, keys)
        self.assertNotIn(route_context(config_key="a", segment_index=1).fingerprint, keys)

    def test_denied_learning_never_calls_store(self):
        context = route_context()
        with (
            patch.object(self.store, "snapshot", side_effect=AssertionError("lookup")),
            patch.object(self.store, "update_many", side_effect=AssertionError("write")),
            deny_learning("post_battle"),
        ):
            self.assertIsNone(self.learning.lookup(context))
            self.assertIsNone(self.learning.nearest_yaw(context))
            self.assertFalse(self.learning.record_success(context, 100, 20, now=1))
            self.assertFalse(self.learning.reject_shared(context, now=1))

    def test_business_family_requires_explicit_origin_and_canonical_destination(self):
        a = delivery_route_family("常沄", origin_anchor=DELIVERY_DISPATCH_BOARD)
        self.assertTrue(a)
        self.assertEqual(a, delivery_route_family("彦宁", origin_anchor=DELIVERY_DISPATCH_BOARD))
        self.assertNotEqual(a, delivery_route_family("阿禾", origin_anchor=DELIVERY_DISPATCH_BOARD))
        self.assertNotEqual(a, delivery_route_family("通向武陵城送货点", origin_anchor=DELIVERY_TRANSFER_BOARD))
        for name, origin in (
            ("常沄", "unknown"),
            ("unknown-stage", DELIVERY_DISPATCH_BOARD),
            ("通向武陵城送货点", DELIVERY_DISPATCH_BOARD),
        ):
            self.assertEqual(delivery_route_family(name, origin_anchor=origin), ())


if __name__ == "__main__":
    unittest.main()
